from __future__ import annotations

import time
from typing import Any
from zoneinfo import available_timezones

import frappe
from frappe import _
from frappe.email.email_body import get_formatted_html
from frappe.query_builder.functions import Count, JSONValue
from frappe.utils import cint, validate_email_address

from . import authoring, bulk, collaboration, emailing, engine, events, external, observability, registry, webhooks
from .configuration import (
	automation_enabled,
	external_actions_enabled,
	workflow_runtime_allowed,
)
from .errors import AutomationError, AutomationPermissionError, AutomationConflictError
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
def get_node_types(workflow_id: str | None = None):
	registry.require_builder()
	primary_doctype = None
	execution_user = None
	if workflow_id:
		workflow = frappe.get_doc("Automation Workflow", workflow_id)
		workflow.check_permission("read")
		primary_doctype = workflow.primary_doctype
		execution_user = workflow.execution_user
	return {
		"node_types": registry.node_catalog(
			primary_doctype=primary_doctype,
			execution_user=execution_user,
		)
	}


def _email_workflow(workflow_id: str, ptype: str = "read"):
	workflow = frappe.get_doc("Automation Workflow", workflow_id)
	workflow.check_permission(ptype)
	return workflow


def _email_preview_record(workflow, record_name: str | None):
	if not record_name:
		return frappe._dict(doctype=workflow.primary_doctype)
	record = frappe.get_doc(workflow.primary_doctype, record_name)
	record.check_permission("read")
	return record


@frappe.whitelist()
def list_email_templates(workflow_id: str, search: str | None = None, start: int = 0, page_length: int = 20):
	registry.require_builder()
	workflow = _email_workflow(workflow_id)
	meta = frappe.get_meta("Email Template")
	fields = ["name", "subject", "enabled", "use_html", "modified"]
	for fieldname in ("reference_doctype", "custom_reference_doctype", "custom_builder_mode", "custom_preheader_text"):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	filters = {"enabled": 1} if meta.has_field("enabled") else {}
	needle = str(search or "").strip()
	rows = frappe.get_list(
		"Email Template",
		filters=filters,
		or_filters={"name": ["like", f"%{needle}%"], "subject": ["like", f"%{needle}%"]} if needle else None,
		fields=fields,
		order_by="modified desc",
		limit=200,
	)
	compatible = [
		emailing.email_template_summary(row, workflow.primary_doctype)
		for row in rows
		if not emailing.template_reference_doctype(row)
		or emailing.template_reference_doctype(row) == workflow.primary_doctype
	]
	start = max(cint(start), 0)
	limit = min(max(cint(page_length), 1), 50)
	return {"rows": compatible[start : start + limit], "has_more": len(compatible) > start + limit}


@frappe.whitelist()
def list_email_senders(search: str | None = None, page_length: int = 20):
	"""Return enabled outgoing identities, never arbitrary typed sender addresses."""
	registry.require_builder()
	needle = str(search or "").strip()
	# The builder role intentionally receives only these non-secret identity fields;
	# Email Account credentials and transport settings are never returned.
	rows = frappe.get_all(
		"Email Account",
		filters={"enable_outgoing": 1},
		or_filters={
			"name": ["like", f"%{needle}%"],
			"email_id": ["like", f"%{needle}%"],
		}
		if needle
		else None,
		fields=["name", "email_id", "default_outgoing"],
		order_by="default_outgoing desc, name asc",
		limit=min(max(cint(page_length), 1), 50),
	)
	return {
		"rows": [
			{
				"value": row.email_id,
				"label": row.email_id,
				"description": _("{0}{1}").format(
					row.name,
					_(" · Default outgoing") if cint(row.default_outgoing) else "",
				),
			}
			for row in rows
			if row.email_id
		]
	}


@frappe.whitelist()
def get_workflow_email_template(workflow_id: str, template_name: str):
	registry.require_builder()
	workflow = _email_workflow(workflow_id)
	template = emailing.get_email_template(template_name, workflow.primary_doctype)
	return emailing.email_template_summary(template, workflow.primary_doctype)


