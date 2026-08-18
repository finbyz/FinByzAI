from __future__ import annotations

from typing import Any
from zoneinfo import available_timezones

import frappe
from frappe import _
from frappe.utils import cint

from . import authoring, bulk, engine, events, external, observability, registry
from .configuration import (
	automation_enabled,
	external_actions_enabled,
	workflow_runtime_allowed,
)
from .errors import AutomationError, AutomationPermissionError
from .schema import parse_object, validate_graph


def _object(value: Any, label: str = "payload") -> dict:
	if value in (None, ""):
		return {}
	if isinstance(value, str):
		value = frappe.parse_json(value)
	if not isinstance(value, dict):
		raise AutomationError(_("{0} must be a JSON object.").format(label.title()))
	return value


def _envelope(value: Any = None, **fallback) -> dict:
	data = _object(value, "mutation envelope")
	if not data:
		data = {key: item for key, item in fallback.items() if item is not None}
	payload = _object(data.get("payload"), "payload")
	for key, item in fallback.items():
		if item is not None and key not in data:
			data[key] = item
	data["payload"] = payload
	return data


@frappe.whitelist()
def get_doctypes(
	search: str | None = None,
	start: int = 0,
	page_length: int = 50,
	permission_type: str = "read",
	workflow_id: str | None = None,
):
	registry.require_builder()
	execution_user = None
	if workflow_id:
		workflow = frappe.get_doc("Automation Workflow", workflow_id)
		workflow.check_permission("read")
		execution_user = workflow.execution_user
	permission_type = str(permission_type or "read").lower()
	if permission_type not in registry.DOCTYPE_PERMISSION_TYPES:
		access = registry.doctype_eligibility("", permission_type=permission_type)
		return {"rows": [], "has_more": False, **access}
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), 100)
	page = registry.eligible_doctype_page(
		permission_type=permission_type,
		user=execution_user,
		search=search,
		start=start,
		page_length=page_length,
	)
	return {
		**page,
		"permission_type": permission_type,
	}


@frappe.whitelist()
def get_fields(doctype: str, permission_type: str = "read", workflow_id: str | None = None):
	registry.require_builder()
	execution_user = None
	if workflow_id:
		workflow = frappe.get_doc("Automation Workflow", workflow_id)
		workflow.check_permission("read")
		execution_user = workflow.execution_user
	return registry.field_catalog_result(doctype, permission_type=permission_type, user=execution_user)


@frappe.whitelist()
def get_node_types():
	registry.require_builder()
	return {"node_types": registry.node_catalog()}


@frappe.whitelist()
def get_integration_secrets(search: str | None = None):
	registry.require_publisher()
	filters = {"enabled": 1}
	if search:
		filters["title"] = ["like", f"%{str(search).strip()}%"]
	return {
		"rows": frappe.get_list(
			"Automation Integration Secret",
			filters=filters,
			fields=["name", "title", "auth_type", "allowed_hosts", "requests_per_minute"],
			order_by="title asc",
			ignore_permissions=True,
			limit=50,
		)
	}


@frappe.whitelist()
def get_timezones(search: str | None = None):
	registry.require_operator()
	needle = str(search or "").strip().lower()
	rows = [name for name in sorted(available_timezones()) if not needle or needle in name.lower()]
	return {"rows": [{"value": name, "label": name} for name in rows[:50]]}


@frappe.whitelist()
def list_workflows(
	start: int = 0,
	page_length: int = 50,
	status: str | None = None,
	search: str | None = None,
	primary_doctype: str | None = None,
	exclude_workflow: str | None = None,
):
	registry.require_viewer()
	return authoring.list_workflow_records(start, page_length, status, search, primary_doctype, exclude_workflow)


