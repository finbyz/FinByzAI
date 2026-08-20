from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.desk.form.assign_to import _add as add_assignment, close_all_assignments
from frappe.utils import add_to_date, cint, get_system_timezone, now_datetime, validate_email_address

from .configuration import automation_enabled, int_setting, workflow_runtime_allowed
from .constants import (
	ACTION_NODE_TYPES,
	EXTERNAL_ACTION_NODE_TYPES,
	MAX_RECURSION_DEPTH,
	MAX_SNAPSHOT_COLLECTION_ROWS,
	RETRY_DELAYS_SECONDS,
	RUN_TERMINAL_STATUSES,
	TOKEN_TERMINAL_STATUSES,
)
from .errors import AutomationCancelledError, AutomationError, AutomationTransientError
from .notifications import enqueue_notification_for_user
from .observability import increment_metric, record_enrollment_decision, record_incident
from .registry import assert_field_access, is_eligible_doctype
from .schema import (
	canonical_json,
	condition_fields,
	event_filter_matches,
	event_wait_data_source,
	event_wait_timeout_mode,
	evaluate_expression,
	parse_object,
	resolve_value,
)

_MISSING = object()
_SET_USER_LOCAL_FIELDS = (
	"cache",
	"form_dict",
	"jenv_restricted",
	"jenv_unrestricted",
	"role_permissions",
	"new_doc_templates",
	"user_perms",
)


def _assert_worker_execution() -> None:
	"""Business actions may impersonate users only in an isolated RQ job or a test transaction."""
	if not frappe.in_test and not getattr(frappe.local, "job", None):
		raise AutomationError(_("Automation tokens can only execute inside an isolated background worker."))


@contextmanager
def _execution_identity(user: str, automation_context: dict) -> Iterator[None]:
	"""Temporarily assume the execution user and restore every local reset by frappe.set_user."""
	_assert_worker_execution()
	session = frappe.local.session
	session_snapshot = {
		"user": session.user,
		"sid": session.sid,
		"data": session.data,
	}
	local_snapshot = {
		fieldname: getattr(frappe.local, fieldname, _MISSING) for fieldname in _SET_USER_LOCAL_FIELDS
	}
	had_automation_context = "automation_context" in frappe.flags
	previous_automation_context = frappe.flags.get("automation_context")
	try:
		frappe.set_user(user)
		frappe.flags.automation_context = automation_context
		yield
	finally:
		# Clear permissions/caches created for the execution identity first, then
		# restore the exact caller-owned local objects instead of only its username.
		frappe.set_user(session_snapshot["user"])
		session.user = session_snapshot["user"]
		session.sid = session_snapshot["sid"]
		session.data = session_snapshot["data"]
		for fieldname, value in local_snapshot.items():
			if value is _MISSING:
				if hasattr(frappe.local, fieldname):
					delattr(frappe.local, fieldname)
			else:
				setattr(frappe.local, fieldname, value)
		if had_automation_context:
			frappe.flags.automation_context = previous_automation_context
		else:
			frappe.flags.pop("automation_context", None)


def _new_trace_id() -> str:
	return frappe.generate_hash(length=20)


def _graph(version) -> dict:
	return parse_object(version.graph_json, "published workflow graph")


def published_trigger_type(version_name: str | None) -> str | None:
	"""Return the pinned version's start-node type, or None when no version is available."""
	if not version_name:
		return None
	version = frappe.get_doc("Automation Workflow Version", version_name)
	graph = _graph(version)
	start_node_id = graph.get("start_node_id")
	start_node = next((node for node in graph.get("nodes") or [] if node.get("id") == start_node_id), None)
	return str(start_node.get("type")) if start_node and start_node.get("type") else None


def _node_map(graph: dict) -> dict[str, dict]:
	return {node["id"]: node for node in graph.get("nodes") or []}


def _deduplicate_fields(node: dict) -> tuple[list[str], str]:
	config = node.get("config") or {}
	if cint(node.get("type_version") or 1) >= 2:
		return [str(field) for field in config.get("match_fields") or [] if str(field or "").strip()], str(config.get("match_mode") or "all")
	field = str(config.get("match_field") or "").strip()
	return ([field] if field else []), "all"


def _duplicate_filter(node: dict, record, values=None) -> tuple[dict, list[str]]:
	fields, mode = _deduplicate_fields(node)
	values = values or record
	predicates = [{field: values.get(field)} for field in fields if values.get(field) not in (None, "")]
	if not predicates or (mode == "all" and len(predicates) != len(fields)):
		return {}, []
	filters: dict[str, Any] = {"name": ("!=", record.name)}
	if mode == "all":
		for predicate in predicates:
			filters.update(predicate)
	else:
		filters["or_filters"] = predicates
	return filters, [next(iter(predicate)) for predicate in predicates]


def _find_duplicate(node: dict, record, values=None):
	filters, matched_fields = _duplicate_filter(node, record, values)
	if not filters:
		return None, []
	or_filters = filters.pop("or_filters", None)
	if not or_filters:
		return frappe.db.exists(record.doctype, filters), matched_fields
	rows = frappe.get_all(record.doctype, filters=filters, or_filters=or_filters, pluck="name", limit=1)
	return (rows[0] if rows else None), matched_fields


def _number(value: Any) -> float:
	if isinstance(value, bool):
		raise AutomationError(_("Boolean values cannot be converted to numbers."))
	if isinstance(value, (int, float)):
		return float(value)
	text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
	if not text:
		raise AutomationError(_("A numeric transform input is empty."))
	# Accept common 1,234.56 and 1.234,56 forms without depending on server locale.
	if "," in text and "." in text:
		decimal = "," if text.rfind(",") > text.rfind(".") else "."
		thousands = "." if decimal == "," else ","
		text = text.replace(thousands, "").replace(decimal, ".")
	elif "," in text:
		parts = text.split(",")
		text = ".".join(parts) if len(parts[-1]) != 3 else "".join(parts)
	text = re.sub(r"[^0-9+\-.]", "", text)
	try:
		return float(text)
	except ValueError as exc:
		raise AutomationError(_("Value {0} is not a number.").format(value)) from exc


def _transform_output(config: dict, values: list[Any], *, seed: str) -> Any:
	operation = str(config.get("operation") or "")
	if operation == "coalesce":
		return next((item for item in values if item not in (None, "")), None)
	if operation == "concat":
		return str(config.get("separator") or "").join("" if item is None else str(item) for item in values)
	if operation == "upper":
		return str(values[0] if values else "").upper()
	if operation == "lower":
		return str(values[0] if values else "").lower()
	if operation == "parse_number":
		return _number(values[0])
	if operation == "format_number":
		decimals = min(max(cint(config.get("decimals", 2)), 0), 12)
		formatted = f"{_number(values[0]):,.{decimals}f}"
		if not cint(config.get("use_grouping", 1)):
			formatted = formatted.replace(",", "")
		return formatted
	if operation == "format_phone":
		digits = re.sub(r"\D", "", str(values[0] if values else ""))
		if not 3 <= len(digits) <= 15:
			raise AutomationError(_("Phone transform requires between 3 and 15 digits."))
		country_code = re.sub(r"\D", "", str(config.get("country_code") or ""))
		if country_code and not str(values[0]).strip().startswith("+") and not digits.startswith(country_code):
			digits = country_code + digits.lstrip("0")
		return f"+{digits}"
	if operation == "format_currency":
		decimals = min(max(cint(config.get("decimals", 2)), 0), 12)
		currency = str(config.get("currency") or "").strip()
		return f"{currency} {_number(values[0]):,.{decimals}f}".strip()
	if operation == "random_number":
		minimum = float(config.get("minimum", 0))
		maximum = float(config.get("maximum", 100))
		fraction = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big") / 2**64
		value = minimum + fraction * (maximum - minimum)
		return int(value) if cint(config.get("integer", 1)) else value
	if operation == "math":
		numbers = [_number(value) for value in values]
		if not numbers:
			raise AutomationError(_("Math transform needs at least one input."))
		result = numbers[0]
		for number in numbers[1:]:
			math_operation = str(config.get("math_operation") or "add")
			if math_operation == "add": result += number
			elif math_operation == "subtract": result -= number
			elif math_operation == "multiply": result *= number
			elif math_operation == "divide":
				if number == 0: raise AutomationError(_("Math transform cannot divide by zero."))
				result /= number
			elif math_operation == "modulo":
				if number == 0: raise AutomationError(_("Math transform cannot divide by zero."))
				result %= number
			elif math_operation == "power": result **= number
		return result
	raise AutomationError(_("Unsupported transform operation."))


def _record_key(doctype: str, name: str) -> str:
	return f"{doctype}:{name}"


def _record_fields(graph: dict, settings: dict) -> set[str]:
	"""Find the already-validated primary-record fields needed by the pinned version."""
	fields = set(condition_fields(settings.get("goal_condition")))
	fields.update(condition_fields(settings.get("eligibility_condition")))
	for node in graph.get("nodes") or []:
		config = node.get("config") or {}
		field_by_type = {
			"condition.switch": "field",
			"delay.until_date": "field",
			"transform.associated_record": "reference_field",
			"transform.child_records": "child_table_field",
		}.get(node.get("type"))
		if field_by_type and config.get(field_by_type):
			fields.add(str(config[field_by_type]))
		if node.get("type") == "condition.deduplicate":
			fields.update(_deduplicate_fields(node)[0])
	stack: list[Any] = [node.get("config") or {} for node in graph.get("nodes") or []]
	while stack:
		value = stack.pop()
		if isinstance(value, dict):
			fields.update(condition_fields(value.get("condition")))
			if value.get("kind") == "record_field" and value.get("field"):
				fields.add(str(value["field"]))
			if value.get("field") and value.get("kind") is None and set(value).intersection({"offset_minutes", "seconds"}):
				fields.add(str(value["field"]))
			stack.extend(value.values())
		elif isinstance(value, list):
			stack.extend(value)
	return fields


def _child_snapshot_fields(graph: dict) -> dict[str, set[str]]:
	result: dict[str, set[str]] = {}
	for node in graph.get("nodes") or []:
		if node.get("type") != "transform.child_records":
			continue
		config = node.get("config") or {}
		parent_field = str(config.get("child_table_field") or "")
		child_field = str(config.get("fetch_field") or "")
		if parent_field and child_field:
			result.setdefault(parent_field, set()).add(child_field)
	return result


def _table_multiselect_definition(doctype: str, fieldname: str):
	field = frappe.get_meta(doctype).get_field(fieldname)
	if not field or field.fieldtype != "Table MultiSelect" or not field.options:
		return None
	child_meta = frappe.get_meta(field.options)
	links = [child for child in child_meta.fields if child.fieldtype == "Link"]
	listed = [child for child in links if child.in_list_view]
	link = listed[0] if len(listed) == 1 else links[0] if len(links) == 1 else None
	if not link or not link.options:
		raise AutomationError(_("Table MultiSelect {0}.{1} has no unambiguous Link field.").format(doctype, fieldname))
	return field, link


def _multiselect_names(doctype: str, fieldname: str, value: Any, *, validate_links: bool = False) -> list[str]:
	definition = _table_multiselect_definition(doctype, fieldname)
	if not definition:
		raise AutomationError(_("Field {0}.{1} is not a Table MultiSelect field.").format(doctype, fieldname))
	_field, link = definition
	if value in (None, ""):
		return []
	if not isinstance(value, list):
		raise AutomationError(_("Table MultiSelect values must be a list."))
	result = []
	for item in value:
		if isinstance(item, str):
			candidate = item
		elif hasattr(item, "get"):
			candidate = item.get(link.fieldname)
		else:
			candidate = None
		candidate = str(candidate or "").strip()
		if not candidate or candidate in result:
			continue
		if validate_links and not frappe.db.exists(link.options, candidate):
			raise AutomationError(_("{0} {1} does not exist.").format(link.options, candidate))
		result.append(candidate)
	return result


def _snapshot_record(record, graph: dict, settings: dict) -> dict:
	values = {"doctype": record.doctype, "name": record.name}
	child_fields = _child_snapshot_fields(graph)
	child_snapshots: dict[str, list[dict]] = {}
	for fieldname in sorted(_record_fields(graph, settings)):
		field = frappe.get_meta(record.doctype).get_field(fieldname)
		value = record.get(fieldname)
		if field and field.fieldtype == "Table MultiSelect":
			names = _multiselect_names(record.doctype, fieldname, value)
			if len(names) > MAX_SNAPSHOT_COLLECTION_ROWS:
				raise AutomationError(_("Collection field {0} exceeds the snapshot row limit.").format(fieldname))
			values[fieldname] = sorted(names)
			definition = _table_multiselect_definition(record.doctype, fieldname)
			link_field = definition[1].fieldname if definition else None
			wanted = child_fields.get(fieldname, set()) - ({link_field} if link_field else set())
			if wanted:
				child_snapshots[fieldname] = [
					{child_field: row.get(child_field) for child_field in sorted(wanted)}
					for row in (value or [])
				]
		elif field and field.fieldtype == "Table":
			rows = value or []
			if len(rows) > MAX_SNAPSHOT_COLLECTION_ROWS:
				raise AutomationError(_("Collection field {0} exceeds the snapshot row limit.").format(fieldname))
			wanted = child_fields.get(fieldname, set())
			values[fieldname] = [
				{child_field: row.get(child_field) for child_field in sorted(wanted)} for row in rows
			]
		else:
			values[fieldname] = value
	if child_snapshots:
		values["__automation_child_records__"] = child_snapshots
	return values


def _read_record(run, current_record):
	if str(run.read_mode or "CURRENT") != "ENROLLMENT_SNAPSHOT":
		return current_record
	snapshot = parse_object(run.enrollment_snapshot_json or "{}", "enrollment snapshot")
	return frappe._dict(snapshot)


def _trigger_condition(graph: dict) -> dict | None:
	start = _node_map(graph).get(graph.get("start_node_id")) or {}
	return (start.get("config") or {}).get("condition")


def _policy_dependency_fields(settings: dict, graph: dict) -> set[str]:
	"""Fields whose live changes can alter an active run's lifecycle."""
	fields = set(condition_fields(settings.get("goal_condition")))
	if cint(settings.get("unenroll_when_ineligible")):
		fields.update(condition_fields(settings.get("eligibility_condition") or _trigger_condition(graph)))
	return fields