@frappe.whitelist(methods=["POST"])
def create_workflow_email_template(workflow_id: str, template_name: str, subject: str):
	registry.require_builder()
	workflow = _email_workflow(workflow_id, "write")
	if "finbyzreach" not in frappe.get_installed_apps():
		raise AutomationError(_("The visual Email Template Builder app is not installed."))
	from finbyzreach.email_template_builder.api import create_visual_template

	result = create_visual_template(template_name, subject)
	template = frappe.get_doc("Email Template", result["name"])
	meta = frappe.get_meta("Email Template")
	if meta.has_field("custom_reference_doctype"):
		template.custom_reference_doctype = workflow.primary_doctype
	if meta.has_field("reference_doctype"):
		template.reference_doctype = workflow.primary_doctype
	template.save()
	return {**result, **emailing.email_template_summary(template, workflow.primary_doctype)}


@frappe.whitelist(methods=["POST"])
def preview_workflow_email(workflow_id: str, config=None, record_name: str | None = None):
	registry.require_builder()
	workflow = _email_workflow(workflow_id)
	record = _email_preview_record(workflow, record_name)
	content = emailing.resolve_email_content(
		_object(config, "email configuration"),
		record=record,
		outputs={},
		primary_doctype=workflow.primary_doctype,
	)
	html = get_formatted_html(
		content["subject"], content["message"], raw_html=bool(content["raw_html"]), add_css=not bool(content["raw_html"])
	)
	return {
		**content,
		"html": html,
		"bytes": len(html.encode()),
		"record_name": record_name or None,
	}


def _check_workflow_test_email_rate_limit() -> None:
	window = 600
	key = frappe.cache.make_key(f"workflow-email-test:{frappe.session.user}:{int(time.time()) // window}")
	if not frappe.cache.get(key):
		frappe.cache.setex(key, window, 0)
	if frappe.cache.incrby(key, 1) > 10:
		frappe.throw(_("You can send at most 10 workflow test emails every 10 minutes."), frappe.RateLimitExceededError)


@frappe.whitelist(methods=["POST"])
def send_workflow_test_email(
	workflow_id: str,
	config=None,
	recipient: str | None = None,
	record_name: str | None = None,
):
	registry.require_builder()
	workflow = _email_workflow(workflow_id)
	recipient = str(recipient or "").strip()
	if any(character in recipient for character in (",", ";", "\n", "\r")):
		raise AutomationError(_("Send a test to one email address at a time."))
	validate_email_address(recipient, throw=True)
	_check_workflow_test_email_rate_limit()
	record = _email_preview_record(workflow, record_name)
	values = _object(config, "email configuration")
	content = emailing.resolve_email_content(values, record=record, outputs={}, primary_doctype=workflow.primary_doctype)
	draft_name = frappe.db.get_value("Automation Workflow Draft", {"workflow": workflow.name}, "name")
	settings = parse_object(frappe.db.get_value("Automation Workflow Draft", draft_name, "settings_json") or "{}", "workflow settings") if draft_name else {}
	communication = settings.get("communication") or {}
	sender_email = str(values.get("sender_email") or communication.get("default_sender_email") or "").strip()
	if sender_email and not frappe.db.exists("Email Account", {"email_id": sender_email, "enable_outgoing": 1}):
		raise AutomationError(_("Choose the address of an enabled outgoing Email Account."))
	sender_name = str(values.get("sender_name") or communication.get("default_sender_name") or "").strip()
	sender = f"{sender_name} <{sender_email}>" if sender_name and sender_email else sender_email or None
	reply_to = str(values.get("reply_to") or "").strip()
	if reply_to:
		validate_email_address(reply_to, throw=True)
	queue = frappe.sendmail(
		recipients=[recipient],
		sender=sender,
		reply_to=reply_to or None,
		subject=f"[TEST] {content['subject']}",
		content=content["message"],
		delayed=True,
		reference_doctype="Automation Workflow",
		reference_name=workflow.name,
		add_unsubscribe_link=0,
		raw_html=bool(content["raw_html"]),
		add_css=not bool(content["raw_html"]),
	)
	if not queue or not getattr(queue, "name", None):
		raise AutomationError(_("Frappe did not create an Email Queue record."))
	return {"status": "queued", "email_queue": queue.name, "recipient": recipient, "subject": content["subject"]}