@frappe.whitelist()
def list_templates(search: str | None = None, start: int = 0, page_length: int = 24):
	registry.require_builder()
	filters = {}
	if search:
		filters["title"] = ["like", f"%{search}%"]

	limit = min(max(cint(page_length), 1), 100)
	rows = frappe.get_list(
		"Automation Workflow Template",
		filters=filters,
		fields=["name", "title", "category", "description", "primary_doctype", "preview_image"],
		order_by="category asc, title asc",
		start=max(cint(start), 0),
		limit=limit + 1,
	)
	return {"rows": rows[:limit], "has_more": len(rows) > limit}


@frappe.whitelist(methods=["POST"])
def create_workflow_from_template(envelope: str):
	registry.require_builder()
	data = _envelope(envelope)
	payload = data["payload"]
	template_name = payload.get("id")
	if not template_name:
		raise AutomationError("Template ID is required.")

	from .template import load_template
	template, values = load_template(template_name)
	result = authoring.create_workflow_record(
		title=f"Copy of {template.title}"[:140],
		primary_doctype=template.primary_doctype,
		description=template.description or "",
		idempotency_key=data.get("idempotency_key"),
		operation="template",
		source=template.name,
	)
	if result.get("deduplicated"):
		return result
	saved = authoring.save_workflow_draft(
		result["workflow"], result["draft_revision"], values["graph"], values["settings"], "template"
	)
	return {**result, **saved}


@frappe.whitelist()
def export_template(template_name: str):
	registry.require_publisher()
	from .template import export_template as do_export
	return do_export(template_name)


@frappe.whitelist(methods=["POST"])
def import_template(json_data: str):
	registry.require_publisher()
	from .template import import_template as do_import
	return do_import(json_data)


@frappe.whitelist()
def get_runtime_health():
	registry.require_viewer()
	return events.runtime_health()


@frappe.whitelist()
def get_operations():
	registry.require_operator()
	return events.operation_snapshot()


@frappe.whitelist()
def list_outbox(status: str | None = None, search: str | None = None, start: int = 0, page_length: int = 50):
	registry.require_operator()
	return events.list_outbox_events(status=status, search=search, start=start, page_length=page_length)


@frappe.whitelist(methods=["POST"])
def bulk_retry_outbox(event_ids=None):
	registry.require_operator()
	values = frappe.parse_json(event_ids) if isinstance(event_ids, str) else event_ids
	return events.bulk_retry_outbox_events(values if isinstance(values, list) else [])


@frappe.whitelist(methods=["POST"])
def retry_outbox_event(event_id: str):
	registry.require_operator()
	return events.retry_outbox_event(event_id)


@frappe.whitelist(methods=["POST"])
def discard_outbox_event(event_id: str):
	registry.require_operator()
	return events.discard_outbox_event(event_id)


@frappe.whitelist(methods=["POST"])
def create_workflow(envelope=None, title=None, primary_doctype=None, description=None, execution_user=None, trigger_type=None):
	registry.require_builder()
	data = _envelope(
		envelope,
		title=title,
		primary_doctype=primary_doctype,
		description=description,
		execution_user=execution_user,
		trigger_type=trigger_type,
	)
	payload = data["payload"]
	return authoring.create_workflow_record(
		payload.get("title") or data.get("title"),
		payload.get("primary_doctype") or data.get("primary_doctype"),
		payload.get("description") or data.get("description") or "",
		payload.get("execution_user") or data.get("execution_user"),
		payload.get("trigger_type") or data.get("trigger_type") or "trigger.manual",
		idempotency_key=data.get("idempotency_key"),
	)


@frappe.whitelist(methods=["POST"])
def delete_workflow(envelope=None, workflow_id=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id)
	return authoring.delete_workflow_record(data.get("workflow_id"))


@frappe.whitelist()
def get_draft(workflow_id: str):
	registry.require_builder()
	return authoring.get_workflow_draft(workflow_id)