def _active_policy_candidates(record_doctype: str, record_name: str) -> list[dict]:
	if not frappe.db.table_exists("Automation Run"):
		return []
	runs = frappe.get_list(
		"Automation Run",
		filters={
			"record_doctype": record_doctype,
			"record_name": record_name,
			"status": ["in", ["QUEUED", "RUNNING", "WAITING"]],
		},
		fields=["name", "workflow", "workflow_version", "read_mode"],
		order_by="creation asc",
		ignore_permissions=True,
		limit=0,
	)
	if not runs:
		return []
	workflow_names = sorted({row.workflow for row in runs})
	workflow_states = {
		row.name: row.status
		for row in frappe.get_list(
			"Automation Workflow",
			filters={"name": ["in", workflow_names]},
			fields=["name", "status"],
			ignore_permissions=True,
			limit=0,
		)
	}
	allowed = {
		workflow_name
		for workflow_name in workflow_names
		if workflow_states.get(workflow_name) == "ACTIVE" and workflow_runtime_allowed(workflow_name)
	}
	version_names = sorted({row.workflow_version for row in runs if row.workflow in allowed})
	if not version_names:
		return []
	versions = {
		row.name: row
		for row in frappe.get_list(
			"Automation Workflow Version",
			filters={"name": ["in", version_names]},
			fields=["name", "workflow", "settings_json", "graph_json", "execution_user"],
			ignore_permissions=True,
			limit=0,
		)
	}
	candidates = []
	for run in runs:
		version = versions.get(run.workflow_version)
		if not version or run.workflow not in allowed or version.workflow != run.workflow:
			continue
		settings = parse_object(version.settings_json or "{}", "workflow settings")
		read_mode = str(run.read_mode or settings.get("read_mode") or "CURRENT").upper()
		if read_mode == "ENROLLMENT_SNAPSHOT":
			continue
		graph = parse_object(version.graph_json, "published workflow graph")
		dependencies = _policy_dependency_fields(settings, graph)
		if dependencies:
			candidates.append(
				{
					"run": run.name,
					"workflow": run.workflow,
					"version": version,
					"settings": settings,
					"graph": graph,
					"dependencies": dependencies,
				}
			)
	return candidates


def active_policy_dependency_fields(record_doctype: str, record_name: str) -> set[str]:
	"""Fast capture-time union used to avoid irrelevant outbox traffic."""
	fields: set[str] = set()
	for candidate in _active_policy_candidates(record_doctype, record_name):
		fields.update(candidate["dependencies"])
	return fields


def _matching_suppression(workflow_name: str, record) -> Any | None:
	if not frappe.db.table_exists("Automation Suppression Rule"):
		return None
	now = now_datetime()
	rows = frappe.get_list(
		"Automation Suppression Rule",
		filters={"workflow": workflow_name, "enabled": 1},
		fields=["name", "title", "reason", "condition_json", "valid_from", "valid_until"],
		order_by="priority asc, creation asc",
		ignore_permissions=True,
		limit=0,
	)
	for row in rows:
		if row.valid_from and row.valid_from > now:
			continue
		if row.valid_until and row.valid_until < now:
			continue
		if evaluate_expression(parse_object(row.condition_json, "suppression condition"), record):
			return row
	return None


def _append_event(run_name: str, event_type: str, *, node_id: str | None = None, payload: dict | None = None) -> None:
	# The run row serializes event writers. Derive the next sequence from the
	# append-only event stream so a stale in-memory Run document cannot reuse it.
	frappe.db.get_value("Automation Run", run_name, "name", for_update=True)
	state_version = cint(
		frappe.db.get_value("Automation Run Event", {"run": run_name}, [{"MAX": "sequence_no"}])
	) + 1
	frappe.db.set_value("Automation Run", run_name, "state_version", state_version, update_modified=False)
	frappe.get_doc(
		{
			"doctype": "Automation Run Event",
			"run": run_name,
			"sequence_no": state_version,
			"event_type": event_type,
			"node_id": node_id,
			"payload_json": json.dumps(payload or {}, default=str),
			"occurred_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	frappe.publish_realtime(
		"automation_run_updated",
		{"run_id": run_name, "event_type": event_type, "node_id": node_id, "sequence_no": state_version},
		doctype="Automation Run",
		docname=run_name,
		after_commit=True,
	)


def _queue_token(token_name: str) -> None:
	if not automation_enabled():
		return
	site = str(getattr(frappe.local, "site", "site")).replace(".", "-")
	frappe.enqueue(
		"finbyzai.workflow_builder.engine.execute_token",
		token_name=token_name,
		queue="default",
		enqueue_after_commit=True,
		job_id=f"automation-{site}-token-{token_name}",
		deduplicate=True,
	)


def enroll(
	workflow_name: str,
	record_doctype: str,
	record_name: str,
	*,
	source: str,
	occurrence_key: str,
	workflow_version: str | None = None,
	require_active_version: bool = False,
	causation_id: str | None = None,
	recursion_depth: int = 0,
) -> str | None:
	if not workflow_runtime_allowed(workflow_name):
		return None
	workflow = frappe.get_doc("Automation Workflow", workflow_name, for_update=True)
	if workflow.status != "ACTIVE" or not workflow.active_version:
		return None
	if workflow.primary_doctype != record_doctype or not frappe.db.exists(record_doctype, record_name):
		return None
	if recursion_depth > int_setting("max_recursion_depth", MAX_RECURSION_DEPTH):
		return None
	version = frappe.get_doc("Automation Workflow Version", workflow_version or workflow.active_version)
	if require_active_version and version.name != workflow.active_version:
		return None
	if version.workflow != workflow.name:
		raise AutomationError(_("The requested workflow version does not belong to this workflow."))
	if version.primary_doctype != record_doctype:
		raise AutomationError(_("The requested workflow version cannot enroll this DocType."))
	settings = parse_object(version.settings_json or "{}", "workflow settings")
	graph = _graph(version)
	record = frappe.get_doc(record_doctype, record_name)
	trace_id = _new_trace_id()
	if not frappe.has_permission(record_doctype, ptype="read", doc=record, user=version.execution_user):
		record_enrollment_decision(
			workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
			record_name=record_name, source=source, occurrence_key=occurrence_key, decision="REJECTED",
			reason_code="RECORD_PERMISSION_DENIED", evidence={}, trace_id=trace_id,
		)
		return None
	eligibility = settings.get("eligibility_condition") or _trigger_condition(graph)
	if eligibility and not evaluate_expression(eligibility, record):
		record_enrollment_decision(
			workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
			record_name=record_name, source=source, occurrence_key=occurrence_key, decision="REJECTED",
			reason_code="ELIGIBILITY_CONDITION_FALSE", evidence={"fields": sorted(condition_fields(eligibility))}, trace_id=trace_id,
		)
		return None
	read_mode = str(settings.get("read_mode") or "CURRENT").upper()
	if read_mode not in {"CURRENT", "ENROLLMENT_SNAPSHOT"}:
		raise AutomationError(_("Published workflow has an invalid record read mode."))
	suppression = _matching_suppression(workflow.name, record)
	if suppression:
		record_enrollment_decision(
			workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
			record_name=record_name, source=source, occurrence_key=occurrence_key, decision="SUPPRESSED",
			reason_code="SUPPRESSION_RULE", evidence={"rule": suppression.name, "title": suppression.title}, trace_id=trace_id,
		)
		return None
	goal_condition = settings.get("goal_condition")
	if goal_condition and evaluate_expression(goal_condition, record):
		record_enrollment_decision(
			workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
			record_name=record_name, source=source, occurrence_key=occurrence_key, decision="GOAL_ALREADY_MET",
			reason_code="GOAL_CONDITION_TRUE", evidence={"fields": sorted(condition_fields(goal_condition))}, trace_id=trace_id,
		)
		return None
	reenrollment = str(settings.get("reenrollment") or "NEVER").upper()
	duplicate_occurrence = frappe.db.get_value(
		"Automation Enrollment Ledger",
		{
			"workflow": workflow.name,
			"record_key": _record_key(record_doctype, record_name),
			"occurrence_key": occurrence_key,
		},
		"name",
		for_update=True,
	)
	if duplicate_occurrence:
		record_enrollment_decision(
			workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
			record_name=record_name, source=source, occurrence_key=occurrence_key, decision="DUPLICATE",
			reason_code="OCCURRENCE_ALREADY_PROCESSED", evidence={"ledger": duplicate_occurrence}, trace_id=trace_id,
		)
		return None
	previous = frappe.db.get_value(
		"Automation Enrollment Ledger",
		{"workflow": workflow.name, "record_key": _record_key(record_doctype, record_name)},
		["name", "run", "enrollment_count"],
		as_dict=True,
		order_by="creation desc",
		for_update=True,
	)
	if previous and reenrollment == "NEVER":
		record_enrollment_decision(
			workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
			record_name=record_name, source=source, occurrence_key=occurrence_key, decision="DUPLICATE",
			reason_code="REENROLLMENT_DISABLED", evidence={"previous_run": previous.run}, trace_id=trace_id,
		)
		return None
	if previous and reenrollment == "AFTER_COMPLETION":
		previous_status = frappe.db.get_value("Automation Run", previous.run, "status")
		if previous_status not in RUN_TERMINAL_STATUSES:
			record_enrollment_decision(
				workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
				record_name=record_name, source=source, occurrence_key=occurrence_key, decision="REJECTED",
				reason_code="PREVIOUS_RUN_ACTIVE", evidence={"previous_run": previous.run, "status": previous_status}, trace_id=trace_id,
			)
			return None
	if reenrollment not in {"NEVER", "AFTER_COMPLETION", "ALWAYS"}:
		raise AutomationError(_("Published workflow has an invalid re-enrollment policy."))
	snapshot = _snapshot_record(record, graph, settings) if read_mode == "ENROLLMENT_SNAPSHOT" else {}
	run = frappe.get_doc(
		{
			"doctype": "Automation Run",
			"workflow": workflow.name,
			"workflow_version": version.name,
			"record_doctype": record_doctype,
			"record_name": record_name,
			"record_key": _record_key(record_doctype, record_name),
			"source": source,
			"read_mode": read_mode,
			"enrollment_snapshot_json": json.dumps(snapshot, default=str) if snapshot else None,
			"status": "QUEUED",
			"trace_id": trace_id,
			"causation_id": causation_id,
			"recursion_depth": recursion_depth,
			"state_version": 0,
		}
	).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Automation Enrollment Ledger",
			"workflow": workflow.name,
			"record_doctype": record_doctype,
			"record_name": record_name,
			"record_key": _record_key(record_doctype, record_name),
			"run": run.name,
			"occurrence_key": occurrence_key,
			"enrollment_count": cint(previous.enrollment_count) + 1 if previous else 1,
			"last_enrolled_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	token = frappe.get_doc(
		{
			"doctype": "Automation Run Token",
			"run": run.name,
			"node_id": graph["start_node_id"],
			"occurrence": 1,
			"status": "READY",
			"available_at": now_datetime(),
			"attempts": 0,
		}
	).insert(ignore_permissions=True)
	_append_event(run.name, "RUN_CREATED", payload={"source": source, "record_key": run.record_key})
	record_enrollment_decision(
		workflow=workflow.name, workflow_version=version.name, record_doctype=record_doctype,
		record_name=record_name, source=source, occurrence_key=occurrence_key, decision="ENROLLED",
		reason_code="MATCHED", evidence={"read_mode": read_mode}, trace_id=trace_id, run=run.name,
	)
	_queue_token(token.name)
	return run.name


def _completed_outputs(run_name: str) -> dict[str, Any]:
	outputs = {}
	for row in frappe.get_list(
		"Automation Run Token",
		filters={"run": run_name, "status": "COMPLETED"},
		fields=["node_id", "output_json"],
		ignore_permissions=True,
		limit=0,
	):
		outputs[row.node_id] = json.loads(row.output_json or "{}")
	return outputs


def _assignments(config: dict, *, record, outputs: dict[str, Any]) -> dict:
	values = {}
	for assignment in config.get("assignments") or []:
		fieldname = assignment.get("field")
		if not fieldname:
			raise AutomationError(_("Every assignment needs a field."))
		values[fieldname] = resolve_value(assignment.get("value"), record=record, outputs=outputs)
	return values


def _coerce_assignment_value(doctype: str, fieldname: str, value: Any) -> Any:
	definition = _table_multiselect_definition(doctype, fieldname)
	if not definition:
		return value
	_field, link = definition
	return [{link.fieldname: name} for name in _multiselect_names(doctype, fieldname, value, validate_links=True)]


def _effect_key(run_name: str, node_id: str, occurrence: int) -> str:
	return f"{run_name}:{node_id}:{occurrence}"


def _calculate_numeric_adjustment(current: float, operation: str, amount: float) -> float:
	if operation == "add":
		return current + amount
	if operation == "subtract":
		return current - amount
	if operation == "multiply":
		return current * amount
	if operation == "set":
		return amount
	raise AutomationError(_("Unsupported numeric adjustment operation."))


def _next_round_robin_member(run, node: dict, users: list[str]) -> str:
	"""Atomically advance the immutable workflow-version/node cursor."""
	cursor_key = hashlib.sha256(f"{run.workflow_version}\0{node['id']}".encode()).hexdigest()
	# A missing-row SELECT followed by INSERT takes competing InnoDB gap locks and
	# can deadlock under the exact concurrent burst this cursor must serialize.
	# Upsert the identity first, then lock the one materialized row.
	cursor_table = frappe.qb.DocType("Automation Round Robin Cursor")
	now = now_datetime()
	(
		frappe.qb.into(cursor_table)
		.columns(
			cursor_table.name,
			cursor_table.creation,
			cursor_table.modified,
			cursor_table.modified_by,
			cursor_table.owner,
			cursor_table.docstatus,
			cursor_table.idx,
			cursor_table.cursor_key,
			cursor_table.workflow_version,
			cursor_table.node_id,
			cursor_table.next_index,
		)
		.insert(
			frappe.generate_hash(length=10), now, now, frappe.session.user, frappe.session.user,
			0, 0, cursor_key, run.workflow_version, node["id"], 0,
		)
		.on_duplicate_key_update(cursor_table.cursor_key, cursor_key)
	).run()
	cursor_name = frappe.db.get_value(
		"Automation Round Robin Cursor", {"cursor_key": cursor_key}, "name", for_update=True
	)
	cursor = frappe.get_doc("Automation Round Robin Cursor", cursor_name)
	index = cint(cursor.next_index) % len(users)
	cursor.next_index = cint(cursor.next_index) + 1
	cursor.member_hash = hashlib.sha256(canonical_json(users).encode()).hexdigest()
	cursor.save(ignore_permissions=True)
	return users[index]


def _reserve_drip_slot(run, node: dict, config: dict) -> dict:
	"""Reserve one durable, transaction-locked batch slot for this node."""
	cursor_key = hashlib.sha256(f"{run.workflow_version}\0{node['id']}\0drip".encode()).hexdigest()
	cursor_table = frappe.qb.DocType("Automation Drip Cursor")
	now = now_datetime()
	(
		frappe.qb.into(cursor_table)
		.columns(
			cursor_table.name,
			cursor_table.creation,
			cursor_table.modified,
			cursor_table.modified_by,
			cursor_table.owner,
			cursor_table.docstatus,
			cursor_table.idx,
			cursor_table.cursor_key,
			cursor_table.workflow_version,
			cursor_table.node_id,
			cursor_table.window_start,
			cursor_table.issued,
		)
		.insert(
			frappe.generate_hash(length=10), now, now, frappe.session.user, frappe.session.user,
			0, 0, cursor_key, run.workflow_version, node["id"], now, 0,
		)
		.on_duplicate_key_update(cursor_table.cursor_key, cursor_key)
	).run()
	cursor_name = frappe.db.get_value("Automation Drip Cursor", {"cursor_key": cursor_key}, "name", for_update=True)
	cursor = frappe.get_doc("Automation Drip Cursor", cursor_name)
	window_start = frappe.utils.get_datetime(cursor.window_start)
	batch_size = cint(config.get("batch_size"))
	interval = cint(config.get("interval_seconds"))
	if window_start <= now and (now - window_start).total_seconds() >= interval:
		window_start = now
		cursor.issued = 0
	if cint(cursor.issued) >= batch_size:
		window_start = max(window_start, now)
		window_start = add_to_date(window_start, seconds=interval)
		cursor.issued = 0
	cursor.window_start = window_start
	cursor.issued = cint(cursor.issued) + 1
	position = cint(cursor.issued)
	cursor.save(ignore_permissions=True)
	return {"due_at": str(window_start), "batch_size": batch_size, "position": position, "released": window_start <= now}