@frappe.whitelist()
def get_event_types(primary_doctype: str | None = None, usage: str = "all"):
	registry.require_builder()
	usage = str(usage or "all").strip().lower()
	if usage not in {"all", "trigger", "wait"}:
		raise AutomationError(_("Event catalogue usage must be all, trigger, or wait."))
	return {
		"event_types": registry.business_event_catalog(primary_doctype, usage),
		"object_profile": registry.workflow_object_profile(primary_doctype),
		"usage": usage,
	}


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
	folder: str | None = None,
):
	registry.require_viewer()
	return authoring.list_workflow_records(start, page_length, status, search, primary_doctype, exclude_workflow, folder)


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
def create_workflow(envelope=None, title=None, primary_doctype=None, description=None, execution_user=None, trigger_type=None, folder=None):
	registry.require_builder()
	data = _envelope(
		envelope,
		title=title,
		primary_doctype=primary_doctype,
		description=description,
		execution_user=execution_user,
		trigger_type=trigger_type,
		folder=folder,
	)
	payload = data["payload"]
	return authoring.create_workflow_record(
		payload.get("title") or data.get("title"),
		payload.get("primary_doctype") or data.get("primary_doctype"),
		payload.get("description") or data.get("description") or "",
		payload.get("execution_user") or data.get("execution_user"),
		payload.get("trigger_type") or data.get("trigger_type") or "trigger.manual",
		payload.get("folder") or data.get("folder") or "",
		idempotency_key=data.get("idempotency_key"),
	)


@frappe.whitelist(methods=["POST"])
def set_workflow_folder(envelope=None, workflow_id=None, folder=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id, folder=folder)
	return authoring.set_workflow_folder(data.get("workflow_id"), data["payload"].get("folder", data.get("folder")))


@frappe.whitelist(methods=["POST"])
def delete_workflow(envelope=None, workflow_id=None, delete_history=None):
	registry.require_builder()
	data = _envelope(envelope, workflow_id=workflow_id, delete_history=delete_history)
	return authoring.delete_workflow_record(
		data.get("workflow_id"),
		data["payload"].get("delete_history", data.get("delete_history")),
	)


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
def signal_event(
	event_topic: str,
	payload=None,
	record_doctype: str | None = None,
	record_name: str | None = None,
	source_doctype: str | None = None,
	source_name: str | None = None,
	idempotency_key: str | None = None,
):
	"""Resume waits and enroll matching event-triggered workflows."""
	registry.require_operator()
	return events.signal_business_event(
		event_topic,
		_object(payload, "event payload"),
		record_doctype=record_doctype,
		record_name=record_name,
		source_doctype=source_doctype,
		source_name=source_name,
		idempotency_key=idempotency_key,
		check_record_permission=True,
	)


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


@frappe.whitelist()
def get_canvas_metrics(workflow_id: str, workflow_version: str | None = None):
	"""Return idempotent per-step reach and branch counts for the published canvas."""
	registry.require_viewer()
	workflow = frappe.get_doc("Automation Workflow", workflow_id)
	workflow.check_permission("read")
	version = str(workflow_version or workflow.active_version or "").strip()
	if not version:
		return {"workflow_id": workflow.name, "workflow_version": None, "total_enrollments": 0, "nodes": []}
	if not frappe.db.exists("Automation Workflow Version", {"name": version, "workflow": workflow.name}):
		raise AutomationPermissionError(_("That workflow version is not available for this workflow."))

	Run = frappe.qb.DocType("Automation Run")
	Token = frappe.qb.DocType("Automation Run Token")
	count = Count(Token.name).as_("count")
	rows = (
		frappe.qb.from_(Token)
		.join(Run).on(Run.name == Token.run)
		.select(Token.node_id, Token.status, count)
		.where((Run.workflow == workflow.name) & (Run.workflow_version == version))
		.groupby(Token.node_id, Token.status)
	).run(as_dict=True)

	metrics: dict[str, dict] = {}
	for row in rows:
		node_id = str(row.node_id)
		metric = metrics.setdefault(node_id, {
			"node_id": node_id,
			"reached": 0,
			"ready": 0,
			"running": 0,
			"waiting": 0,
			"completed": 0,
			"failed": 0,
			"cancelled": 0,
			"branches": {},
		})
		status = str(row.status or "").lower()
		value = cint(row.count)
		metric[status] = metric.get(status, 0) + value
		metric["reached"] += value

	branch_handle = JSONValue(Token.output_json, "$.selected_handle")
	branch_count = Count(Token.name).as_("count")
	branch_rows = (
		frappe.qb.from_(Token)
		.join(Run).on(Run.name == Token.run)
		.select(Token.node_id, branch_handle.as_("branch_handle"), branch_count)
		.where(
			(Run.workflow == workflow.name)
			& (Run.workflow_version == version)
			& (Token.status == "COMPLETED")
			& Token.output_json.isnotnull()
			& branch_handle.isnotnull()
		)
		.groupby(Token.node_id, branch_handle)
	).run(as_dict=True)
	for row in branch_rows:
		handle = str(row.branch_handle or "").strip()
		if not handle or str(row.node_id) not in metrics:
			continue
		branches = metrics[str(row.node_id)]["branches"]
		branches[handle] = cint(branches.get(handle)) + cint(row.count)

	total_enrollments = cint(frappe.db.count("Automation Run", {"workflow": workflow.name, "workflow_version": version}))
	return {
		"workflow_id": workflow.name,
		"workflow_version": version,
		"total_enrollments": total_enrollments,
		"nodes": list(metrics.values()),
	}