@frappe.whitelist(methods=["POST"])
def save_draft(envelope=None, workflow_id=None, draft_revision=None, graph=None, settings=None, client_id=None):
	registry.require_builder()
	data = _envelope(
		envelope,
		workflow_id=workflow_id,
		draft_revision=draft_revision,
		graph=graph,
		settings=settings,
		client_id=client_id,
	)
	payload = data["payload"]
	return authoring.save_workflow_draft(
		data.get("workflow_id"),
		cint(data.get("draft_revision")),
		payload.get("graph", data.get("graph")),
		payload.get("settings", data.get("settings")),
		payload.get("client_id", data.get("client_id")),
	)


@frappe.whitelist(methods=["POST"])
def validate_draft(workflow_id: str, publish: int = 0, draft_revision: int | None = None):
	registry.require_builder()
	return authoring.validate_workflow_draft(
		workflow_id,
		publish=bool(cint(publish)),
		draft_revision=cint(draft_revision) if draft_revision is not None else None,
	)


@frappe.whitelist(methods=["POST"])
def simulate(envelope=None, workflow_id=None, record_name=None, graph=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id, record_name=record_name, graph=graph)
	payload = data["payload"]
	workflow = frappe.get_doc("Automation Workflow", data.get("workflow_id"))
	workflow.check_permission("read")
	access = registry.doctype_eligibility(workflow.primary_doctype)
	if not access["available"]:
		raise AutomationPermissionError(access["explanation"])
	if payload.get("graph") is not None or data.get("graph") is not None:
		validation = validate_graph(payload.get("graph", data.get("graph")), primary_doctype=workflow.primary_doctype)
		simulation_graph = validation["graph"]
	else:
		simulation_graph = authoring.get_workflow_draft(workflow.name)["draft"]["graph"]
		validation = validate_graph(simulation_graph, primary_doctype=workflow.primary_doctype)
	if not validation["valid"]:
		return {"valid": False, "issues": validation["issues"], "path": [], "mutated": False}
	issues = authoring.validate_bindings(simulation_graph, workflow.execution_user)
	if issues:
		return {"valid": False, "issues": issues, "path": [], "mutated": False}
	record = frappe.get_doc(workflow.primary_doctype, payload.get("record_name") or data.get("record_name"))
	record.check_permission("read")
	if not frappe.has_permission(workflow.primary_doctype, ptype="read", doc=record, user=workflow.execution_user):
		raise AutomationPermissionError(_("Workflow execution user cannot read this record."))
	return {"valid": True, "issues": [], **engine.simulate_graph(simulation_graph, record, execution_user=workflow.execution_user)}


@frappe.whitelist(methods=["POST"])
def test_node(envelope=None, workflow_id=None, record_name=None, node_id=None, graph=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id, record_name=record_name, node_id=node_id, graph=graph)
	payload = data["payload"]
	workflow = frappe.get_doc("Automation Workflow", data.get("workflow_id"))
	workflow.check_permission("read")
	record = frappe.get_doc(workflow.primary_doctype, payload.get("record_name") or data.get("record_name"))
	record.check_permission("read")
	if not frappe.has_permission(workflow.primary_doctype, ptype="read", doc=record, user=workflow.execution_user):
		raise AutomationPermissionError(_("Workflow execution user cannot read this record."))
	graph_value = payload.get("graph", data.get("graph"))
	if graph_value is None:
		graph_value = authoring.get_workflow_draft(workflow.name)["draft"]["graph"]
	validation = validate_graph(graph_value, primary_doctype=workflow.primary_doctype)
	if not validation["valid"]:
		return {"valid": False, "issues": validation["issues"], "mutated": False}
	binding_issues = authoring.validate_bindings(validation["graph"], workflow.execution_user, workflow.name)
	if binding_issues:
		return {"valid": False, "issues": binding_issues, "mutated": False}
	selected = payload.get("node_id") or data.get("node_id")
	if selected not in {node.get("id") for node in validation["graph"].get("nodes") or [] if isinstance(node, dict)}:
		return {
			"valid": False,
			"issues": [{"severity": "error", "code": "UNKNOWN_TEST_NODE", "message": _("The selected step no longer exists."), "node_id": selected}],
			"mutated": False,
		}
	result = engine.simulate_graph(validation["graph"], record, execution_user=workflow.execution_user)
	selected_result = next((entry for entry in result["path"] if entry["node_id"] == selected), None)
	if not selected_result:
		return {
			"valid": False,
			"issues": [{"severity": "error", "code": "NODE_NOT_REACHED", "message": _("This record does not reach the selected step on the evaluated branch."), "node_id": selected}],
			"mutated": False,
		}
	return {"valid": True, "issues": [], "node": selected_result, "mutated": False}