def _enabled_user_names(identifiers: list[str]) -> list[str]:
	users: list[str] = []
	seen: set[str] = set()
	for identifier in identifiers:
		identifier = str(identifier or "").strip()
		if not identifier:
			continue
		row = frappe.db.get_value("User", identifier, ["name", "enabled"], as_dict=True)
		if not row:
			row = frappe.db.get_value("User", {"email": identifier}, ["name", "enabled"], as_dict=True)
		if row and row.enabled and row.name not in seen:
			seen.add(row.name)
			users.append(row.name)
	return users


def _claim_effect(run, token, node, request_payload: dict) -> tuple[Any, bool]:
	key = _effect_key(run.name, node["id"], cint(token.occurrence))
	name = frappe.db.get_value("Automation Effect Ledger", {"effect_key": key}, "name", for_update=True)
	if name:
		ledger = frappe.get_doc("Automation Effect Ledger", name)
		if ledger.status == "COMPLETED":
			return ledger, True
		if ledger.request_hash != frappe.utils.sha256_hash(canonical_json(request_payload)):
			raise AutomationError(_("The action request changed for an existing effect key."))
		if ledger.status == "FAILED":
			# A failed effect is safe to retry because the provider did not accept
			# responsibility for delivery. Re-arm the durable ledger before the
			# worker is enqueued; UNKNOWN_COMMIT deliberately remains blocked.
			ledger.status = "STARTED"
			ledger.result_json = None
			ledger.completed_at = None
			ledger.save(ignore_permissions=True)
		return ledger, False
	ledger = frappe.get_doc(
		{
			"doctype": "Automation Effect Ledger",
			"effect_key": key,
			"request_hash": frappe.utils.sha256_hash(canonical_json(request_payload)),
			"status": "STARTED",
			"run": run.name,
			"node_id": node["id"],
		}
	).insert(ignore_permissions=True)
	return ledger, False


def _execute_action(run, token, node, record, value_record, outputs: dict[str, Any]) -> dict:
	config = node.get("config") or {}
	request_payload = {"type": node["type"], "type_version": cint(node.get("type_version") or 1), "config": config, "record_key": run.record_key}
	ledger, completed = _claim_effect(run, token, node, request_payload)
	if completed:
		return json.loads(ledger.result_json or "{}")
	node_type = node["type"]
	if node_type == "action.update_record":
		values = _assignments(config, record=value_record, outputs=outputs)
		if any(_table_multiselect_definition(record.doctype, fieldname) for fieldname in values):
			record = frappe.get_doc(record.doctype, record.name, for_update=True)
		record.check_permission("write")
		for fieldname, value in values.items():
			assert_field_access(
				record.doctype,
				fieldname,
				permission_type="write",
				user=frappe.session.user,
				capability=("assignment_scalar", "assignment_collection"),
			)
			record.set(fieldname, _coerce_assignment_value(record.doctype, fieldname, value))
		record.save()
		result = {"doctype": record.doctype, "name": record.name, "updated_fields": sorted(values)}
	elif node_type == "action.numeric_adjust":
		# Serialize adjustments from different workflow runs so none overwrite a
		# value read concurrently by another worker.
		record = frappe.get_doc(record.doctype, record.name, for_update=True)
		record.check_permission("write")
		fieldname = str(config.get("field") or "")
		if not fieldname:
			raise AutomationError(_("Numeric adjust requires a field."))
		assert_field_access(record.doctype, fieldname, permission_type="write", user=frappe.session.user, capability="assignment_scalar")
		amount = frappe.utils.flt(config.get("amount") or 0)
		operation = str(config.get("operation") or "add")
		current = frappe.utils.flt(record.get(fieldname))
		new_value = _calculate_numeric_adjustment(current, operation, amount)
		record.set(fieldname, new_value)
		record.save()
		result = {"doctype": record.doctype, "name": record.name, "field": fieldname, "previous": current, "new_value": new_value}
	elif node_type == "action.delete_record":
		record.check_permission("delete")
		frappe.delete_doc(record.doctype, record.name, ignore_permissions=False)
		result = {"doctype": record.doctype, "name": record.name, "deleted": True}
	elif node_type == "action.create_record":
		target_doctype = config.get("target_doctype")
		if not is_eligible_doctype(target_doctype, permission_type="create", user=frappe.session.user):
			raise AutomationError(_("Execution user cannot create the configured DocType."))
		values = _assignments(config, record=value_record, outputs=outputs)
		for fieldname, value in list(values.items()):
			assert_field_access(
				target_doctype,
				fieldname,
				permission_type="create",
				user=frappe.session.user,
				capability=("assignment_scalar", "assignment_collection"),
			)
			values[fieldname] = _coerce_assignment_value(target_doctype, fieldname, value)
		target = frappe.get_doc({"doctype": target_doctype, **values}).insert()
		result = {"doctype": target.doctype, "name": target.name}
	elif node_type == "action.manage_association":
		record.check_permission("write")
		target_doctype = str(config.get("target_doctype") or "")
		target_name = str(config.get("target_name") or "")
		link_field = str(config.get("link_field") or "")
		operation = config.get("operation") or "link"
		assert_field_access(record.doctype, link_field, permission_type="write", user=frappe.session.user, capability="assignment_scalar")
		field = frappe.get_meta(record.doctype).get_field(link_field)
		if not field or field.fieldtype != "Link" or field.options != target_doctype:
			raise AutomationError(_("Association field must link to the configured target DocType."))
		if operation == "link":
			target = frappe.get_doc(target_doctype, target_name)
			target.check_permission("read")
			record.set(link_field, target_name)
			record.save()
		else:
			if record.get(link_field) == target_name:
				record.set(link_field, None)
				record.save()
		result = {"doctype": record.doctype, "name": record.name, "operation": operation, "target_name": target_name}
	elif node_type == "action.round_robin":
		record.check_permission("read")
		group = str(config.get("group") or "").strip()
		if not group:
			raise AutomationError(_("Round robin assignment requires a group to be configured."))
		# Resolve user pool: try Frappe User Group first, then comma-separated email list
		users: list[str] = []
		if frappe.db.exists("User Group", group):
			members = [
				row.user
				for row in frappe.get_list(
					"User Group Member",
					filters={"parent": group},
					fields=["user"],
					ignore_permissions=True,
					order_by="idx asc",
					limit=0,
				)
				if row.user
			]
			users = _enabled_user_names(members)
		else:
			# Treat as comma-separated list of user emails
			candidates = [u.strip() for u in group.replace(";", ",").split(",") if u.strip()]
			users = _enabled_user_names(candidates)
		if not users:
			raise AutomationError(_("Round robin group '{0}' has no enabled members.").format(group))
		if cint(node.get("type_version") or 1) >= 2:
			assigned_user = _next_round_robin_member(run, node, users)
		else:
			idx = int(hashlib.sha1(record.name.encode()).hexdigest(), 16) % len(users)
			assigned_user = users[idx]
		
		add_assignment(
			{
				"assign_to": json.dumps([assigned_user]),
				"doctype": record.doctype,
				"name": record.name,
				"description": _("Round robin assignment from workflow"),
			},
			ignore_permissions=False,
		)
		result = {"doctype": record.doctype, "name": record.name, "assigned_to": assigned_user, "group": group, "assignment_version": cint(node.get("type_version") or 1)}
	elif node_type == "action.create_todo":
		record.check_permission("read")
		assignments = add_assignment(
			{
				"assign_to": json.dumps([config.get("allocated_to")]),
				"doctype": record.doctype,
				"name": record.name,
				"description": config.get("description") or _("Automation task for {0}").format(record.name),
				"priority": config.get("priority") or "Medium",
				"date": config.get("date"),
			},
			ignore_permissions=False,
		)
		created_todo = next(
			(
				row
				for row in (assignments or [])
				if row.get("owner") == config.get("allocated_to") and row.get("name")
			),
			None,
		)
		if not created_todo:
			raise AutomationError(_("Frappe did not return the created ToDo."))
		result = {
			"doctype": "ToDo",
			"name": created_todo.name,
			"allocated_to": config.get("allocated_to"),
		}
	elif node_type == "action.add_comment":
		record.check_permission("write")
		comment = record.add_comment("Comment", text=str(config.get("content") or ""))
		result = {"comment": comment.name if comment else None}
	elif node_type == "action.create_note":
		record.check_permission("read")
		note = frappe.get_doc(
			{
				"doctype": "Note",
				"title": str(config.get("title") or "")[:140],
				"content": f"{str(config.get('content') or '')}<p><a href=\"/app/{frappe.scrub(record.doctype).replace('_', '-')}/{record.name}\">{record.doctype} {record.name}</a></p>",
			}
		).insert()
		result = {"note": note.name}
	elif node_type == "action.copy_record":
		record.check_permission("read")
		if not frappe.has_permission(record.doctype, ptype="create"):
			raise frappe.PermissionError
		copied = frappe.copy_doc(record)
		copied.flags.ignore_links = False
		copied.insert()
		result = {"doctype": copied.doctype, "name": copied.name}
	elif node_type == "action.merge_contact":
		if record.doctype != "Contact":
			raise AutomationError(_("Merge contact can only run in a Contact workflow."))
		record.check_permission("write")
		fields = [str(field) for field in config.get("match_fields") or []]
		predicates = [{field: value_record.get(field)} for field in fields if value_record.get(field) not in (None, "")]
		if not predicates or (config.get("match_mode", "all") == "all" and len(predicates) != len(fields)):
			raise AutomationError(_("The enrolled Contact has no complete merge identity."))
		filters = {"name": ("!=", record.name)}
		or_filters = None
		if config.get("match_mode", "all") == "all":
			for predicate in predicates:
				filters.update(predicate)
		else:
			or_filters = predicates
		matches = frappe.get_all("Contact", filters=filters, or_filters=or_filters, pluck="name", order_by="creation asc", limit=2)
		if not matches:
			raise AutomationError(_("No canonical Contact matches the configured identity fields."))
		if len(matches) > 1:
			raise AutomationError(_("More than one canonical Contact matches; resolve ambiguity before merging."))
		canonical = matches[0]
		frappe.get_doc("Contact", canonical).check_permission("write")
		frappe.rename_doc("Contact", record.name, canonical, merge=True)
		result = {"canonical_contact": canonical, "merged_contact": record.name, "matched_fields": [next(iter(item)) for item in predicates], "deleted": True}
	elif node_type == "action.unassign_record":
		record.check_permission("write")
		open_count = frappe.db.count("ToDo", {"reference_type": record.doctype, "reference_name": record.name, "status": "Open"})
		close_all_assignments(record.doctype, record.name)
		result = {"closed_assignments": open_count}
	elif node_type == "action.verify_email":
		record.check_permission("read")
		email = str(resolve_value(config.get("email"), record=value_record, outputs=outputs) or "").strip()
		valid = bool(validate_email_address(email, throw=False))
		result = {"email": email, "valid": valid, "reason": None if valid else "INVALID_FORMAT"}
	elif node_type == "action.mark_communications_read":
		record.check_permission("write")
		updated = frappe.db.count("Communication", {"reference_doctype": record.doctype, "reference_name": record.name, "sent_or_received": "Received", "seen": 0})
		frappe.db.set_value("Communication", {"reference_doctype": record.doctype, "reference_name": record.name, "sent_or_received": "Received", "seen": 0}, {"seen": 1, "unread_notification_sent": 1}, update_modified=False)
		result = {"updated": updated}
	elif node_type == "action.remove_from_workflow":
		record.check_permission("read")
		target = str(config.get("target_workflow") or "current")
		target_workflow = run.workflow if target == "current" else target
		if not frappe.db.exists("Automation Workflow", {"name": target_workflow, "primary_doctype": record.doctype}):
			raise AutomationError(_("Target workflow does not exist for this record type."))
		other_runs = frappe.get_all(
			"Automation Run",
			filters={"workflow": target_workflow, "record_doctype": record.doctype, "record_name": record.name, "status": ["not in", list(RUN_TERMINAL_STATUSES)], "name": ("!=", run.name)},
			pluck="name",
			limit=500,
		)
		for other_run in other_runs:
			frappe.db.set_value("Automation Timer", {"run": other_run, "status": "ACTIVE"}, "status", "CANCELLED", update_modified=False)
			frappe.db.set_value("Automation Run Token", {"run": other_run, "status": ["not in", list(TOKEN_TERMINAL_STATUSES) + ["RUNNING"]]}, "status", "CANCELLED", update_modified=False)
			frappe.db.set_value("Automation Run", other_run, {"status": "CANCELLED", "completed_at": now_datetime(), "error_code": "REMOVED_BY_WORKFLOW"}, update_modified=False)
		result = {"cancelled_runs": len(other_runs), "target_workflow": target_workflow, "terminate_path": target_workflow == run.workflow}
	elif node_type == "action.complete_goal":
		record.check_permission("read")
		result = {"goal": str(config.get("goal") or "Goal reached")[:140], "terminate_path": True}
	elif node_type == "action.go_to":
		record.check_permission("read")
		result = {"target_node_id": str(config.get("target_node_id") or "")}
	elif node_type == "action.notify_user":
		record.check_permission("read")
		audience = str(config.get("audience") or "specific")
		if audience == "assigned":
			recipients = frappe.get_all("ToDo", filters={"reference_type": record.doctype, "reference_name": record.name, "status": "Open"}, pluck="allocated_to", limit=500)
		elif audience == "all":
			recipients = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name", limit=500)
		else:
			recipients = [config.get("for_user")]
		recipients = _enabled_user_names(recipients)
		if not recipients:
			raise AutomationError(_("The notification audience contains no enabled users."))
		sent = []
		for recipient in recipients:
			if enqueue_notification_for_user(recipient, {
				"type": "Alert",
				"subject": str(config.get("subject") or _("Workflow notification")),
				"email_content": str(config.get("message") or ""),
				"document_type": record.doctype,
				"document_name": record.name,
				"from_user": frappe.session.user,
			}):
				sent.append(recipient)
		if not sent:
			raise AutomationError(_("Notification recipients are disabled, missing, or have no email address."))
		result = {"for_user": sent[0] if len(sent) == 1 else None, "recipients": sent, "recipient_count": len(sent)}
	else:
		raise AutomationError(_("Unsupported action node."))
	ledger.status = "COMPLETED"
	ledger.result_json = json.dumps(result, default=str)
	ledger.completed_at = now_datetime()
	ledger.save(ignore_permissions=True)
	return result