@frappe.whitelist()
def get_workflow_connections(workflow_id: str):
	registry.require_viewer()
	return collaboration.workflow_connections(workflow_id)


@frappe.whitelist()
def list_workflow_comments(workflow_id: str, step_id: str | None = None, include_resolved: int = 1):
	registry.require_viewer()
	return collaboration.list_comments(workflow_id, step_id=step_id, include_resolved=bool(cint(include_resolved)))


@frappe.whitelist(methods=["POST"])
def create_workflow_comment(workflow_id: str, content: str, step_id: str | None = None, mention_users=None):
	registry.require_builder()
	return collaboration.create_comment(workflow_id, content, step_id=step_id, mention_users=mention_users)


@frappe.whitelist(methods=["POST"])
def set_workflow_comment_resolved(comment_id: str, resolved: int = 1):
	registry.require_builder()
	return collaboration.set_comment_resolved(comment_id, bool(cint(resolved)))


@frappe.whitelist(methods=["POST"])
def delete_workflow_comment(comment_id: str):
	registry.require_builder()
	return collaboration.delete_comment(comment_id)


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
	recurrence=None,
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
		recurrence=recurrence,
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
		recurrence=payload.get("recurrence", data.get("recurrence")),
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
		fields=["name", "enabled", "frequency", "recurrence_json", "timezone", "version_policy", "workflow_version", "catch_up_policy", "overlap_policy", "filters_json", "batch_size", "records_per_minute", "max_records", "next_run_at", "last_run_at", "last_backfill_job", "modified"],
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


@frappe.whitelist(methods=["POST"])
def create_inbound_webhook(envelope=None, workflow_id=None, **kwargs):
	registry.require_publisher()
	data = _envelope(envelope, workflow_id=workflow_id, **kwargs)
	payload = data["payload"]
	return webhooks.create_definition(
		data.get("workflow_id"),
		payload.get("title") or "Inbound workflow webhook",
		auth_type=payload.get("auth_type") or "HMAC SHA256",
		record_identity_field=payload.get("record_identity_field") or "name",
		payload_record_path=payload.get("payload_record_path") or "record_id",
		payload_fields=payload.get("payload_fields"),
		payload_filters=payload.get("payload_filters"),
		idempotency_path=payload.get("idempotency_path") or "event_id",
		max_request_bytes=cint(payload.get("max_request_bytes") or 262144),
		requests_per_minute=cint(payload.get("requests_per_minute") or 60),
	)


@frappe.whitelist()
def list_inbound_webhooks(workflow_id: str):
	registry.require_publisher()
	return webhooks.list_definitions(workflow_id)


@frappe.whitelist(methods=["POST"])
def rotate_inbound_webhook_secret(webhook_id: str):
	registry.require_publisher()
	return webhooks.rotate_secret(webhook_id)


@frappe.whitelist(methods=["POST"])
def set_inbound_webhook_enabled(webhook_id: str, enabled: int):
	registry.require_publisher()
	return webhooks.set_enabled(webhook_id, bool(cint(enabled)))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_inbound_webhook(endpoint_key: str):
	return webhooks.receive(endpoint_key)