@frappe.whitelist(methods=["POST"])
def publish(envelope=None, workflow_id=None, draft_revision=None, activate=1, reenrollment=None):
	registry.require_publisher()
	data = _envelope(envelope, workflow_id=workflow_id, draft_revision=draft_revision, activate=activate, reenrollment=reenrollment)
	payload = data["payload"]
	return authoring.publish_workflow(
		data.get("workflow_id"),
		cint(data.get("draft_revision")),
		activate=bool(cint(payload.get("activate", data.get("activate", 1)))),
		reenrollment=payload.get("reenrollment", data.get("reenrollment")),
	)


@frappe.whitelist()
def get_versions(workflow_id: str):
	registry.require_viewer()
	return {"rows": authoring.list_versions(workflow_id)}


@frappe.whitelist(methods=["POST"])
def clone_workflow(envelope=None, workflow_id=None, title=None, version_id=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id, title=title, version_id=version_id)
	payload = data["payload"]
	return authoring.clone_workflow_record(
		data.get("workflow_id"), payload.get("title") or data.get("title"), payload.get("version_id") or data.get("version_id"), data.get("idempotency_key")
	)


@frappe.whitelist()
def compare_versions(workflow_id: str, left_version: str, right_version: str | None = None):
	registry.require_viewer()
	return authoring.compare_versions(workflow_id, left_version, right_version)


@frappe.whitelist()
def list_suppressions(workflow_id: str):
	registry.require_viewer()
	return {"rows": authoring.list_suppression_rules(workflow_id)}


@frappe.whitelist(methods=["POST"])
def save_suppression(envelope=None, workflow_id=None, rule=None):
	registry.require_publisher()
	data = _envelope(envelope, workflow_id=workflow_id, rule=rule)
	payload = data["payload"]
	return authoring.save_suppression_rule(data.get("workflow_id"), payload.get("rule", data.get("rule")))


@frappe.whitelist(methods=["POST"])
def delete_suppression(workflow_id: str, rule_id: str):
	registry.require_publisher()
	return authoring.delete_suppression_rule(workflow_id, rule_id)


@frappe.whitelist(methods=["POST"])
def restore_version(envelope=None, workflow_id=None, version_id=None, draft_revision=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id, version_id=version_id, draft_revision=draft_revision)
	payload = data["payload"]
	return authoring.restore_version_to_draft(
		data.get("workflow_id"),
		payload.get("version_id") or data.get("version_id"),
		cint(data.get("draft_revision")),
	)