def _schedule_external_action(run, token, node) -> dict:
	request_payload = {"type": node["type"], "config": node.get("config") or {}, "record_key": run.record_key}
	ledger, completed = _claim_effect(run, token, node, request_payload)
	if completed:
		return {"status": "COMPLETE", "output": json.loads(ledger.result_json or "{}")}
	if ledger.status == "UNKNOWN_COMMIT":
		raise AutomationError(_("External effect has an unknown delivery state and requires operator reconciliation."))
	frappe.enqueue(
		"finbyzai.workflow_builder.engine.execute_external_effect",
		ledger_name=ledger.name,
		token_name=token.name,
		queue="default",
		enqueue_after_commit=True,
		job_id=f"automation-external-{ledger.name}",
		deduplicate=True,
	)
	return {"status": "WAIT_EXTERNAL", "output": {"effect": ledger.name}}


def _business_hours_state(config: dict, server_now: datetime | None = None) -> dict:
	tz_name = str(config.get("timezone") or "").strip()
	try:
		tz = ZoneInfo(tz_name)
	except ZoneInfoNotFoundError as exc:
		raise AutomationError(_("Business-hours timezone is invalid.")) from exc
	system_tz = ZoneInfo(get_system_timezone())
	server_now = server_now or now_datetime()
	local_now = server_now.replace(tzinfo=system_tz).astimezone(tz)
	calendar = str(config.get("calendar") or "").strip()
	if calendar and not frappe.db.exists("Holiday List", calendar):
		raise AutomationError(_("Business-hours Holiday List does not exist."))
	from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday

	weekdays = {cint(day) for day in config.get("weekdays", [0, 1, 2, 3, 4])}
	try:
		start_hour, start_minute = (int(part) for part in str(config.get("start_time") or "09:00").split(":"))
		end_hour, end_minute = (int(part) for part in str(config.get("end_time") or "17:00").split(":"))
	except (TypeError, ValueError) as exc:
		raise AutomationError(_("Business-hours start or end time is invalid.")) from exc
	start_minutes = start_hour * 60 + start_minute
	end_minutes = end_hour * 60 + end_minute
	if not weekdays or start_minutes >= end_minutes or min(start_minutes, end_minutes) < 0 or max(start_minutes, end_minutes) >= 24 * 60:
		raise AutomationError(_("Business-hours configuration is invalid."))

	def is_workday(candidate) -> bool:
		return candidate.weekday() in weekdays and not bool(calendar and is_holiday(calendar, candidate))

	local_minutes = local_now.hour * 60 + local_now.minute
	if is_workday(local_now.date()) and start_minutes <= local_minutes < end_minutes:
		return {"released": True, "due_at": str(server_now), "timezone": tz_name}
	if is_workday(local_now.date()) and local_minutes < start_minutes:
		next_local = local_now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
	else:
		next_local = (local_now + timedelta(days=1)).replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
	for _day in range(366):
		if is_workday(next_local.date()):
			break
		next_local += timedelta(days=1)
	else:
		raise AutomationError(_("Business-hours calendar has no working day in the next year."))
	due_at = next_local.astimezone(system_tz).replace(tzinfo=None)
	return {"released": False, "due_at": str(due_at), "timezone": tz_name}


def _hold_for_execution_window(run, token, node: dict, graph: dict, settings: dict) -> bool:
	"""Durably postpone action nodes outside the workflow-wide execution window."""
	window = settings.get("execution_window")
	if not node.get("type", "").startswith("action.") or not isinstance(window, dict) or not window.get("enabled"):
		return False
	current = json.loads(token.output_json or "{}")
	if current.get("execution_window") and current.get("released"):
		return False
	state = _business_hours_state(window)
	if state["released"]:
		return False
	frappe.get_doc(
		{
			"doctype": "Automation Timer",
			"run": run.name,
			"token": token.name,
			"node_id": node["id"],
			"timer_type": "DELAY",
			"due_at": state["due_at"],
			"status": "ACTIVE",
		}
	).insert(ignore_permissions=True)
	_finish_or_continue(
		run,
		token,
		graph,
		{"status": "WAIT_TIMER", "output": {**state, "execution_window": True}},
	)
	return True


def _execute_node(run, token, node, record, value_record, outputs: dict[str, Any]) -> dict:
	node_type = node["type"]
	config = node.get("config") or {}
	if node_type.startswith("trigger.") or node_type == "end.complete":
		return {"status": "COMPLETE", "output": {}}
	if node_type == "condition.if_else":
		if cint(node.get("type_version") or 1) >= 2:
			matched_handle = "none"
			branch_name = "None"
			for branch in config.get("branches") or []:
				if not isinstance(branch, dict) or not evaluate_expression(branch.get("condition"), value_record):
					continue
				matched_handle = str(branch.get("handle") or "")
				branch_name = str(branch.get("name") or matched_handle)
				break
			matched = matched_handle != "none"
			return {
				"status": "COMPLETE",
				"output": {"matched": matched, "selected_handle": matched_handle, "branch_name": branch_name},
				"handle": matched_handle,
			}
		matched = evaluate_expression(config.get("condition"), value_record)
		handle = "true" if matched else "false"
		return {
			"status": "COMPLETE",
			"output": {"matched": matched, "selected_handle": handle, "branch_name": "Yes" if matched else "No"},
			"handle": handle,
		}
	if node_type == "condition.random_split":
		branches = [branch for branch in config.get("branches") or [] if isinstance(branch, dict)]
		seed = f"{run.name}\0{node['id']}\0{getattr(token, 'occurrence', 0)}".encode()
		bucket = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") / 2**64 * 100
		cumulative = 0.0
		selected = branches[-1] if branches else {}
		for branch in branches:
			cumulative += float(branch.get("percentage") or 0)
			if bucket < cumulative:
				selected = branch
				break
		handle = str(selected.get("handle") or "")
		return {
			"status": "COMPLETE",
			"output": {"selected_handle": handle, "branch_name": str(selected.get("name") or handle), "bucket": round(bucket, 6)},
			"handle": handle,
		}
	if node_type == "condition.switch":
		field = str(config.get("field") or "")
		raw_value = value_record.get(field)
		value = "" if raw_value is None else str(raw_value)
		matched_handle = "default"
		for case in config.get("cases") or []:
			if str(case.get("value") or "") == value:
				matched_handle = str(case.get("handle") or case.get("value") or "")
				break
		return {"status": "COMPLETE", "output": {"value": value, "matched_handle": matched_handle}, "handle": matched_handle}
	if node_type == "condition.deduplicate":
		exists, matched_fields = _find_duplicate(node, record, value_record)
		is_duplicate = bool(exists)
		return {
			"status": "COMPLETE",
			"output": {"duplicate_name": exists if is_duplicate else None, "is_duplicate": is_duplicate, "matched_fields": matched_fields},
			"handle": "duplicate" if is_duplicate else "unique",
		}
	if node_type == "delay.fixed":
		current = json.loads(token.output_json or "{}")
		if current.get("released"):
			return {"status": "COMPLETE", "output": current}
		due_at = add_to_date(now_datetime(), seconds=cint(config.get("seconds")))
		frappe.get_doc(
			{
				"doctype": "Automation Timer",
				"run": run.name,
				"token": token.name,
				"node_id": node["id"],
				"timer_type": "DELAY",
				"due_at": due_at,
				"status": "ACTIVE",
			}
		).insert(ignore_permissions=True)
		return {"status": "WAIT_TIMER", "output": {"due_at": str(due_at)}}
	if node_type == "delay.drip":
		current = json.loads(token.output_json or "{}")
		if current.get("released"):
			return {"status": "COMPLETE", "output": current}
		state = _reserve_drip_slot(run, node, config)
		if state["released"]:
			return {"status": "COMPLETE", "output": state}
		frappe.get_doc({"doctype": "Automation Timer", "run": run.name, "token": token.name, "node_id": node["id"], "timer_type": "DELAY", "due_at": state["due_at"], "status": "ACTIVE"}).insert(ignore_permissions=True)
		return {"status": "WAIT_TIMER", "output": state}
	if node_type == "delay.until_date":
		current = json.loads(token.output_json or "{}")
		if current.get("released"):
			return {"status": "COMPLETE", "output": current}
		mode = str(config.get("mode") or ("literal" if config.get("datetime") else "field"))
		if mode == "literal":
			due_value = config.get("datetime")
			source = {"mode": "literal"}
		else:
			fieldname = str(config.get("field") or "")
			assert_field_access(record.doctype, fieldname, permission_type="read", user=frappe.session.user, capability="scalar_read")
			due_value = value_record.get(fieldname)
			source = {"mode": "field", "field": fieldname}
			if due_value in (None, ""):
				raise AutomationError(_("Wait-until field {0} has no date value.").format(fieldname))
		due_at = frappe.utils.get_datetime(due_value)
		if due_at <= now_datetime():
			return {"status": "COMPLETE", "output": {"due_at": str(due_at), "released": True, **source}}
		frappe.get_doc({"doctype": "Automation Timer", "run": run.name, "token": token.name, "node_id": node["id"], "timer_type": "DELAY", "due_at": due_at, "status": "ACTIVE"}).insert(ignore_permissions=True)
		return {"status": "WAIT_TIMER", "output": {"due_at": str(due_at), **source}}
	if node_type == "delay.until_event":
		current = json.loads(token.output_json or "{}")
		if current.get("released"):
			matched_handle = current.get("matched_handle", "timeout")
			handle = matched_handle if cint(node.get("type_version") or 1) < 2 or cint(config.get("branch_on_timeout")) else "default"
			return {"status": "COMPLETE", "output": current, "handle": handle}
		data_source = event_wait_data_source(config)
		if data_source == "action_output":
			event_source = config.get("event_source")
			event_source_id = resolve_value(event_source, record=value_record, outputs=outputs)
			event_source_doctype = resolve_value(
				config.get("event_source_doctype"), record=value_record, outputs=outputs
			) or ("Email Queue" if (event_source or {}).get("path") == "email_queue" else None)
			if not event_source_id or not event_source_doctype:
				raise AutomationError(_("The selected earlier action did not produce a usable event source."))
			if not frappe.db.exists(str(event_source_doctype), str(event_source_id)):
				raise AutomationError(_("The record produced by the selected earlier action no longer exists."))
			event_source_type = "ACTION_EMAIL" if event_source_doctype == "Email Queue" else "ACTION_RECORD"
		else:
			event_source_id = run.record_name
			event_source_doctype = run.record_doctype
			event_source_type = "ENROLLED_RECORD"
		timeout_mode = event_wait_timeout_mode(config)
		wait_indefinitely = timeout_mode == "indefinite"
		due_at = None if wait_indefinitely else add_to_date(
			now_datetime(), seconds=cint(config.get("timeout_seconds") or 86400)
		)
		frappe.get_doc(
			{
				"doctype": "Automation Timer",
				"run": run.name,
				"token": token.name,
				"node_id": node["id"],
				"timer_type": "EVENT_WAIT" if wait_indefinitely else "TIMEOUT",
				"event_topic": str(config.get("event_topic") or "").strip(),
				"record_doctype": run.record_doctype,
				"record_name": run.record_name,
				"source_type": event_source_type,
				"source_doctype": event_source_doctype,
				"source_name": event_source_id,
				"due_at": due_at,
				"status": "ACTIVE",
			}
		).insert(ignore_permissions=True)
		return {
			"status": "WAIT_TIMER",
			"output": {
				"due_at": str(due_at) if due_at else None,
				"event_source_id": event_source_id,
				"event_source_doctype": event_source_doctype,
				"event_source_type": event_source_type,
				"wait_indefinitely": wait_indefinitely,
			},
		}
	if node_type == "delay.business_hours":
		current = json.loads(token.output_json or "{}")
		if current.get("released"):
			return {"status": "COMPLETE", "output": current}
		state = _business_hours_state(config)
		if state["released"]:
			return {"status": "COMPLETE", "output": state}
		frappe.get_doc({"doctype": "Automation Timer", "run": run.name, "token": token.name, "node_id": node["id"], "timer_type": "DELAY", "due_at": state["due_at"], "status": "ACTIVE"}).insert(ignore_permissions=True)
		return {"status": "WAIT_TIMER", "output": state}
	if node_type == "transform.value":
		values = [resolve_value(value, record=value_record, outputs=outputs) for value in (config.get("values") or [])]
		value = _transform_output(config, values, seed=f"{run.name}\0{node['id']}\0{getattr(token, 'occurrence', 0)}")
		return {"status": "COMPLETE", "output": {"value": value}}
	if node_type == "transform.associated_record":
		reference_field = str(config.get("reference_field") or "")
		fetch_field = str(config.get("fetch_field") or "")
		ref_value = value_record.get(reference_field)
		if not ref_value:
			return {"status": "COMPLETE", "output": {"value": None}}
		meta = frappe.get_meta(record.doctype)
		df = meta.get_field(reference_field)
		if not df or df.fieldtype not in {"Link", "Dynamic Link"}:
			raise AutomationError(_("Reference field must be a Link field."))
		target_doctype = df.options
		if df.fieldtype == "Dynamic Link":
			target_doctype = value_record.get(df.options)
		assert_field_access(target_doctype, fetch_field, permission_type="read", user=frappe.session.user, capability="scalar_read")
		linked_record = frappe.get_doc(target_doctype, ref_value)
		linked_record.check_permission("read")
		fetched_value = linked_record.get(fetch_field)
		return {"status": "COMPLETE", "output": {"value": fetched_value, "linked_name": ref_value}}
	if node_type == "transform.child_records":
		child_table_field = str(config.get("child_table_field") or "")
		fetch_field = str(config.get("fetch_field") or "")
		field = frappe.get_meta(record.doctype).get_field(child_table_field)
		if not field or field.fieldtype not in {"Table", "Table MultiSelect"} or not field.options:
			raise AutomationError(_("Child-record source must be a child table field."))
		assert_field_access(
			field.options,
			fetch_field,
			permission_type="read",
			user=frappe.session.user,
			parenttype=record.doctype,
			capability="scalar_read",
		)
		children = value_record.get(child_table_field) or []
		if field.fieldtype == "Table MultiSelect" and children and isinstance(children[0], str):
			definition = _table_multiselect_definition(record.doctype, child_table_field)
			link_field = definition[1].fieldname if definition else None
			if fetch_field == link_field:
				values = list(children)
			else:
				snapshot_rows = (value_record.get("__automation_child_records__") or {}).get(child_table_field, [])
				values = [child.get(fetch_field) for child in snapshot_rows]
		else:
			values = [child.get(fetch_field) for child in children]
		return {"status": "COMPLETE", "output": {"values": values, "count": len(values)}}
	if node_type == "action.call_subflow":
		subflow_id = config.get("subflow_id")
		if not subflow_id:
			raise AutomationError(_("Subflow workflow ID is required."))
		wait_for_completion = bool(cint(config.get("wait_for_completion", 1)))
		# Propagate recursion depth so the MAX_RECURSION_DEPTH guard in enroll() fires
		# correctly for subflow chains (subflow-of-subflow-of-...). Without this the
		# guard always sees depth=0 and allows unbounded recursion.
		next_recursion_depth = cint(run.recursion_depth) + 1
		if wait_for_completion:
			request_payload = {"type": node["type"], "subflow_id": subflow_id}
			ledger, completed = _claim_effect(run, token, node, request_payload)
			if completed:
				result = json.loads(ledger.result_json or "{}")
				return {"status": "COMPLETE", "output": {"run_id": result.get("run_id") or result.get("subflow_run"), "status": result.get("status", "COMPLETED")}}
			subflow_run = enroll(
				subflow_id,
				record.doctype,
				record.name,
				source="SUBFLOW",
				occurrence_key=ledger.name,
				causation_id=ledger.name,
				recursion_depth=next_recursion_depth,
			)
			if not subflow_run:
				raise AutomationError(_("Subflow failed to enroll. Ensure it is active and matches the DocType."))
			return {"status": "WAIT_EXTERNAL", "output": {"effect": ledger.name, "run_id": subflow_run, "status": "WAITING"}}
		else:
			subflow_run = enroll(
				subflow_id,
				record.doctype,
				record.name,
				source="SUBFLOW",
				occurrence_key=f"{run.name}-{node['id']}",
				recursion_depth=next_recursion_depth,
			)
			return {"status": "COMPLETE", "output": {"run_id": subflow_run, "status": "QUEUED" if subflow_run else "NOT_ENROLLED"}}
	if node_type in EXTERNAL_ACTION_NODE_TYPES:
		return _schedule_external_action(run, token, node)
	if node_type in ACTION_NODE_TYPES:
		return {"status": "COMPLETE", "output": _execute_action(run, token, node, record, value_record, outputs)}

	from .registry import get_node_definition
	definition = get_node_definition(node_type)
	if definition and definition.get("executor"):
		executor = frappe.get_attr(definition["executor"])
		result = executor(run, token, node, record, value_record, outputs)
		if isinstance(result, dict) and "status" in result and "output" in result:
			return result
		return {"status": "COMPLETE", "output": result or {}}

	raise AutomationError(_("Unsupported node type {0}.").format(node_type))