@frappe.whitelist()
def runtime_preflight(workflow_id: str | None = None):
	registry.require_operator()
	issues = []
	workflow = None
	draft_graph = {}
	if not automation_enabled():
		issues.append({"code": "GLOBAL_DISABLED", "message": _("Automation Settings is disabled.")})
	if workflow_id:
		workflow = frappe.get_doc("Automation Workflow", workflow_id)
		workflow.check_permission("read")
		if workflow.active_version:
			try:
				active_validation = authoring.validate_published_version(workflow.name, workflow.active_version)
			except (AutomationError, frappe.DoesNotExistError) as exc:
				issues.append(
					{
						"code": "ACTIVE_VERSION_UNAVAILABLE",
						"message": _("The active published version cannot be validated: {0}").format(exc),
					}
				)
			else:
				if not active_validation["valid"]:
					codes = ", ".join(dict.fromkeys(issue["code"] for issue in active_validation["issues"][:5]))
					issues.append(
						{
							"code": "ACTIVE_VERSION_INVALID",
							"message": _("The active published version fails current safety validation: {0}.").format(codes),
						}
					)
		if not frappe.db.get_value("User", workflow.execution_user, "enabled"):
			issues.append({"code": "EXECUTION_USER_DISABLED", "message": _("Execution user is disabled.")})
		try:
			draft_validation = authoring.validate_workflow_draft(workflow.name, publish=True)
			draft_graph = frappe.parse_json(
				frappe.db.get_value("Automation Workflow Draft", {"workflow": workflow.name}, "graph_json") or "{}"
			)
		except (AutomationError, frappe.DoesNotExistError) as exc:
			issues.append({"code": "DRAFT_UNAVAILABLE", "message": str(exc)})
		else:
			if not draft_validation["valid"]:
				codes = ", ".join(dict.fromkeys(issue["code"] for issue in draft_validation["issues"][:5]))
				issues.append(
					{
						"code": "DRAFT_INVALID",
						"message": _("The saved draft is not publish-ready: {0}.").format(codes),
					}
				)
	try:
		from frappe.utils.background_jobs import get_workers

		workers = len(get_workers())
	except Exception:
		workers = 0
	if not workers:
		issues.append({"code": "NO_WORKERS", "message": _("No background workers are available.")})
	health = events.runtime_health(workflow_id=workflow.name if workflow else None)
	if not health["healthy"]:
		issues.append(
			{
				"code": "RUNTIME_UNHEALTHY",
				"message": _("Runtime health requires attention: {0}.").format(", ".join(health["reasons"])),
			}
		)
	transport_readiness = external.transport_readiness()
	external_node_types = {
		str(node.get("type"))
		for node in (draft_graph.get("nodes") or [])
		if isinstance(node, dict) and str(node.get("type")) in {"action.send_email", "action.send_sms", "action.webhook"}
	}
	transport_by_node = {
		"action.send_email": "email",
		"action.send_sms": "sms",
		"action.webhook": "webhook",
	}
	if external_node_types and not external_actions_enabled():
		issues.append(
			{
				"code": "EXTERNAL_ACTIONS_DISABLED",
				"message": _("This draft uses external actions, but their independent kill switch is disabled."),
			}
		)
	if external_actions_enabled():
		for node_type in sorted(external_node_types):
			transport = transport_by_node[node_type]
			if not transport_readiness[transport]["configured"]:
				issues.append(
					{
						"code": f"{transport.upper()}_TRANSPORT_UNCONFIGURED",
						"message": transport_readiness[transport]["message"],
					}
				)
	return {
		"ready": not issues,
		"issues": issues,
		"settings": {
			"enabled": automation_enabled(),
			"external_actions_enabled": external_actions_enabled(),
		},
		"workers": workers,
		"health": health,
		"transports": transport_readiness,
	}


@frappe.whitelist(methods=["POST"])
def set_state(envelope=None, workflow_id=None, status=None):
	registry.require_operator()
	data = _envelope(envelope, workflow_id=workflow_id, status=status)
	payload = data["payload"]
	result = authoring.change_workflow_state(data.get("workflow_id"), payload.get("status") or data.get("status"))
	if result["status"] == "ACTIVE":
		result["released_tokens"] = engine.resume_held_tokens(result["workflow_id"])
		engine.release_due_timers()
	return result


@frappe.whitelist(methods=["POST"])
def enroll_manual(envelope=None, workflow_id=None, record_name=None, idempotency_key=None):
	registry.require_operator()
	data = _envelope(
		envelope,
		workflow_id=workflow_id,
		record_name=record_name,
		idempotency_key=idempotency_key,
	)
	payload = data["payload"]
	workflow = frappe.get_doc("Automation Workflow", data.get("workflow_id"))
	workflow.check_permission("read")
	if engine.published_trigger_type(workflow.active_version) != "trigger.manual":
		raise AutomationError(_("Only workflows published with a manual trigger accept manual enrollment."))
	record = frappe.get_doc(workflow.primary_doctype, payload.get("record_name") or data.get("record_name"))
	record.check_permission("read")
	key = data.get("idempotency_key")
	if not key:
		raise AutomationError(_("An idempotency key is required."))
	run_name = engine.enroll(
		workflow.name,
		record.doctype,
		record.name,
		source="MANUAL",
		occurrence_key=str(key)[:140],
	)
	return {"workflow_id": workflow.name, "run_id": run_name, "enrolled": bool(run_name)}


@frappe.whitelist(methods=["POST"])
def signal_event(event_topic: str, payload=None, record_doctype: str | None = None, record_name: str | None = None):
	"""Resume matching durable event waits from an authorized integration."""
	registry.require_operator()
	return {
		"event_topic": str(event_topic or "").strip(),
		"released": engine.release_event_waiters(
			event_topic,
			_object(payload, "event payload"),
			record_doctype=record_doctype,
			record_name=record_name,
		),
	}


@frappe.whitelist()
def list_runs(
	workflow_id: str,
	start: int = 0,
	page_length: int = 50,
	record_name: str | None = None,
):
	registry.require_operator()
	return engine.list_run_records(workflow_id, start, page_length, record_name)


@frappe.whitelist()
def get_run(run_id: str):
	registry.require_operator()
	return engine.get_run_record(run_id)


@frappe.whitelist()
def get_run_trace(run_id: str, section: str, start: int = 0, page_length: int = 100):
	registry.require_operator()
	return engine.get_run_trace(run_id, section, start, page_length)


@frappe.whitelist()
def list_enrollment_decisions(
	workflow: str | None = None,
	record_doctype: str | None = None,
	record_name: str | None = None,
	decision: str | None = None,
	reason_code: str | None = None,
	source: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	start: int = 0,
	page_length: int = 50
):
	registry.require_operator()
	return observability.list_enrollment_decisions(
		workflow=workflow,
		record_doctype=record_doctype,
		record_name=record_name,
		decision=decision,
		reason_code=reason_code,
		source=source,
		date_from=date_from,
		date_to=date_to,
		start=start,
		page_length=page_length
	)


@frappe.whitelist()
def export_enrollment_decisions(
	workflow: str | None = None,
	record_doctype: str | None = None,
	record_name: str | None = None,
	decision: str | None = None,
	reason_code: str | None = None,
	source: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
):
	registry.require_operator()
	return observability.export_enrollment_decisions(
		workflow=workflow,
		record_doctype=record_doctype,
		record_name=record_name,
		decision=decision,
		reason_code=reason_code,
		source=source,
		date_from=date_from,
		date_to=date_to,
	)


@frappe.whitelist()
def list_incidents(status: str | None = "OPEN", start: int = 0, page_length: int = 50):
	registry.require_operator()
	return observability.list_incidents(status=status, start=start, page_length=page_length)


@frappe.whitelist()
def list_dead_letters(status: str | None = "OPEN", start: int = 0, page_length: int = 50):
	registry.require_operator()
	return observability.list_dead_letters(status=status, start=start, page_length=page_length)


@frappe.whitelist(methods=["POST"])
def resolve_incident(incident_id: str, resolution: str = ""):
	registry.require_operator()
	return observability.resolve_incident(incident_id, resolution)


@frappe.whitelist(methods=["POST"])
def retry_dead_letter(dead_letter_id: str):
	registry.require_operator()
	return observability.retry_dead_letter(dead_letter_id)


@frappe.whitelist(methods=["POST"])
def reconcile_dead_letter(dead_letter_id: str, resolution: str):
	registry.require_operator()
	return observability.reconcile_dead_letter(dead_letter_id, resolution)


@frappe.whitelist(methods=["POST"])
def bulk_retry_dead_letters(dead_letter_ids=None):
	registry.require_operator()
	values = frappe.parse_json(dead_letter_ids) if isinstance(dead_letter_ids, str) else dead_letter_ids
	return observability.bulk_retry_dead_letters(values if isinstance(values, list) else [])