def _next_nodes(graph: dict, node_id: str, handle: str | None = None) -> list[str]:
	rows = []
	for edge in graph.get("edges") or []:
		if edge.get("source") != node_id:
			continue
		if handle and edge.get("source_handle") != handle:
			continue
		rows.append(edge["target"])
	return rows


def _finish_or_continue(run, token, graph: dict, result: dict) -> None:
	if result["status"] in {"WAIT_TIMER", "WAIT_EXTERNAL"}:
		token.status = "WAITING"
		token.output_json = json.dumps(result.get("output") or {}, default=str)
		token.save(ignore_permissions=True)
		run.status = "WAITING"
		run.save(ignore_permissions=True)
		_append_event(run.name, "TIMER_CREATED" if result["status"] == "WAIT_TIMER" else "EXTERNAL_EFFECT_QUEUED", node_id=token.node_id, payload=result.get("output"))
		return
	token.status = "COMPLETED"
	token.output_json = json.dumps(result.get("output") or {}, default=str)
	token.completed_at = now_datetime()
	token.save(ignore_permissions=True)
	_append_event(run.name, "NODE_COMPLETED", node_id=token.node_id, payload=result.get("output"))
	record_deleted = bool((result.get("output") or {}).get("deleted"))
	terminate_path = bool((result.get("output") or {}).get("terminate_path"))
	go_to_target = str((result.get("output") or {}).get("target_node_id") or "").strip()
	next_nodes = [] if record_deleted or terminate_path else ([go_to_target] if go_to_target else _next_nodes(graph, token.node_id, result.get("handle")))
	if not next_nodes:
		run.status = "COMPLETED"
		run.completed_at = now_datetime()
		# The run intentionally remains an immutable audit trail after a delete
		# action removes its Dynamic Link target. Saving the terminal state must
		# therefore skip link existence validation for this one transition.
		run.flags.ignore_links = record_deleted
		run.save(ignore_permissions=True)
		increment_metric(run.workflow, run.workflow_version, "completed_runs")
		if run.started_at:
			increment_metric(run.workflow, run.workflow_version, "total_duration_seconds", max((run.completed_at - run.started_at).total_seconds(), 0))
		_append_event(run.name, "RUN_COMPLETED", node_id=token.node_id)
		_resolve_subflow_if_any(run)
		return
	run.status = "RUNNING"
	run.save(ignore_permissions=True)
	for node_id in next_nodes:
		existing = frappe.db.get_value(
			"Automation Run Token",
			{"run": run.name, "node_id": node_id, "occurrence": token.occurrence},
			"name",
		)
		if existing:
			continue
		next_token = frappe.get_doc(
			{
				"doctype": "Automation Run Token",
				"run": run.name,
				"node_id": node_id,
				"occurrence": token.occurrence,
				"status": "READY",
				"available_at": now_datetime(),
				"attempts": 0,
			}
		).insert(ignore_permissions=True)
		_queue_token(next_token.name)


def _is_transient_failure(exc: Exception) -> bool:
	if isinstance(exc, AutomationTransientError):
		return True
	if isinstance(exc, (frappe.QueryDeadlockError, frappe.QueryTimeoutError, ConnectionError, TimeoutError)):
		return True
	try:
		return bool(frappe.db.is_deadlocked(exc) or frappe.db.is_timedout(exc))
	except Exception:
		return False


def _handle_failure(run, token, attempt, exc: Exception) -> None:
	retryable = _is_transient_failure(exc)
	attempt.status = "FAILED"
	attempt.error_code = getattr(exc, "code", "WF_VALIDATION_ERROR")
	attempt.error_message = str(exc)[:2000]
	attempt.completed_at = now_datetime()
	attempt.save(ignore_permissions=True)
	attempts = cint(token.attempts)
	if retryable and attempts <= len(RETRY_DELAYS_SECONDS):
		delay = RETRY_DELAYS_SECONDS[attempts - 1]
		token.status = "WAITING"
		token.error_message = str(exc)[:2000]
		token.save(ignore_permissions=True)
		run.status = "WAITING"
		run.save(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Automation Timer",
				"run": run.name,
				"token": token.name,
				"node_id": token.node_id,
				"timer_type": "RETRY",
				"due_at": add_to_date(now_datetime(), seconds=delay),
				"status": "ACTIVE",
			}
		).insert(ignore_permissions=True)
		increment_metric(run.workflow, run.workflow_version, "retries")
		_append_event(run.name, "NODE_RETRY_SCHEDULED", node_id=token.node_id, payload={"delay_seconds": delay, "error": str(exc)})
		return
	token.status = "FAILED"
	token.error_message = str(exc)[:2000]
	token.save(ignore_permissions=True)
	run.status = "FAILED"
	run.error_code = getattr(exc, "code", "WF_VALIDATION_ERROR")
	run.error_message = str(exc)[:2000]
	run.completed_at = now_datetime()
	run.save(ignore_permissions=True)
	increment_metric(run.workflow, run.workflow_version, "failed_runs")
	increment_metric(run.workflow, run.workflow_version, "node_failures")
	record_incident(
		source_type="RUN", source_name=run.name, workflow=run.workflow, run=run.name,
		node_id=token.node_id, error_code=run.error_code, message=run.error_message, attempts=attempts,
	)
	_append_event(run.name, "NODE_FAILED", node_id=token.node_id, payload={"error": str(exc), "retryable": retryable})
	_resolve_subflow_if_any(run)


def _resolve_subflow_if_any(run) -> None:
	if not run.causation_id:
		return
	if run.status not in RUN_TERMINAL_STATUSES:
		return
	if not frappe.db.exists("Automation Effect Ledger", run.causation_id):
		return
	ledger = frappe.get_doc("Automation Effect Ledger", run.causation_id, for_update=True)
	if ledger.status != "STARTED":
		return
	token_name = frappe.db.get_value(
		"Automation Run Token",
		{"run": ledger.run, "node_id": ledger.node_id, "status": "WAITING"},
		"name",
		order_by="creation desc",
		for_update=True,
	)
	if not token_name:
		return
	parent_token = frappe.get_doc("Automation Run Token", token_name, for_update=True)
	parent_run = frappe.get_doc("Automation Run", parent_token.run, for_update=True)
	if parent_run.status in RUN_TERMINAL_STATUSES:
		ledger.status = "FAILED"
		ledger.result_json = json.dumps({"status": run.status, "run_id": run.name, "error": _("Parent run is already terminal.")})
		ledger.completed_at = now_datetime()
		ledger.save(ignore_permissions=True)
		return
	resolution = {"status": run.status, "run_id": run.name, "error": run.error_message}
	ledger.status = "COMPLETED" if run.status == "COMPLETED" else "FAILED"
	ledger.result_json = json.dumps(resolution, default=str)
	ledger.completed_at = now_datetime()
	ledger.save(ignore_permissions=True)
	attempt_name = frappe.db.get_value(
		"Automation Action Attempt",
		{"token": parent_token.name},
		"name",
		order_by="creation desc",
		for_update=True,
	)
	if not attempt_name:
		raise AutomationError(_("Waiting subflow parent has no action attempt."))
	parent_attempt = frappe.get_doc("Automation Action Attempt", attempt_name, for_update=True)
	if run.status == "COMPLETED":
		parent_attempt.status = "COMPLETED"
		parent_attempt.output_json = json.dumps(resolution, default=str)
		parent_attempt.completed_at = now_datetime()
		parent_attempt.save(ignore_permissions=True)
		parent_version = frappe.get_doc("Automation Workflow Version", parent_run.workflow_version)
		_finish_or_continue(parent_run, parent_token, _graph(parent_version), {"status": "COMPLETE", "output": resolution})
	else:
		message = _("Subflow {0} finished with status {1}: {2}").format(run.name, run.status, run.error_message or _("No error details"))
		_handle_failure(parent_run, parent_token, parent_attempt, AutomationError(message))


def _terminate_for_policy(run, token, *, status: str, event_type: str, reason_code: str) -> None:
	now = now_datetime()
	token.status = "COMPLETED" if status == "COMPLETED" else "CANCELLED"
	token.completed_at = now
	token.lease_owner = None
	token.lease_until = None
	token.save(ignore_permissions=True)
	frappe.db.set_value(
		"Automation Run Token",
		{"run": run.name, "name": ["!=", token.name], "status": ["not in", ["COMPLETED", "FAILED", "CANCELLED"]]},
		"status", "CANCELLED", update_modified=False,
	)
	frappe.db.set_value("Automation Timer", {"run": run.name, "status": "ACTIVE"}, "status", "CANCELLED", update_modified=False)
	run.status = status
	run.completed_at = now
	run.current_node_id = token.node_id
	run.save(ignore_permissions=True)
	increment_metric(run.workflow, run.workflow_version, "completed_runs" if status == "COMPLETED" else "cancelled_runs")
	if status == "COMPLETED" and run.started_at:
		increment_metric(run.workflow, run.workflow_version, "total_duration_seconds", max((now - run.started_at).total_seconds(), 0))
	_append_event(run.name, event_type, node_id=token.node_id, payload={"reason_code": reason_code})
	_resolve_subflow_if_any(run)


def _evaluate_run_policy(settings: dict, graph: dict, value_record) -> tuple[str, str]:
	goal = settings.get("goal_condition")
	if goal and evaluate_expression(goal, value_record):
		return "GOAL_MET", "GOAL_CONDITION_TRUE"
	if cint(settings.get("unenroll_when_ineligible")):
		eligibility = settings.get("eligibility_condition") or _trigger_condition(graph)
		if eligibility and not evaluate_expression(eligibility, value_record):
			return "ELIGIBILITY_LOST", "ELIGIBILITY_CONDITION_FALSE"
	return "NO_CHANGE", "POLICY_STILL_SATISFIED"


def _apply_run_policies(run, token, version, graph: dict, value_record) -> bool:
	settings = parse_object(version.settings_json or "{}", "workflow settings")
	outcome, reason_code = _evaluate_run_policy(settings, graph, value_record)
	if outcome == "GOAL_MET":
		_terminate_for_policy(run, token, status="COMPLETED", event_type="RUN_GOAL_MET", reason_code="GOAL_CONDITION_TRUE")
		return True
	if outcome == "ELIGIBILITY_LOST":
		_terminate_for_policy(run, token, status="CANCELLED", event_type="RUN_ELIGIBILITY_LOST", reason_code=reason_code)
		return True
	return False


def _terminate_run_from_record_event(
	run,
	*,
	outcome: str,
	reason_code: str,
	outbox_event: str,
	event_id: str,
	changed_fields: set[str],
) -> None:
	now = now_datetime()
	status = "COMPLETED" if outcome == "GOAL_MET" else "CANCELLED"
	frappe.db.set_value(
		"Automation Run Token",
		{"run": run.name, "status": ["not in", ["COMPLETED", "FAILED", "CANCELLED"]]},
		{
			"status": "CANCELLED",
			"completed_at": now,
			"lease_owner": None,
			"lease_until": None,
		},
		update_modified=False,
	)
	frappe.db.set_value(
		"Automation Action Attempt",
		{"run": run.name, "status": ["in", ["STARTED", "WAITING"]]},
		{
			"status": "CANCELLED",
			"completed_at": now,
			"error_code": "WF_POLICY_TERMINATED",
			"error_message": "Stopped after a relevant record change triggered lifecycle policy evaluation.",
		},
		update_modified=False,
	)
	frappe.db.set_value(
		"Automation Timer",
		{"run": run.name, "status": "ACTIVE"},
		"status",
		"CANCELLED",
		update_modified=False,
	)
	run.status = status
	run.completed_at = now
	run.error_code = None
	run.error_message = None
	run.save(ignore_permissions=True)
	increment_metric(run.workflow, run.workflow_version, "completed_runs" if status == "COMPLETED" else "cancelled_runs")
	if status == "COMPLETED" and run.started_at:
		increment_metric(run.workflow, run.workflow_version, "total_duration_seconds", max((now - run.started_at).total_seconds(), 0))
	_append_event(
		run.name,
		"RUN_GOAL_MET" if outcome == "GOAL_MET" else "RUN_ELIGIBILITY_LOST",
		node_id=run.current_node_id,
		payload={
			"reason_code": reason_code,
			"source": "RECORD_CHANGE",
			"outbox_event": outbox_event,
			"event_id": event_id,
			"changed_fields": sorted(changed_fields),
		},
	)