@frappe.whitelist(methods=["POST"])
def bulk_discard_dead_letters(dead_letter_ids=None):
	registry.require_operator()
	values = frappe.parse_json(dead_letter_ids) if isinstance(dead_letter_ids, str) else dead_letter_ids
	return observability.bulk_discard_dead_letters(values if isinstance(values, list) else [])


@frappe.whitelist()
def get_automation_analytics(workflow_id: str | None = None, days: int = 30):
	registry.require_viewer()
	return observability.analytics(workflow_id, days=days)


@frappe.whitelist(methods=["POST"])
def cancel_run(run_id: str):
	registry.require_operator()
	return engine.cancel_run_record(run_id)


@frappe.whitelist(methods=["POST"])
def retry_run(run_id: str):
	registry.require_operator()
	return engine.retry_run_record(run_id)


@frappe.whitelist(methods=["POST"])
def reconcile_effect(effect_id: str, resolution: str):
	registry.require_operator()
	return engine.reconcile_external_effect(effect_id, resolution)


@frappe.whitelist(methods=["POST"])
def preview_backfill(envelope=None, workflow_id=None, filters=None, max_records=0):
	registry.require_operator()
	data = _envelope(envelope, workflow_id=workflow_id, filters=filters, max_records=max_records)
	payload = data["payload"]
	return bulk.preview_backfill(
		data.get("workflow_id"),
		payload.get("filters", data.get("filters")),
		max_records=cint(payload.get("max_records", data.get("max_records", 0))),
	)


@frappe.whitelist(methods=["POST"])
def start_backfill(envelope=None, workflow_id=None, filters=None, batch_size=100, dry_run=0, max_records=0, records_per_minute=500, preview_receipt=None):
	registry.require_operator()
	data = _envelope(
		envelope,
		workflow_id=workflow_id,
		filters=filters,
		batch_size=batch_size,
		dry_run=dry_run,
		max_records=max_records,
		records_per_minute=records_per_minute,
		preview_receipt=preview_receipt,
	)
	payload = data["payload"]
	receipt = payload.get("preview_receipt", data.get("preview_receipt"))
	if not receipt:
		raise AutomationConflictError(_("Preview the audience before starting a backfill."))
	return bulk.create_backfill(
		data.get("workflow_id"),
		payload.get("filters", data.get("filters")),
		cint(payload.get("batch_size", data.get("batch_size", 100))),
		dry_run=bool(cint(payload.get("dry_run", data.get("dry_run", 0)))),
		max_records=cint(payload.get("max_records", data.get("max_records", 0))),
		records_per_minute=cint(payload.get("records_per_minute", data.get("records_per_minute", 500))),
		preview_receipt=receipt,
	)


@frappe.whitelist()
def list_backfills(workflow_id: str, start: int = 0, page_length: int = 50):
	registry.require_operator()
	workflow = frappe.get_doc("Automation Workflow", workflow_id)
	workflow.check_permission("read")
	return {
		"rows": frappe.get_list(
			"Automation Backfill Job",
			filters={"workflow": workflow.name},
			fields=["name", "workflow_version", "source", "schedule", "status", "cursor_name", "batch_size", "records_per_minute", "max_records", "estimated_count", "processed_count", "enrolled_count", "failed_count", "dry_run", "snapshot_at", "next_batch_at", "started_at", "last_heartbeat_at", "completed_at", "error_message", "creation"],
			order_by="creation desc",
			start=max(cint(start), 0),
			limit=min(max(cint(page_length), 1), 100),
		)
	}


@frappe.whitelist(methods=["POST"])
def control_backfill(backfill_id: str, action: str):
	registry.require_operator()
	return bulk.change_backfill_state(backfill_id, action)