def reevaluate_active_run_policies(
	*,
	outbox_event: str,
	event_id: str,
	record_doctype: str,
	record_name: str,
	changed_fields: set[str],
) -> list[dict]:
	"""Apply current-value policies once per run/outbox event in the event worker transaction."""
	if not changed_fields or not frappe.db.table_exists("Automation Policy Evaluation"):
		return []
	results = []
	for candidate in _active_policy_candidates(record_doctype, record_name):
		dependencies = set(candidate["dependencies"])
		relevant = dependencies.intersection(changed_fields)
		if not relevant:
			continue
		run = frappe.get_doc("Automation Run", candidate["run"], for_update=True)
		existing = frappe.db.get_value(
			"Automation Policy Evaluation",
			{"run": run.name, "event_id": event_id},
			["name", "outcome", "reason_code"],
			as_dict=True,
		)
		if existing:
			results.append(
				{
					"run": run.name,
					"evaluation": existing.name,
					"outcome": existing.outcome,
					"reason_code": existing.reason_code,
					"deduplicated": True,
				}
			)
			continue
		if run.status in RUN_TERMINAL_STATUSES:
			continue
		if frappe.db.get_value("Automation Workflow", run.workflow, "status") != "ACTIVE":
			continue
		if not workflow_runtime_allowed(run.workflow):
			continue
		version = frappe.get_doc("Automation Workflow Version", run.workflow_version)
		if version.name != candidate["version"].name or version.workflow != run.workflow:
			raise AutomationError(_("Pinned workflow version changed during policy evaluation."))
		if not frappe.db.get_value("User", version.execution_user, "enabled"):
			raise AutomationError(_("Workflow execution user is disabled or missing."))
		if not is_eligible_doctype(run.record_doctype, permission_type="read", user=version.execution_user):
			raise AutomationError(_("Workflow execution user can no longer read the enrolled DocType."))
		settings = parse_object(version.settings_json or "{}", "workflow settings")
		graph = _graph(version)
		with _execution_identity(
			version.execution_user,
			{
				"trace_id": run.trace_id,
				"causation_id": event_id,
				"recursion_depth": cint(run.recursion_depth) + 1,
			},
		):
			record = frappe.get_doc(record_doctype, record_name)
			record.check_permission("read")
			outcome, reason_code = _evaluate_run_policy(settings, graph, record)
		if outcome in {"GOAL_MET", "ELIGIBILITY_LOST"}:
			_terminate_run_from_record_event(
				run,
				outcome=outcome,
				reason_code=reason_code,
				outbox_event=outbox_event,
				event_id=event_id,
				changed_fields=changed_fields,
			)
		evaluation = frappe.get_doc(
			{
				"doctype": "Automation Policy Evaluation",
				"run": run.name,
				"workflow": run.workflow,
				"workflow_version": run.workflow_version,
				"outbox_event": outbox_event,
				"event_id": event_id,
				"record_doctype": record_doctype,
				"record_name": record_name,
				"changed_fields_json": json.dumps(sorted(changed_fields)),
				"outcome": outcome,
				"reason_code": reason_code,
				"evaluated_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
		results.append(
			{
				"run": run.name,
				"evaluation": evaluation.name,
				"outcome": outcome,
				"reason_code": reason_code,
				"relevant_fields": sorted(relevant),
			}
		)
	return results


def _fail_unexecutable_token(run, token, *, error_code: str, message: str) -> None:
	"""Terminally quarantine a token whose pinned runtime dependencies are gone or invalid."""
	completed_at = now_datetime()
	frappe.db.set_value(
		"Automation Run Token",
		token.name,
		{
			"status": "FAILED",
			"lease_owner": None,
			"lease_until": None,
			"error_message": str(message)[:2000],
			"completed_at": completed_at,
		},
		update_modified=False,
	)
	frappe.db.set_value(
		"Automation Run",
		run.name,
		{
			"status": "FAILED",
			"error_code": error_code,
			"error_message": str(message)[:2000],
			"completed_at": completed_at,
		},
		update_modified=False,
	)
	_append_event(
		run.name,
		"RUN_FAILED",
		node_id=token.node_id,
		payload={"error_code": error_code, "error": str(message), "phase": "runtime_preflight"},
	)
	if frappe.db.exists("Automation Workflow", run.workflow):
		record_incident(
			source_type="RUN",
			source_name=run.name,
			workflow=run.workflow,
			run=run.name,
			node_id=token.node_id,
			error_code=error_code,
			message=str(message),
			attempts=cint(token.attempts),
		)


def execute_token(token_name: str) -> None:
	_assert_worker_execution()
	run_name = frappe.db.get_value("Automation Run Token", token_name, "run")
	if not run_name:
		return
	if not frappe.db.exists("Automation Run", run_name):
		frappe.db.set_value(
			"Automation Run Token",
			token_name,
			{
				"status": "CANCELLED",
				"lease_owner": None,
				"lease_until": None,
				"error_message": "Parent automation run no longer exists.",
				"completed_at": now_datetime(),
			},
			update_modified=False,
		)
		return
	run = frappe.get_doc("Automation Run", run_name, for_update=True)
	token = frappe.get_doc("Automation Run Token", token_name, for_update=True)
	if token.status != "READY":
		return
	if run.status in RUN_TERMINAL_STATUSES:
		frappe.db.set_value(
			"Automation Run Token",
			token.name,
			{
				"status": "CANCELLED",
				"lease_owner": None,
				"lease_until": None,
				"error_message": f"Parent run is already {run.status}.",
				"completed_at": now_datetime(),
			},
			update_modified=False,
		)
		return
	if not frappe.db.exists("Automation Workflow", run.workflow):
		_fail_unexecutable_token(
			run,
			token,
			error_code="MISSING_WORKFLOW",
			message=f"Pinned automation workflow {run.workflow} no longer exists.",
		)
		return
	if not workflow_runtime_allowed(run.workflow):
		token.status = "HELD"
		token.lease_owner = None
		token.lease_until = None
		token.save(ignore_permissions=True)
		run.status = "WAITING"
		run.save(ignore_permissions=True)
		return
	workflow = frappe.get_doc("Automation Workflow", run.workflow)
	if workflow.status == "PAUSED":
		token.status = "HELD"
		token.save(ignore_permissions=True)
		run.status = "WAITING"
		run.save(ignore_permissions=True)
		return
	if workflow.status != "ACTIVE":
		token.status = "CANCELLED"
		token.save(ignore_permissions=True)
		run.status = "CANCELLED"
		run.completed_at = now_datetime()
		run.save(ignore_permissions=True)
		_append_event(run.name, "RUN_CANCELLED", node_id=token.node_id)
		return
	version_row = frappe.db.get_value(
		"Automation Workflow Version", run.workflow_version, ["name", "workflow"], as_dict=True
	)
	if not version_row or version_row.workflow != run.workflow:
		_fail_unexecutable_token(
			run,
			token,
			error_code="MISSING_WORKFLOW_VERSION",
			message="The run's pinned workflow version is missing or belongs to another workflow.",
		)
		return
	version = frappe.get_doc("Automation Workflow Version", run.workflow_version)
	if not frappe.db.get_value("User", version.execution_user, "enabled"):
		_fail_unexecutable_token(
			run,
			token,
			error_code="EXECUTION_USER_UNAVAILABLE",
			message="Workflow execution user is disabled or missing.",
		)
		return
	if not is_eligible_doctype(run.record_doctype, permission_type="read", user=version.execution_user):
		_fail_unexecutable_token(
			run,
			token,
			error_code="RECORD_DOCTYPE_UNAVAILABLE",
			message="Workflow execution user can no longer read the enrolled DocType.",
		)
		return
	try:
		graph = _graph(version)
	except Exception as exc:
		_fail_unexecutable_token(
			run,
			token,
			error_code="INVALID_PUBLISHED_GRAPH",
			message=f"Published workflow graph cannot be loaded: {exc}",
		)
		return
	node = _node_map(graph).get(token.node_id)
	if not node:
		_fail_unexecutable_token(
			run,
			token,
			error_code="MISSING_PUBLISHED_NODE",
			message="Published workflow node is missing.",
		)
		return
	try:
		with _execution_identity(
			version.execution_user,
			{"trace_id": run.trace_id, "causation_id": run.causation_id or run.trace_id, "recursion_depth": cint(run.recursion_depth) + 1},
		):
			policy_record = frappe.get_doc(run.record_doctype, run.record_name)
			policy_record.check_permission("read")
			if _apply_run_policies(run, token, version, graph, _read_record(run, policy_record)):
				return
	except frappe.db.InternalError:
		raise
	except Exception as exc:
		_fail_unexecutable_token(
			run,
			token,
			error_code=getattr(exc, "code", "RUNTIME_PREFLIGHT_FAILED"),
			message=f"Workflow runtime preflight failed: {exc}",
		)
		return
	try:
		settings = parse_object(version.settings_json or {}, "workflow settings")
		if _hold_for_execution_window(run, token, node, graph, settings):
			return
	except frappe.db.InternalError:
		raise
	except Exception as exc:
		_fail_unexecutable_token(
			run,
			token,
			error_code=getattr(exc, "code", "EXECUTION_WINDOW_FAILED"),
			message=f"Workflow action-window check failed: {exc}",
		)
		return
	token.status = "RUNNING"
	token.attempts = cint(token.attempts) + 1
	token.lease_owner = getattr(frappe.local, "request_ip", None) or "worker"
	token.lease_until = add_to_date(now_datetime(), minutes=5)
	token.save(ignore_permissions=True)
	if not run.started_at:
		run.started_at = now_datetime()
	run.status = "RUNNING"
	run.current_node_id = token.node_id
	run.save(ignore_permissions=True)
	attempt = frappe.get_doc(
		{
			"doctype": "Automation Action Attempt",
			"run": run.name,
			"token": token.name,
			"node_id": token.node_id,
			"attempt_no": token.attempts,
			"status": "STARTED",
			"effect_key": _effect_key(run.name, token.node_id, token.occurrence),
			"started_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	_append_event(run.name, "NODE_STARTED", node_id=token.node_id, payload={"attempt": token.attempts})
	frappe.db.savepoint("automation_node")
	try:
		with _execution_identity(
			version.execution_user,
			{
				"trace_id": run.trace_id,
				"causation_id": run.causation_id or run.trace_id,
				"recursion_depth": cint(run.recursion_depth) + 1,
			},
		):
			record = frappe.get_doc(run.record_doctype, run.record_name)
			result = _execute_node(run, token, node, record, _read_record(run, record), _completed_outputs(run.name))
	except frappe.db.InternalError:
		raise
	except Exception as exc:
		frappe.db.rollback(save_point="automation_node")
		_handle_failure(run, token, attempt, exc)
		return
	attempt.status = "COMPLETED" if result["status"] == "COMPLETE" else "WAITING"
	attempt.output_json = json.dumps(result.get("output") or {}, default=str)
	attempt.completed_at = now_datetime()
	attempt.save(ignore_permissions=True)
	_finish_or_continue(run, token, graph, result)


def execute_external_effect(ledger_name: str, token_name: str) -> None:
	"""Perform a persisted external effect in its own Frappe-managed job transaction."""
	_assert_worker_execution()

	# Fetch without locks first to avoid holding DB locks during network IO
	ledger = frappe.get_doc("Automation Effect Ledger", ledger_name)
	if ledger.status != "STARTED":
		return
	token = frappe.get_doc("Automation Run Token", token_name)
	run = frappe.get_doc("Automation Run", token.run)
	if token.status != "WAITING" or run.status in RUN_TERMINAL_STATUSES:
		return

	if not workflow_runtime_allowed(run.workflow):
		# We need locks to modify state
		run = frappe.get_doc("Automation Run", run.name, for_update=True)
		token = frappe.get_doc("Automation Run Token", token_name, for_update=True)
		token.status = "HELD"
		token.save(ignore_permissions=True)
		return

	workflow = frappe.get_doc("Automation Workflow", run.workflow)
	if workflow.status != "ACTIVE":
		return
	version = frappe.get_doc("Automation Workflow Version", run.workflow_version)
	graph = _graph(version)
	node = _node_map(graph).get(token.node_id)
	if not node or node.get("type") not in EXTERNAL_ACTION_NODE_TYPES:
		raise AutomationError(_("Persisted external action node is missing or invalid."))

	attempt_name = frappe.db.get_value(
		"Automation Action Attempt",
		{"token": token.name},
		"name",
		order_by="attempt_no desc",
	)
	attempt = frappe.get_doc("Automation Action Attempt", attempt_name) if attempt_name else None

	try:
		from .external import AutomationUnknownCommitError, execute_external

		with _execution_identity(
			version.execution_user,
			{
				"trace_id": run.trace_id,
				"causation_id": run.causation_id or run.trace_id,
				"recursion_depth": cint(run.recursion_depth) + 1,
			},
		):
			record = frappe.get_doc(run.record_doctype, run.record_name)
			record.check_permission("read")
			value_record = _read_record(run, record)

			# EXECUTE NETWORK I/O (No DB locks held here!)
			result = execute_external(
				node["type"],
				run,
				node.get("config") or {},
				record=value_record,
				outputs=_completed_outputs(run.name),
				effect_key=ledger.effect_key,
				workflow_settings=parse_object(version.settings_json or {}, "workflow settings"),
			)
	except Exception as exc:
		from .external import AutomationUnknownCommitError

		# Acquire locks to record failure
		run = frappe.get_doc("Automation Run", run.name, for_update=True)
		token = frappe.get_doc("Automation Run Token", token.name, for_update=True)
		ledger = frappe.get_doc("Automation Effect Ledger", ledger.name, for_update=True)

		if isinstance(exc, AutomationUnknownCommitError):
			ledger.status = "UNKNOWN_COMMIT"
			ledger.result_json = json.dumps({"error": str(exc)}, default=str)
			ledger.save(ignore_permissions=True)
			if attempt:
				attempt.status = "UNKNOWN_COMMIT"
				attempt.completed_at = now_datetime()
				attempt.save(ignore_permissions=True)
			return

		ledger.status = "FAILED"
		ledger.result_json = json.dumps({"error": str(exc)}, default=str)
		ledger.save(ignore_permissions=True)

		if attempt:
			_handle_failure(run, token, attempt, exc)
		return

	# Acquire locks to record success
	run = frappe.get_doc("Automation Run", run.name, for_update=True)
	token = frappe.get_doc("Automation Run Token", token.name, for_update=True)
	ledger = frappe.get_doc("Automation Effect Ledger", ledger.name, for_update=True)

	# Verify states haven't changed while we were doing network I/O
	if ledger.status != "STARTED" or token.status != "WAITING" or run.status in RUN_TERMINAL_STATUSES:
		return

	ledger.status = "COMPLETED"
	ledger.result_json = json.dumps(result.get("output") or {}, default=str)
	ledger.completed_at = now_datetime()
	ledger.save(ignore_permissions=True)

	if attempt:
		attempt.status = "COMPLETED" if result["status"] == "COMPLETE" else "WAITING"
		attempt.output_json = json.dumps(result.get("output") or {}, default=str)
		attempt.completed_at = now_datetime()
		attempt.save(ignore_permissions=True)

	_finish_or_continue(run, token, graph, result)


def release_due_timers() -> int:
	if not automation_enabled() or not frappe.db.table_exists("Automation Timer"):
		return 0
	batch_size = min(max(int_setting("timer_batch_size", 100), 1), 500)
	rows = frappe.db.get_values(
		"Automation Timer",
		filters={"status": "ACTIVE", "due_at": ["<=", now_datetime()]},
		fieldname=["name"],
		as_dict=True,
		order_by="due_at asc",
		limit=batch_size,
		for_update=True,
		skip_locked=True,
	)
	released = 0
	for row in rows:
		timer = frappe.get_doc("Automation Timer", row.name, for_update=True)
		if timer.status != "ACTIVE":
			continue
		token = frappe.get_doc("Automation Run Token", timer.token, for_update=True)
		run = frappe.get_doc("Automation Run", timer.run)
		workflow = frappe.get_doc("Automation Workflow", run.workflow)
		if not workflow_runtime_allowed(workflow.name):
			continue
		if workflow.status == "PAUSED":
			continue
		if workflow.status != "ACTIVE" or run.status in RUN_TERMINAL_STATUSES:
			timer.status = "CANCELLED"
			timer.save(ignore_permissions=True)
			continue
		timer.status = "RELEASED"
		timer.released_at = now_datetime()
		timer.save(ignore_permissions=True)
		token.status = "READY"
		token.available_at = now_datetime()
		if timer.timer_type in {"DELAY", "TIMEOUT"}:
			payload = json.loads(token.output_json or "{}")
			payload["released"] = True
			if timer.timer_type == "TIMEOUT":
				payload.update({"event_payload": None, "timed_out": True, "matched_handle": "timeout"})
			token.output_json = json.dumps(payload)
		token.lease_owner = None
		token.lease_until = None
		token.save(ignore_permissions=True)
		run.status = "QUEUED"
		run.save(ignore_permissions=True)
		_append_event(run.name, "TIMER_RELEASED", node_id=token.node_id)
		_queue_token(token.name)
		released += 1
	return released


def release_event_waiters(
	event_topic: str,
	payload: dict | None = None,
	*,
	record_doctype: str | None = None,
	record_name: str | None = None,
	source_doctype: str | None = None,
	source_name: str | None = None,
	limit: int = 500,
) -> int:
	"""Release durable event waits. Call this after the source transaction commits."""
	event_topic = str(event_topic or "").strip()
	if not event_topic:
		raise AutomationError(_("Event topic is required."))
	if payload is not None and not isinstance(payload, dict):
		raise AutomationError(_("Event payload must be a JSON object."))
	row_limit = min(max(cint(limit), 1), 500)
	timer_names: list[str] = []

	def add_candidates(filters: dict) -> None:
		for name in frappe.get_all(
			"Automation Timer",
			filters={
				"status": "ACTIVE",
				"timer_type": ["in", ["TIMEOUT", "EVENT_WAIT"]],
				"event_topic": event_topic,
				**filters,
			},
			pluck="name",
			order_by="creation asc",
			limit=row_limit,
		):
			if name not in timer_names:
				timer_names.append(name)

	if record_doctype and record_name:
		add_candidates(
			{
				"record_doctype": record_doctype,
				"record_name": record_name,
				"source_type": "ENROLLED_RECORD",
			}
		)
	if source_doctype and source_name:
		add_candidates(
			{
				"source_doctype": source_doctype,
				"source_name": source_name,
				"source_type": ["in", ["ACTION_EMAIL", "ACTION_RECORD"]],
			}
		)
	if not (record_doctype and record_name) and not (source_doctype and source_name):
		# Backward-compatible integration boundary: callers that can only provide
		# an event payload still scan the indexed topic, never an unrelated global
		# timer window. Source IDs in token state remain authoritative below.
		add_candidates({})
	# Active timers created before source indexing was introduced remain
	# releasable until the migration backfill has visited them.
	add_candidates({"record_doctype": ["is", "not set"]})
	released = 0
	for timer_name in timer_names:
		timer = frappe.get_doc("Automation Timer", timer_name, for_update=True)
		if timer.status != "ACTIVE" or timer.timer_type not in {"TIMEOUT", "EVENT_WAIT"}:
			continue
		run = frappe.get_doc("Automation Run", timer.run)
		if record_doctype and run.record_doctype != record_doctype:
			continue
		if record_name and run.record_name != record_name:
			continue
		if run.status in RUN_TERMINAL_STATUSES or not workflow_runtime_allowed(run.workflow):
			continue
		version = frappe.get_doc("Automation Workflow Version", run.workflow_version)
		node = _node_map(_graph(version)).get(timer.node_id)
		if not node or node.get("type") != "delay.until_event":
			continue
		if str((node.get("config") or {}).get("event_topic") or "").strip() != event_topic:
			continue
		if not event_filter_matches((node.get("config") or {}).get("event_filter"), payload):
			continue
		token = frappe.get_doc("Automation Run Token", timer.token, for_update=True)
		if token.status != "WAITING":
			continue
		waiting_state = json.loads(token.output_json or "{}")
		expected_source = waiting_state.get("event_source_id")
		if expected_source and waiting_state.get("event_source_type") != "ENROLLED_RECORD":
			payload_source = source_name or (payload or {}).get("email_queue") or (payload or {}).get("email_id") or (payload or {}).get("message_id")
			if str(payload_source or "") != str(expected_source):
				continue
		outcome_time = now_datetime()
		if (payload or {}).get("occurred_at"):
			try:
				occurred_at = frappe.utils.get_datetime((payload or {}).get("occurred_at"))
				timer_created_at = frappe.utils.get_datetime(timer.creation)
				if timer_created_at <= occurred_at <= outcome_time:
					outcome_time = occurred_at
			except (TypeError, ValueError):
				pass
		timed_out = bool(
			timer.timer_type == "TIMEOUT"
			and timer.due_at
			and frappe.utils.get_datetime(timer.due_at) <= outcome_time
		)
		timer.status = "RELEASED"
		timer.released_at = now_datetime()
		timer.save(ignore_permissions=True)
		token.status = "READY"
		token.available_at = now_datetime()
		token.lease_owner = None
		token.lease_until = None
		token.output_json = json.dumps({
			**waiting_state,
			"released": True,
			"event_payload": None if timed_out else payload or {},
			"timed_out": timed_out,
			"matched_handle": "timeout" if timed_out else "event",
		})
		token.save(ignore_permissions=True)
		run.status = "QUEUED"
		run.save(ignore_permissions=True)
		_append_event(
			run.name,
			"TIMER_RELEASED" if timed_out else "EVENT_WAIT_RELEASED",
			node_id=token.node_id,
			payload={"event_topic": event_topic, "outcome": "timeout" if timed_out else "event"},
		)
		_queue_token(token.name)
		released += 1
	return released


def dispatch_ready_tokens(token_names: list[str] | None = None) -> int:
	"""Recover expired leases and wake ready tokens after worker/Redis crashes."""
	if not automation_enabled() or not frappe.db.table_exists("Automation Run Token"):
		return 0
	batch_size = min(max(int_setting("token_batch_size", 100), 1), 500)
	running_filters = {"status": "RUNNING", "lease_until": ["<=", now_datetime()]}
	if token_names is not None:
		if not token_names:
			return 0
		running_filters["name"] = ["in", token_names]
	for row in frappe.get_list(
		"Automation Run Token",
		filters=running_filters,
		fields=["name", "run"],
		ignore_permissions=True,
		limit=batch_size,
	):
		token = frappe.get_doc("Automation Run Token", row.name, for_update=True)
		if token.status != "RUNNING" or not token.lease_until or token.lease_until > now_datetime():
			continue
		token.status = "READY"
		token.available_at = now_datetime()
		token.lease_owner = None
		token.lease_until = None
		token.save(ignore_permissions=True)
		frappe.db.set_value("Automation Run", token.run, "status", "QUEUED", update_modified=False)

	ready_filters = {"status": "READY", "available_at": ["<=", now_datetime()]}
	if token_names is not None:
		ready_filters["name"] = ["in", token_names]
	ready = frappe.get_list(
		"Automation Run Token",
		filters=ready_filters,
		pluck="name",
		ignore_permissions=True,
		limit=batch_size,
	)
	for token_name in ready:
		_queue_token(token_name)
	return len(ready)


def resume_held_tokens(workflow_name: str) -> int:
	if not workflow_runtime_allowed(workflow_name):
		return 0
	run_names = frappe.get_list(
		"Automation Run", filters={"workflow": workflow_name}, pluck="name", ignore_permissions=True, limit=0
	)
	if not run_names:
		return 0
	rows = frappe.get_list(
		"Automation Run Token",
		filters={
			"status": "HELD",
			"run": ["in", run_names],
		},
		pluck="name",
		ignore_permissions=True,
		limit=0,
	)
	for token_name in rows:
		frappe.db.set_value("Automation Run Token", token_name, "status", "READY", update_modified=False)
		_queue_token(token_name)
	return len(rows)


def cancel_run_record(run_name: str) -> dict:
	run = frappe.get_doc("Automation Run", run_name, for_update=True)
	run.check_permission("read")
	if run.status in RUN_TERMINAL_STATUSES:
		return {"run_id": run.name, "status": run.status}
	run.status = "CANCELLED"
	run.completed_at = now_datetime()
	run.save(ignore_permissions=True)
	increment_metric(run.workflow, run.workflow_version, "cancelled_runs")
	frappe.db.set_value(
		"Automation Run Token",
		{"run": run.name, "status": ["not in", ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]]},
		"status",
		"CANCELLED",
		update_modified=False,
	)
	_append_event(run.name, "RUN_CANCELLED")
	_resolve_subflow_if_any(run)
	return {"run_id": run.name, "status": run.status}


def apply_response_policy(record_doctype: str, record_name: str, payload: dict | None = None) -> int:
	"""Unenroll active runs whose pinned communication policy stops on reply."""
	rows = frappe.get_all(
		"Automation Run",
		filters={
			"record_doctype": record_doctype,
			"record_name": record_name,
			"status": ["not in", list(RUN_TERMINAL_STATUSES)],
		},
		fields=["name", "workflow", "workflow_version"],
		limit=500,
	)
	stopped = 0
	mark_read = False
	for row in rows:
		version = frappe.get_doc("Automation Workflow Version", row.workflow_version)
		communication = parse_object(version.settings_json or {}, "workflow settings").get("communication") or {}
		mark_read = mark_read or bool(cint(communication.get("mark_responses_read")))
		if not cint(communication.get("stop_on_response")):
			continue
		run = frappe.get_doc("Automation Run", row.name, for_update=True)
		if run.status in RUN_TERMINAL_STATUSES:
			continue
		frappe.db.set_value(
			"Automation Timer",
			{"run": run.name, "status": "ACTIVE"},
			"status",
			"CANCELLED",
			update_modified=False,
		)
		frappe.db.set_value(
			"Automation Run Token",
			{"run": run.name, "status": ["not in", list(TOKEN_TERMINAL_STATUSES) + ["RUNNING"]]},
			"status",
			"CANCELLED",
			update_modified=False,
		)
		run.status = "CANCELLED"
		run.completed_at = now_datetime()
		run.error_code = "RESPONSE_RECEIVED"
		run.error_message = _("Workflow stopped because the enrolled record responded.")
		run.save(ignore_permissions=True)
		_append_event(run.name, "RUN_STOPPED_ON_RESPONSE", payload=payload or {})
		increment_metric(run.workflow, run.workflow_version, "cancelled_runs")
		_resolve_subflow_if_any(run)
		stopped += 1
	communication_name = str((payload or {}).get("communication") or "")
	if mark_read and communication_name and frappe.db.exists("Communication", communication_name):
		frappe.db.set_value(
			"Communication",
			communication_name,
			{"seen": 1, "unread_notification_sent": 1},
			update_modified=False,
		)
	return stopped


def retry_run_record(run_name: str) -> dict:
	run = frappe.get_doc("Automation Run", run_name, for_update=True)
	run.check_permission("read")
	if run.status != "FAILED":
		raise AutomationError(_("Only failed runs can be retried."))
	token_name = frappe.db.get_value("Automation Run Token", {"run": run.name, "status": "FAILED"}, "name")
	if not token_name:
		raise AutomationError(_("Failed run has no retryable token."))
	frappe.db.set_value("Automation Run Token", token_name, {"status": "READY", "error_message": None}, update_modified=False)
	run.status = "QUEUED"
	run.error_code = None
	run.error_message = None
	run.completed_at = None
	run.save(ignore_permissions=True)
	_append_event(run.name, "RUN_RETRY_REQUESTED")
	_queue_token(token_name)
	return {"run_id": run.name, "status": run.status}


def reconcile_external_effect(ledger_name: str, resolution: str) -> dict:
	ledger = frappe.get_doc("Automation Effect Ledger", ledger_name, for_update=True)
	if ledger.status != "UNKNOWN_COMMIT":
		raise AutomationError(_("Only effects with unknown delivery state can be reconciled."))
	resolution = str(resolution or "").upper()
	if resolution not in {"DELIVERED", "NOT_DELIVERED"}:
		raise AutomationError(_("Resolution must be delivered or not delivered."))
	run = frappe.get_doc("Automation Run", ledger.run, for_update=True)
	token_name = frappe.db.get_value("Automation Run Token", {"run": run.name, "node_id": ledger.node_id}, "name")
	if not token_name:
		raise AutomationError(_("External effect token is missing."))
	token = frappe.get_doc("Automation Run Token", token_name, for_update=True)
	if resolution == "DELIVERED":
		result = {"operator_reconciled": True, "resolution": "DELIVERED"}
		ledger.status = "COMPLETED"
		ledger.result_json = json.dumps(result)
		ledger.completed_at = now_datetime()
		ledger.save(ignore_permissions=True)
		token.status = "READY"
		token.output_json = json.dumps({"external_completed": True, "result": result})
	else:
		ledger.status = "STARTED"
		ledger.result_json = None
		ledger.save(ignore_permissions=True)
		token.status = "WAITING"
	token.error_message = None
	token.save(ignore_permissions=True)
	run.status = "QUEUED" if resolution == "DELIVERED" else "WAITING"
	run.error_code = None
	run.error_message = None
	run.save(ignore_permissions=True)
	_append_event(run.name, "EXTERNAL_EFFECT_RECONCILED", node_id=ledger.node_id, payload={"effect": ledger.name, "resolution": resolution})
	if resolution == "DELIVERED":
		_queue_token(token.name)
	else:
		frappe.enqueue(
			"finbyzai.workflow_builder.engine.execute_external_effect",
			ledger_name=ledger.name,
			token_name=token.name,
			queue="default",
			enqueue_after_commit=True,
			job_id=f"automation-external-{ledger.name}-reconciled",
			deduplicate=True,
		)
	return {"effect_id": ledger.name, "resolution": resolution, "run_id": run.name}


def list_run_records(
	workflow_name: str,
	start: int = 0,
	page_length: int = 50,
	record_name: str | None = None,
) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("read")
	filters = {"workflow": workflow_name}
	needle = str(record_name or "").strip()
	if needle:
		filters["record_name"] = ["like", f"%{needle}%"]
	limit = min(max(cint(page_length), 1), 100)
	rows = frappe.get_list(
		"Automation Run",
		filters=filters,
		fields=["name", "workflow_version", "record_doctype", "record_name", "source", "status", "started_at", "completed_at", "error_code", "modified"],
		order_by="creation desc",
		start=max(cint(start), 0),
		limit=limit + 1,
	)
	count_rows = frappe.get_list(
		"Automation Run",
		filters=filters,
		fields=[{"COUNT": "name", "as": "count"}],
		limit=1,
	)
	return {
		"rows": rows[:limit],
		"has_more": len(rows) > limit,
		"total_count": cint(count_rows[0].get("count")) if count_rows and hasattr(count_rows[0], "get") else 0,
		"workflow": {
			"name": workflow.name,
			"title": workflow.title,
			"primary_doctype": workflow.primary_doctype,
			"status": workflow.status,
			"active_version": workflow.active_version,
			"trigger_type": published_trigger_type(workflow.active_version),
			"runtime_allowed": workflow_runtime_allowed(workflow.name),
		},
	}


def get_run_record(run_name: str) -> dict:
	run = frappe.get_doc("Automation Run", run_name)
	run.check_permission("read")
	version = frappe.get_doc("Automation Workflow Version", run.workflow_version)
	tokens = frappe.get_list("Automation Run Token", filters={"run": run.name}, fields=["name", "node_id", "occurrence", "status", "attempts", "output_json", "error_message"], order_by="creation asc")
	trace = {section: get_run_trace(run.name, section, 0, 100) for section in _RUN_TRACE_SECTIONS}
	return {
		"run": run.as_dict(no_nulls=True), "graph": _graph(version),
		"version_settings": parse_object(version.settings_json or "{}", "workflow settings"),
		"tokens": tokens,
		**{section: page["rows"] for section, page in trace.items()},
		"trace_has_more": {section: page["has_more"] for section, page in trace.items()},
	}


_RUN_TRACE_SECTIONS = {
	"events": (
		"Automation Run Event",
		["name", "sequence_no", "event_type", "node_id", "payload_json", "occurred_at"],
		"sequence_no asc",
	),
	"attempts": (
		"Automation Action Attempt",
		["name", "token", "node_id", "attempt_no", "status", "effect_key", "error_code", "error_message", "output_json", "started_at", "completed_at"],
		"creation asc",
	),
	"enrollment_decisions": (
		"Automation Enrollment Decision",
		["name", "decision", "reason_code", "evidence_json", "source", "trace_id", "decided_at"],
		"creation asc",
	),
	"policy_evaluations": (
		"Automation Policy Evaluation",
		["name", "outbox_event", "event_id", "changed_fields_json", "outcome", "reason_code", "evaluated_at"],
		"creation asc",
	),
}


def get_run_trace(run_name: str, section: str, start: int = 0, page_length: int = 100) -> dict:
	run = frappe.get_doc("Automation Run", run_name)
	run.check_permission("read")
	section = str(section or "").strip()
	if section not in _RUN_TRACE_SECTIONS:
		raise AutomationError(_("Unsupported run trace section."))
	doctype, fields, order_by = _RUN_TRACE_SECTIONS[section]
	if not frappe.db.table_exists(doctype):
		return {"rows": [], "has_more": False}
	limit = min(max(cint(page_length), 1), 200)
	rows = frappe.get_list(
		doctype,
		filters={"run": run.name},
		fields=fields,
		order_by=order_by,
		start=max(cint(start), 0),
		limit=limit + 1,
	)
	return {"rows": rows[:limit], "has_more": len(rows) > limit}


def simulate_graph(graph: dict, record, *, start_node_id: str | None = None, execution_user: str | None = None) -> dict:
	nodes = _node_map(graph)
	current = start_node_id or graph.get("start_node_id")
	path = []
	outputs: dict[str, Any] = {}
	visited = 0
	while current and current in nodes and visited <= len(nodes):
		visited += 1
		node = nodes[current]
		config = node.get("config") or {}
		entry = {
			"node_id": current,
			"type": node["type"],
			"status": "EVALUATED",
			"confidence": "observed",
			"output": {},
		}
		handle = None
		if node["type"] == "condition.if_else":
			if cint(node.get("type_version") or 1) >= 2:
				handle = "none"
				branch_name = "None"
				for branch in config.get("branches") or []:
					if isinstance(branch, dict) and evaluate_expression(branch.get("condition"), record):
						handle = str(branch.get("handle") or "")
						branch_name = str(branch.get("name") or handle)
						break
				entry["output"] = {"matched": handle != "none", "selected_handle": handle, "branch_name": branch_name}
			else:
				matched = evaluate_expression(config.get("condition"), record)
				handle = "true" if matched else "false"
				entry["output"] = {"matched": matched, "selected_handle": handle, "branch_name": "Yes" if matched else "No"}
		elif node["type"] == "condition.random_split":
			branches = [branch for branch in config.get("branches") or [] if isinstance(branch, dict)]
			# Simulations are intentionally stable for the same record and node.
			seed = f"simulation\0{record.doctype}\0{record.name}\0{current}".encode()
			bucket = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") / 2**64 * 100
			cumulative = 0.0
			selected = branches[-1] if branches else {}
			for branch in branches:
				cumulative += float(branch.get("percentage") or 0)
				if bucket < cumulative:
					selected = branch
					break
			handle = str(selected.get("handle") or "")
			entry["output"] = {"selected_handle": handle, "branch_name": str(selected.get("name") or handle), "bucket": round(bucket, 6)}
		elif node["type"] == "condition.switch":
			field = str(config.get("field") or "")
			raw_value = record.get(field)
			value = "" if raw_value is None else str(raw_value)
			handle = "default"
			for case in config.get("cases") or []:
				if str(case.get("value") or "") == value:
					handle = str(case.get("handle") or case.get("value") or "")
					break
			entry["output"] = {"value": value, "matched_handle": handle}
		elif node["type"] == "condition.deduplicate":
			duplicate, matched_fields = _find_duplicate(node, record)
			handle = "duplicate" if duplicate else "unique"
			entry["output"] = {"duplicate_name": duplicate, "is_duplicate": bool(duplicate), "matched_fields": matched_fields, "selected_handle": handle}
		elif node["type"] == "delay.fixed":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"delay_seconds": cint(config.get("seconds")), "due_at": str(add_to_date(now_datetime(), seconds=cint(config.get("seconds")))), "released": False}
		elif node["type"] == "delay.drip":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"batch_size": cint(config.get("batch_size")), "interval_seconds": cint(config.get("interval_seconds")), "released": False}
		elif node["type"] == "delay.until_date":
			mode = str(config.get("mode") or ("literal" if config.get("datetime") else "field"))
			if mode == "literal":
				due_value = config.get("datetime")
				source = {"mode": "literal"}
			else:
				fieldname = str(config.get("field") or "")
				due_value = record.get(fieldname)
				source = {"mode": "field", "field": fieldname}
				if due_value in (None, ""):
					entry["status"] = "FAILED"
					entry["note"] = _("Wait-until field {0} has no date value.").format(fieldname)
					outputs[current] = entry["output"]
					path.append(entry)
					return {"path": path, "mutated": False, "completed": False}
			due_at = frappe.utils.get_datetime(due_value)
			entry["output"] = {**source, "due_at": str(due_at), "released": due_at <= now_datetime()}
		elif node["type"] == "delay.until_event":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			event_source = config.get("event_source")
			data_source = event_wait_data_source(config)
			if data_source == "action_output":
				source_id = resolve_value(event_source, record=record, outputs=outputs) if event_source else None
				source_doctype = resolve_value(config.get("event_source_doctype"), record=record, outputs=outputs) if config.get("event_source_doctype") else None
				if not source_doctype and isinstance(event_source, dict) and event_source.get("path") == "email_queue":
					source_doctype = "Email Queue"
				source_type = "ACTION_EMAIL" if source_doctype == "Email Queue" else "ACTION_RECORD"
			else:
				source_id = record.name
				source_doctype = record.doctype
				source_type = "ENROLLED_RECORD"
			wait_indefinitely = event_wait_timeout_mode(config) == "indefinite"
			entry["output"] = {
				"event_payload": {},
				"timed_out": False,
				"released": False,
				"matched_handle": None,
				"event_source_id": source_id,
				"event_source_doctype": source_doctype,
				"event_source_type": source_type,
				"wait_indefinitely": wait_indefinitely,
				"due_at": None if wait_indefinitely else str(add_to_date(now_datetime(), seconds=cint(config.get("timeout_seconds") or 86400))),
			}
		elif node["type"] == "delay.business_hours":
			entry["output"] = _business_hours_state(config)
			entry["status"] = "OBSERVED" if entry["output"]["released"] else "PREDICTED"
			entry["confidence"] = "observed" if entry["output"]["released"] else "predicted"
		elif node["type"] == "transform.value":
			values = [resolve_value(value, record=record, outputs=outputs) for value in (config.get("values") or [])]
			value = _transform_output(config, values, seed=f"simulation\0{record.doctype}\0{record.name}\0{current}")
			entry["output"] = {"value": value}
		elif node["type"] == "transform.associated_record":
			reference_field = str(config.get("reference_field") or "")
			fetch_field = str(config.get("fetch_field") or "")
			linked_name = record.get(reference_field)
			field = frappe.get_meta(record.doctype).get_field(reference_field)
			target_doctype = field.options if field and field.fieldtype == "Link" else record.get(field.options) if field and field.fieldtype == "Dynamic Link" else None
			if target_doctype and linked_name:
				linked_record = frappe.get_doc(target_doctype, linked_name)
				if not frappe.has_permission(target_doctype, ptype="read", doc=linked_record, user=execution_user or frappe.session.user):
					raise frappe.PermissionError
				value = linked_record.get(fetch_field)
			else:
				value = None
			entry["output"] = {"value": value, "linked_name": linked_name}
		elif node["type"] == "transform.child_records":
			child_table_field = str(config.get("child_table_field") or "")
			fetch_field = str(config.get("fetch_field") or "")
			field = frappe.get_meta(record.doctype).get_field(child_table_field)
			children = record.get(child_table_field) or []
			if field and field.fieldtype == "Table MultiSelect":
				definition = _table_multiselect_definition(record.doctype, child_table_field)
				link_field = definition[1].fieldname if definition else None
				values = _multiselect_names(record.doctype, child_table_field, children) if fetch_field == link_field else [child.get(fetch_field) for child in children]
			else:
				values = [child.get(fetch_field) for child in children]
			entry["output"] = {"values": values, "count": len(values)}
		elif node["type"] == "action.manage_association":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"operation": config.get("operation") or "link", "target_name": config.get("target_name")}
		elif node["type"] == "action.numeric_adjust":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			fieldname = str(config.get("field") or "")
			previous = frappe.utils.flt(record.get(fieldname))
			new_value = _calculate_numeric_adjustment(previous, str(config.get("operation") or "add"), frappe.utils.flt(config.get("amount") or 0))
			entry["output"] = {"field": fieldname, "previous": previous, "new_value": new_value}
		elif node["type"] == "action.round_robin":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["note"] = _("The assignment cursor is not advanced during simulation.")
			entry["output"] = {"assigned_to": "__simulated__", "group": config.get("group") or ""}
		elif node["type"] == "action.call_subflow":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"run_id": "ARUN-00000", "status": "WAITING" if cint(config.get("wait_for_completion", 1)) else "QUEUED"}
		elif node["type"] in {"action.update_record", "action.create_record"}:
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			assignments = _assignments(config, record=record, outputs=outputs)
			entry["output"] = {
				"doctype": record.doctype if node["type"] == "action.update_record" else config.get("target_doctype"),
				"name": "__simulated__",
				"values": assignments,
				**({"updated_fields": sorted(assignments)} if node["type"] == "action.update_record" else {}),
			}
		elif node["type"] == "action.create_todo":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"doctype": "ToDo", "name": "__simulated__", "allocated_to": config.get("allocated_to")}
		elif node["type"] == "action.add_comment":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"comment": "__simulated__"}
		elif node["type"] in {"action.create_note", "action.copy_record", "action.merge_contact", "action.unassign_record", "action.verify_email", "action.mark_communications_read", "action.remove_from_workflow", "action.complete_goal", "action.go_to"}:
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {
				"would_execute": True,
				**({"target_node_id": config.get("target_node_id")} if node["type"] == "action.go_to" else {}),
				**({"terminate_path": True} if node["type"] in {"action.merge_contact", "action.complete_goal"} or (node["type"] == "action.remove_from_workflow" and config.get("target_workflow", "current") == "current") else {}),
			}
		elif node["type"] == "action.notify_user":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"for_user": config.get("for_user"), "subject": config.get("subject")}
		elif node["type"] == "action.send_email":
			entry["status"] = "SKIPPED_EXTERNAL"
			entry["confidence"] = "skipped"
			entry["note"] = _("External delivery is never performed during simulation.")
			entry["output"] = {
				"recipient": resolve_value(config.get("recipient"), record=record, outputs=outputs),
				"email_queue": "__simulated__",
				"sender": config.get("sender_email") or None,
				"reply_to": config.get("reply_to") or None,
				"email_template": config.get("email_template") or None,
				"content_hash": "__simulated__" if config.get("email_template") else None,
			}
		elif node["type"] == "action.send_sms":
			entry["status"] = "SKIPPED_EXTERNAL"
			entry["confidence"] = "skipped"
			entry["note"] = _("External delivery is never performed during simulation.")
			entry["output"] = {"recipient": resolve_value(config.get("recipient"), record=record, outputs=outputs), "status": "WOULD_SEND", "consent_check": True}
		elif node["type"] == "action.webhook":
			entry["status"] = "SKIPPED_EXTERNAL"
			entry["confidence"] = "skipped"
			entry["note"] = _("External delivery is never performed during simulation.")
			entry["output"] = {"status_code": 200, "response_hash": "__simulated__"}
		elif node["type"] == "action.instagram_message":
			entry["status"] = "SKIPPED_EXTERNAL"
			entry["confidence"] = "skipped"
			entry["note"] = _("Instagram delivery is never performed during simulation.")
			entry["output"] = {"recipient_id": resolve_value(config.get("recipient_id"), record=record, outputs=outputs), "status_code": 200, "response_hash": "__simulated__"}
		elif node["type"] == "action.asana":
			entry["status"] = "SKIPPED_EXTERNAL"
			entry["confidence"] = "skipped"
			entry["note"] = _("Asana mutations are never performed during simulation.")
			entry["output"] = {"gid": "__simulated__", "operation": config.get("operation")}
		elif node["type"] == "action.delete_record":
			entry["status"] = "PREDICTED"
			entry["confidence"] = "predicted"
			entry["output"] = {"doctype": record.doctype, "name": record.name, "deleted": True}
		else:
			from .registry import get_node_definition
			definition = get_node_definition(node["type"])
			if definition and definition.get("executor"):
				entry["output"] = {"simulated_plugin": True}
		outputs[current] = entry["output"]
		path.append(entry)
		terminal_prediction = node["type"] in {"action.delete_record", "action.merge_contact", "action.complete_goal"} or (node["type"] == "action.remove_from_workflow" and config.get("target_workflow", "current") == "current")
		next_nodes = [] if terminal_prediction else ([str(config.get("target_node_id"))] if node["type"] == "action.go_to" else _next_nodes(graph, current, handle))
		current = next_nodes[0] if next_nodes else None
	return {"path": path, "mutated": False, "completed": not current}