@frappe.whitelist()
def get_enrollment_overview(workflow_id: str):
	registry.require_operator()
	workflow = frappe.get_doc("Automation Workflow", workflow_id)
	workflow.check_permission("read")
	versions = frappe.get_list(
		"Automation Workflow Version",
		filters={"workflow": workflow.name},
		fields=["name", "version_no", "published_at", "published_by", "execution_user", "graph_hash"],
		order_by="version_no desc",
		limit=100,
	)
	execution_user = versions[0].execution_user if versions else workflow.execution_user
	return {
		"workflow": {
			"name": workflow.name,
			"title": workflow.title,
			"primary_doctype": workflow.primary_doctype,
			"status": workflow.status,
			"active_version": workflow.active_version,
			"trigger_type": engine.published_trigger_type(workflow.active_version),
		},
		"versions": versions,
		"fields": registry.field_catalog(workflow.primary_doctype, user=execution_user) if versions else [],
		"system_timezone": frappe.utils.get_system_timezone(),
		"runtime_allowed": workflow_runtime_allowed(workflow.name),
	}


@frappe.whitelist(methods=["POST"])
def create_schedule(
	envelope=None,
	workflow_id=None,
	frequency=None,
	next_run_at=None,
	filters=None,
	batch_size=100,
	timezone=None,
	version_policy="ACTIVE_AT_RUN",
	workflow_version=None,
	catch_up_policy="RUN_ONCE",
	overlap_policy="SKIP",
	max_records=0,
	records_per_minute=500,
):
	registry.require_operator()
	data = _envelope(
		envelope,
		workflow_id=workflow_id,
		frequency=frequency,
		next_run_at=next_run_at,
		filters=filters,
		batch_size=batch_size,
		timezone=timezone,
		version_policy=version_policy,
		workflow_version=workflow_version,
		catch_up_policy=catch_up_policy,
		overlap_policy=overlap_policy,
		max_records=max_records,
		records_per_minute=records_per_minute,
	)
	payload = data["payload"]
	return bulk.create_schedule(
		data.get("workflow_id"),
		payload.get("frequency", data.get("frequency")),
		payload.get("next_run_at", data.get("next_run_at")),
		payload.get("filters", data.get("filters")),
		cint(payload.get("batch_size", data.get("batch_size", 100))),
		timezone=payload.get("timezone", data.get("timezone")),
		version_policy=payload.get("version_policy", data.get("version_policy", "ACTIVE_AT_RUN")),
		workflow_version=payload.get("workflow_version", data.get("workflow_version")),
		catch_up_policy=payload.get("catch_up_policy", data.get("catch_up_policy", "RUN_ONCE")),
		overlap_policy=payload.get("overlap_policy", data.get("overlap_policy", "SKIP")),
		max_records=cint(payload.get("max_records", data.get("max_records", 0))),
		records_per_minute=cint(payload.get("records_per_minute", data.get("records_per_minute", 500))),
	)


@frappe.whitelist(methods=["POST"])
def set_schedule_enabled(schedule_id: str, enabled: int):
	registry.require_operator()
	return bulk.set_schedule_enabled(schedule_id, bool(cint(enabled)))


@frappe.whitelist(methods=["POST"])
def delete_schedule(schedule_id: str):
	registry.require_operator()
	return bulk.delete_schedule(schedule_id)


@frappe.whitelist()
def list_schedules(workflow_id: str):
	registry.require_operator()
	workflow = frappe.get_doc("Automation Workflow", workflow_id)
	workflow.check_permission("read")
	rows = frappe.get_list(
		"Automation Schedule",
		filters={"workflow": workflow.name},
		fields=["name", "enabled", "frequency", "timezone", "version_policy", "workflow_version", "catch_up_policy", "overlap_policy", "filters_json", "batch_size", "records_per_minute", "max_records", "next_run_at", "last_run_at", "last_backfill_job", "modified"],
		order_by="creation desc",
		limit=100,
	)
	historical = set()
	if rows:
		historical = set(
			frappe.db.get_values(
				"Automation Backfill Job",
				{"schedule": ["in", [row.name for row in rows]]},
				"schedule",
				pluck=True,
				limit=0,
			)
		)
	for row in rows:
		row.has_history = row.name in historical
	return {"rows": rows}
