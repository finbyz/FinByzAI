from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from .errors import AutomationConflictError, AutomationError, AutomationPermissionError
from .registry import assert_field_access, doctype_eligibility, field_catalog_result, is_eligible_doctype
from .schema import (
	canonical_json,
	condition_fields,
	execution_graph,
	execution_graph_hash,
	empty_graph,
	parse_object,
	validate_expression,
	validate_graph,
)


def _trace_id() -> str:
	return frappe.generate_hash(length=20)


def _enabled_user(user: str) -> str:
	if not user or not frappe.db.get_value("User", user, "enabled"):
		raise AutomationError(_("Choose an enabled execution user."))
	return user


def _workflow(name: str, ptype: str = "read", *, for_update: bool = False):
	doc = frappe.get_doc("Automation Workflow", name, for_update=for_update)
	doc.check_permission(ptype)
	return doc


def _draft(workflow_name: str, *, for_update: bool = False):
	name = frappe.db.get_value("Automation Workflow Draft", {"workflow": workflow_name}, "name")
	if not name:
		raise AutomationError(_("Workflow draft is missing."))
	doc = frappe.get_doc("Automation Workflow Draft", name, for_update=for_update)
	doc.check_permission("read")
	return doc


def _normalized_settings(settings_value: Any) -> dict:
	settings = dict(parse_object(settings_value or {}, "workflow settings"))
	settings["reenrollment"] = str(settings.get("reenrollment") or "NEVER").upper()
	settings["read_mode"] = str(settings.get("read_mode") or "CURRENT").upper()
	settings["unenroll_when_ineligible"] = bool(cint(settings.get("unenroll_when_ineligible")))
	for key in ("goal_condition", "eligibility_condition"):
		if not settings.get(key):
			settings.pop(key, None)
	return settings


def workflow_publication_state(workflow, draft=None) -> dict:
	"""Describe how the mutable draft relates to immutable published history."""
	draft = draft or _draft(workflow.name)
	latest_version_no = cint(workflow.latest_version)
	latest = None
	if latest_version_no:
		latest = frappe.db.get_value(
			"Automation Workflow Version",
			{"workflow": workflow.name, "version_no": latest_version_no},
			["name", "version_no", "graph_hash", "graph_json", "settings_json", "execution_user"],
			as_dict=True,
		)
	active_version_no = None
	if workflow.active_version:
		active_version_no = frappe.db.get_value("Automation Workflow Version", workflow.active_version, "version_no")

	draft_execution_hash = execution_graph_hash(draft.graph_json)
	latest_execution_hash = execution_graph_hash(latest.graph_json) if latest else None
	settings_match = bool(
		latest
		and canonical_json(_normalized_settings(draft.settings_json))
		== canonical_json(_normalized_settings(latest.settings_json))
	)
	execution_user_matches = bool(latest and workflow.execution_user == latest.execution_user)
	draft_matches_latest = bool(
		latest
		and draft_execution_hash == latest_execution_hash
		and settings_match
		and execution_user_matches
	)
	if not latest:
		state = "NEVER_PUBLISHED"
	elif not draft_matches_latest:
		state = "DRAFT_CHANGES"
	elif latest.name != workflow.active_version:
		state = "READY_TO_ACTIVATE"
	else:
		state = "PUBLISHED"

	return {
		"state": state,
		"has_published_version": bool(latest),
		"has_unpublished_changes": not draft_matches_latest,
		"draft_matches_latest_version": draft_matches_latest,
		"latest_version": latest.name if latest else None,
		"latest_version_no": cint(latest.version_no) if latest else 0,
		"active_version": workflow.active_version or None,
		"active_version_no": cint(active_version_no) if active_version_no is not None else None,
		"next_version_no": (cint(latest.version_no) + 1) if latest else 1,
	}


def _validate_value_binding(value: Any, primary_doctype: str, execution_user: str) -> dict | None:
	if not isinstance(value, dict):
		return None
	if value.get("kind") == "record_field":
		return assert_field_access(
			primary_doctype,
			value.get("field"),
			permission_type="read",
			user=execution_user,
			capability=("scalar_read", "condition_collection"),
		)
	return None


def _validate_value_binding_tree(value: Any, primary_doctype: str, execution_user: str) -> None:
	if isinstance(value, dict) and value.get("kind") in {"literal", "record_field", "node_output"}:
		_validate_value_binding(value, primary_doctype, execution_user)
		return
	if isinstance(value, dict):
		for item in value.values():
			_validate_value_binding_tree(item, primary_doctype, execution_user)
	elif isinstance(value, list):
		for item in value:
			_validate_value_binding_tree(item, primary_doctype, execution_user)


def _validate_assignment_value_type(value: Any, target_field: dict, source_field: dict | None) -> None:
	target_collection = bool(target_field.get("capabilities", {}).get("assignment_collection"))
	if not isinstance(value, dict):
		return
	kind = value.get("kind")
	if kind == "literal" and target_collection and not isinstance(value.get("value"), list):
		raise AutomationError(_("Table MultiSelect assignments require a list of linked record names."))
	if kind == "record_field":
		source_collection = bool(source_field and source_field.get("capabilities", {}).get("condition_collection"))
		if target_collection != source_collection:
			raise AutomationError(_("Collection values can only be copied between Table MultiSelect fields."))
	if kind == "node_output" and target_collection and value.get("path") != "values":
		raise AutomationError(_("Table MultiSelect assignments require a prior output path that produces values."))


def _condition_predicates(expression: Any):
	stack = [expression]
	while stack:
		current = stack.pop()
		if not isinstance(current, dict):
			continue
		if current.get("kind") == "predicate":
			yield current
		children = current.get("children")
		if isinstance(children, list):
			stack.extend(children)


def _subflow_reaches(start_workflow: str, wanted_workflow: str) -> bool:
	"""Walk immutable active graphs to detect a dependency path back to the publisher."""
	pending = [start_workflow]
	visited: set[str] = set()
	while pending:
		workflow_name = pending.pop()
		if workflow_name == wanted_workflow:
			return True
		if workflow_name in visited:
			continue
		visited.add(workflow_name)
		active_version = frappe.db.get_value("Automation Workflow", workflow_name, "active_version")
		if not active_version:
			continue
		graph_value = frappe.db.get_value("Automation Workflow Version", active_version, "graph_json")
		try:
			dependency_graph = parse_object(graph_value, "subflow graph")
		except AutomationError:
			continue
		for dependency_node in dependency_graph.get("nodes") or []:
			if isinstance(dependency_node, dict) and dependency_node.get("type") == "action.call_subflow":
				target = str((dependency_node.get("config") or {}).get("subflow_id") or "").strip()
				if target:
					pending.append(target)
	return False


def validate_bindings(graph: dict, execution_user: str, workflow_name: str | None = None) -> list[dict]:
	issues = []
	primary_doctype = graph.get("primary_doctype")
	primary_access = doctype_eligibility(primary_doctype, permission_type="read", user=execution_user)
	if not primary_access["available"]:
		return [
			{
				"severity": "error",
				"code": "PRIMARY_DOCTYPE_UNAVAILABLE",
				"path": "primary_doctype",
				"message": primary_access["explanation"],
			}
		]
	nodes = graph.get("nodes")
	for node in nodes if isinstance(nodes, list) else []:
		if not isinstance(node, dict):
			continue
		node_id = node.get("id")
		node_type = node.get("type")
		config = node.get("config")
		if not isinstance(config, dict):
			continue
		if node_type == "action.call_subflow":
			target_name = str(config.get("subflow_id") or "").strip()
			target = frappe.db.get_value(
				"Automation Workflow",
				target_name,
				["name", "status", "active_version", "primary_doctype"],
				as_dict=True,
			) if target_name else None
			if not target:
				issues.append({"severity": "error", "code": "SUBFLOW_NOT_FOUND", "node_id": node_id, "path": f"nodes.{node_id}.config.subflow_id", "message": _("The selected subflow does not exist.")})
			elif workflow_name and target.name == workflow_name:
				issues.append({"severity": "error", "code": "SUBFLOW_SELF_REFERENCE", "node_id": node_id, "path": f"nodes.{node_id}.config.subflow_id", "message": _("A workflow cannot call itself as a subflow.")})
			elif target.status != "ACTIVE" or not target.active_version:
				issues.append({"severity": "error", "code": "SUBFLOW_NOT_ACTIVE", "node_id": node_id, "path": f"nodes.{node_id}.config.subflow_id", "message": _("The selected subflow must have an active published version.")})
			elif target.primary_doctype != primary_doctype:
				issues.append({"severity": "error", "code": "SUBFLOW_DOCTYPE_MISMATCH", "node_id": node_id, "path": f"nodes.{node_id}.config.subflow_id", "message": _("The subflow must use the same primary DocType.")})
			elif workflow_name and _subflow_reaches(target.name, workflow_name):
				issues.append({"severity": "error", "code": "SUBFLOW_DEPENDENCY_CYCLE", "node_id": node_id, "path": f"nodes.{node_id}.config.subflow_id", "message": _("This subflow dependency would create a cycle.")})
		expression = config.get("condition")
		for predicate in _condition_predicates(expression):
			fieldname = predicate.get("field")
			operator = predicate.get("operator")
			try:
				assert_field_access(
					primary_doctype,
					fieldname,
					permission_type="read",
					user=execution_user,
					capability=("condition_scalar", "condition_collection") if operator in {"is_set", "is_not_set"} else "condition_collection" if operator in {"contains_any", "contains_all", "contains_none"} else "condition_scalar",
				)
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.condition", "message": str(exc)})
		if node_type in {"action.update_record", "action.create_record"}:
			target_doctype = primary_doctype if node_type == "action.update_record" else config.get("target_doctype")
			if not target_doctype or not is_eligible_doctype(
				target_doctype,
				permission_type="write" if node_type == "action.update_record" else "create",
				user=execution_user,
			):
				issues.append({"severity": "error", "code": "DOCTYPE_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.target_doctype", "message": _("Execution user cannot use the target DocType.")})
				continue
			field_permission_type = "write" if node_type == "action.update_record" else "create"
			assignments = config.get("assignments")
			if node_type == "action.create_record":
				assignment_values = {
					str(assignment.get("field") or ""): assignment.get("value")
					for assignment in (assignments if isinstance(assignments, list) else []) if isinstance(assignment, dict)
				}
				for field in field_catalog_result(
					target_doctype,
					permission_type="create",
					user=execution_user,
				)["fields"]:
					if not (field.get("required") or field.get("mandatory_depends_on")) or field.get("default") not in (None, ""):
						continue
					capabilities = field.get("capabilities") or {}
					if not (capabilities.get("assignment_scalar") or capabilities.get("assignment_collection")):
						issues.append({
							"severity": "error",
							"code": "UNSUPPORTED_MANDATORY_CREATE_FIELD",
							"node_id": node_id,
							"path": f"nodes.{node_id}.config.target_doctype",
							"message": _("{0} requires {1}, which this action cannot map. Choose a different target DocType.").format(target_doctype, field.get("label") or field["fieldname"]),
						})
						continue
					value = assignment_values.get(field["fieldname"])
					has_value = False
					if isinstance(value, dict) and value.get("kind") == "record_field":
						has_value = bool(value.get("field"))
					elif isinstance(value, dict) and value.get("kind") == "node_output":
						has_value = bool(value.get("node_id") and value.get("path"))
					elif isinstance(value, dict) and value.get("kind") == "literal":
						has_value = value.get("value") not in (None, "", [])
					if not has_value:
						issues.append({
							"severity": "error",
							"code": "MISSING_MANDATORY_CREATE_FIELD",
							"node_id": node_id,
							"path": f"nodes.{node_id}.config.assignments",
							"message": _("Map {0} field {1} before publishing.").format(
								_("conditionally mandatory") if field.get("mandatory_depends_on") and not field.get("required") else _("mandatory"),
								field.get("label") or field["fieldname"],
							),
						})
			for assignment in assignments if isinstance(assignments, list) else []:
				if not isinstance(assignment, dict):
					continue
				try:
					target_field = assert_field_access(
						target_doctype,
						assignment.get("field"),
						permission_type=field_permission_type,
						user=execution_user,
						capability=("assignment_scalar", "assignment_collection"),
					)
					source_field = _validate_value_binding(assignment.get("value"), primary_doctype, execution_user)
					_validate_assignment_value_type(assignment.get("value"), target_field, source_field)
				except (frappe.PermissionError, AutomationError) as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.assignments", "message": str(exc)})
		if node_type == "delay.until_date":
			try:
				assert_field_access(primary_doctype, config.get("field"), permission_type="read", user=execution_user, capability="scalar_read")
				fieldtype = frappe.get_meta(primary_doctype).get_field(config.get("field")).fieldtype
				if fieldtype not in {"Date", "Datetime"}:
					raise AutomationError(_("Wait-until requires a Date or Datetime field."))
			except (frappe.PermissionError, AutomationError, AttributeError) as exc:
				issues.append({"severity": "error", "code": "DELAY_FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.field", "message": str(exc)})
		if node_type in {"condition.switch", "condition.deduplicate"}:
			fieldname = config.get("field") if node_type == "condition.switch" else config.get("match_field")
			try:
				assert_field_access(primary_doctype, fieldname, permission_type="read", user=execution_user, capability="switch" if node_type == "condition.switch" else "deduplicate")
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": str(exc)})
		if node_type == "delay.business_hours":
			calendar = str(config.get("calendar") or "").strip()
			if calendar and not frappe.db.exists("Holiday List", calendar):
				issues.append({"severity": "error", "code": "HOLIDAY_LIST_NOT_FOUND", "node_id": node_id, "path": f"nodes.{node_id}.config.calendar", "message": _("Holiday List does not exist.")})
		if node_type == "transform.value":
			for value in config.get("values") or []:
				try:
					_validate_value_binding(value, primary_doctype, execution_user)
				except frappe.PermissionError as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.values", "message": str(exc)})
		if node_type == "transform.associated_record":
			try:
				assert_field_access(primary_doctype, config.get("reference_field"), permission_type="read", user=execution_user, capability="scalar_read")
				link_field = frappe.get_meta(primary_doctype).get_field(config.get("reference_field"))
				if not link_field or link_field.fieldtype != "Link" or not link_field.options:
					raise AutomationError(_("Associated-record source must be a Link field."))
				assert_field_access(link_field.options, config.get("fetch_field"), permission_type="read", user=execution_user, capability="scalar_read")
			except (frappe.PermissionError, AutomationError, AttributeError) as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.fetch_field", "message": str(exc)})
		if node_type == "transform.child_records":
			try:
				assert_field_access(primary_doctype, config.get("child_table_field"), permission_type="read", user=execution_user, capability="child_collection")
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.child_table_field", "message": str(exc)})
			try:
				child_doctype = frappe.get_meta(primary_doctype).get_field(config.get("child_table_field")).options
				assert_field_access(child_doctype, config.get("fetch_field"), permission_type="read", user=execution_user, parenttype=primary_doctype, capability="scalar_read")
			except (frappe.PermissionError, AttributeError) as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.fetch_field", "message": str(exc)})
		if node_type == "action.numeric_adjust":
			try:
				assert_field_access(primary_doctype, config.get("field"), permission_type="write", user=execution_user, capability="assignment_scalar")
				field = frappe.get_meta(primary_doctype).get_field(config.get("field"))
				if not field or field.fieldtype not in {"Int", "Float", "Currency", "Percent"}:
					raise AutomationError(_("Numeric adjustment requires a numeric field."))
			except (frappe.PermissionError, AutomationError) as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.field", "message": str(exc)})
		if node_type == "action.manage_association":
			try:
				target_doctype = config.get("target_doctype")
				if not is_eligible_doctype(target_doctype, permission_type="read", user=execution_user):
					raise AutomationError(_("Execution user cannot read the association target DocType."))
				assert_field_access(primary_doctype, config.get("link_field"), permission_type="write", user=execution_user, capability="assignment_scalar")
				field = frappe.get_meta(primary_doctype).get_field(config.get("link_field"))
				if not field or field.fieldtype != "Link" or field.options != target_doctype:
					raise AutomationError(_("Association field must link to the configured target DocType."))
			except (frappe.PermissionError, AutomationError) as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.link_field", "message": str(exc)})
		if node_type == "action.round_robin":
			try:
				assignment_field = config.get("assignment_field") or "owner"
				field = assert_field_access(primary_doctype, assignment_field, permission_type="write", user=execution_user, capability="assignment_scalar")
				if field.get("fieldtype") != "Link" or field.get("options") != "User":
					raise AutomationError(_("Round robin requires a Link field targeting User."))
			except (frappe.PermissionError, AutomationError) as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.assignment_field", "message": str(exc)})
			group = str(config.get("group") or "").strip()
			if group and not frappe.db.exists("User Group", group):
				for candidate in [item.strip() for item in group.replace(";", ",").split(",") if item.strip()]:
					user = frappe.db.get_value("User", candidate, ["name", "enabled"], as_dict=True)
					if not user:
						user = frappe.db.get_value("User", {"email": candidate}, ["name", "enabled"], as_dict=True)
					if not user or not user.enabled:
						issues.append({"severity": "error", "code": "INVALID_ROUND_ROBIN_MEMBER", "node_id": node_id, "path": f"nodes.{node_id}.config.group", "message": _("Round robin member {0} is missing or disabled.").format(candidate)})
		if node_type == "action.delete_record":
			if not is_eligible_doctype(primary_doctype, permission_type="delete", user=execution_user):
				issues.append({"severity": "error", "code": "DOCTYPE_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Execution user cannot delete the enrolled DocType.")})
		if node_type == "action.send_email":
			for key in ("recipient", "subject", "message"):
				try:
					_validate_value_binding(config.get(key), primary_doctype, execution_user)
				except frappe.PermissionError as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.{key}", "message": str(exc)})
		if node_type == "action.send_sms":
			for key in ("recipient", "message"):
				try:
					_validate_value_binding(config.get(key), primary_doctype, execution_user)
				except frappe.PermissionError as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.{key}", "message": str(exc)})
		if node_type == "action.webhook":
			try:
				_validate_value_binding_tree(config.get("payload"), primary_doctype, execution_user)
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.payload", "message": str(exc)})
			secret = frappe.db.get_value("Automation Integration Secret", config.get("integration_secret"), ["enabled", "allowed_hosts"], as_dict=True)
			if not secret or not secret.enabled:
				issues.append({"severity": "error", "code": "INTEGRATION_SECRET_DISABLED", "node_id": node_id, "path": f"nodes.{node_id}.config.integration_secret", "message": _("Choose an enabled integration secret.")})
		if node_type == "action.create_todo" and not frappe.db.get_value("User", config.get("allocated_to"), "enabled"):
			issues.append({"severity": "error", "code": "INVALID_ASSIGNEE", "node_id": node_id, "path": f"nodes.{node_id}.config.allocated_to", "message": _("Choose an enabled assignee.")})
		if node_type == "action.notify_user" and not frappe.db.get_value("User", config.get("for_user"), "enabled"):
			issues.append({"severity": "error", "code": "INVALID_RECIPIENT", "node_id": node_id, "path": f"nodes.{node_id}.config.for_user", "message": _("Choose an enabled notification recipient.")})
	return issues


def validate_settings(settings_value: Any, primary_doctype: str, execution_user: str) -> tuple[dict, list[dict]]:
	settings = _normalized_settings(settings_value)
	issues: list[dict] = []
	reenrollment = settings["reenrollment"]
	if reenrollment not in {"NEVER", "AFTER_COMPLETION", "ALWAYS"}:
		issues.append({"severity": "error", "code": "INVALID_REENROLLMENT", "path": "settings.reenrollment", "message": _("Unsupported re-enrollment policy.")})
	read_mode = settings["read_mode"]
	if read_mode not in {"CURRENT", "ENROLLMENT_SNAPSHOT"}:
		issues.append({"severity": "error", "code": "INVALID_READ_MODE", "path": "settings.read_mode", "message": _("Choose current values or enrollment snapshot values.")})
	for key in ("goal_condition", "eligibility_condition"):
		expression = settings.get(key)
		for issue in validate_expression(expression, f"settings.{key}"):
			issues.append({"severity": "error", **issue})
		for predicate in _condition_predicates(expression):
			fieldname = predicate.get("field")
			operator = predicate.get("operator")
			try:
				assert_field_access(
					primary_doctype,
					fieldname,
					permission_type="read",
					user=execution_user,
					capability=("condition_scalar", "condition_collection") if operator in {"is_set", "is_not_set"} else "condition_collection" if operator in {"contains_any", "contains_all", "contains_none"} else "condition_scalar",
				)
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "path": f"settings.{key}", "message": str(exc)})
	return settings, issues


def _creation_key(operation: str, idempotency_key: str | None, source: str = "") -> str | None:
	key = str(idempotency_key or "").strip()
	if not key:
		return None
	material = f"{frappe.session.user}\0{operation}\0{source}\0{key}"
	return hashlib.sha256(material.encode()).hexdigest()


def _existing_creation_result(creation_key: str | None) -> dict | None:
	if not creation_key:
		return None
	workflow_name = frappe.db.get_value("Automation Workflow", {"creation_key": creation_key}, "name")
	if not workflow_name:
		return None
	draft = _draft(workflow_name)
	return {
		"workflow": workflow_name,
		"draft": draft.name,
		"draft_revision": cint(draft.draft_revision),
		"graph": parse_object(draft.graph_json, "workflow graph"),
		"deduplicated": True,
	}


def create_workflow_record(
	title: str,
	primary_doctype: str,
	description: str = "",
	execution_user: str | None = None,
	trigger_type: str = "trigger.manual",
	*,
	idempotency_key: str | None = None,
	operation: str = "create",
	source: str = "",
) -> dict:
	creation_key = _creation_key(operation, idempotency_key, source)
	if existing := _existing_creation_result(creation_key):
		return existing
	access = doctype_eligibility(primary_doctype)
	if not access["available"]:
		raise AutomationPermissionError(access["explanation"])
	execution_user = _enabled_user(execution_user or frappe.session.user)
	execution_access = doctype_eligibility(primary_doctype, user=execution_user)
	if not execution_access["available"]:
		raise AutomationPermissionError(execution_access["explanation"])
	workflow = frappe.get_doc(
		{
			"doctype": "Automation Workflow",
			"title": str(title or "").strip(),
			"description": description,
			"primary_doctype": primary_doctype,
			"status": "DRAFT",
			"execution_user": execution_user,
			"creation_key": creation_key,
			"state_version": 0,
			"latest_version": 0,
		}
	)
	if not workflow.title:
		raise AutomationError(_("Workflow title is required."))
	try:
		workflow.insert()
	except frappe.DuplicateEntryError:
		if existing := _existing_creation_result(creation_key):
			return existing
		raise
	graph = empty_graph(primary_doctype, trigger_type)
	validation = validate_graph(graph, primary_doctype=primary_doctype)
	draft = frappe.get_doc(
		{
			"doctype": "Automation Workflow Draft",
			"workflow": workflow.name,
			"draft_revision": 0,
			"graph_json": json.dumps(graph),
			"settings_json": "{}",
			"graph_hash": validation["graph_hash"],
			"validation_json": json.dumps(validation["issues"]),
		}
	).insert(ignore_permissions=True)
	create_audit(workflow.name, "WORKFLOW_CREATED", {"primary_doctype": primary_doctype})
	return {"workflow": workflow.name, "draft": draft.name, "draft_revision": 0, "graph": graph}


def list_workflow_records(
	start: int = 0,
	page_length: int = 50,
	status: str | None = None,
	search: str | None = None,
	primary_doctype: str | None = None,
	exclude_workflow: str | None = None,
) -> dict:
	filters = {"status": status} if status else {}
	if primary_doctype:
		filters["primary_doctype"] = primary_doctype
	if exclude_workflow:
		filters["name"] = ["!=", exclude_workflow]
	needle = str(search or "").strip()
	or_filters = (
		{
			"name": ["like", f"%{needle}%"],
			"title": ["like", f"%{needle}%"],
			"primary_doctype": ["like", f"%{needle}%"],
		}
		if needle
		else None
	)
	limit = min(max(cint(page_length), 1), 100)
	rows = frappe.get_list(
		"Automation Workflow",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "title", "primary_doctype", "status", "active_version", "latest_version", "execution_user", "modified", "owner"],
		order_by="modified desc",
		start=max(cint(start), 0),
		limit=limit + 1,
	)
	for row in rows[:limit]:
		row["trigger_type"] = None
		if row.get("active_version"):
			graph = parse_object(
				frappe.db.get_value("Automation Workflow Version", row.get("active_version"), "graph_json") or "{}",
				"published workflow graph",
			)
			start_node = next(
				(node for node in graph.get("nodes") or [] if node.get("id") == graph.get("start_node_id")),
				None,
			)
			row.trigger_type = start_node.get("type") if start_node else None

	def count_for(extra_filters: dict | None = None) -> int:
		count_filters = {**filters, **(extra_filters or {})}
		result = frappe.get_list(
			"Automation Workflow",
			filters=count_filters,
			or_filters=or_filters,
			fields=[{"COUNT": "name", "as": "count"}],
			limit=1,
		)
		return cint(result[0].get("count")) if result and hasattr(result[0], "get") else 0

	return {
		"rows": rows[:limit],
		"has_more": len(rows) > limit,
		"total_count": count_for(),
		"status_counts": {"ACTIVE": count_for({"status": "ACTIVE"}), "PAUSED": count_for({"status": "PAUSED"})},
	}


def delete_workflow_record(workflow_name: str) -> dict:
	"""Delete only an unpublished, never-executed draft owned by the current user."""
	workflow = _workflow(workflow_name, "write", for_update=True)
	if workflow.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
		raise AutomationPermissionError(_("Only the workflow owner can delete this draft."))
	if workflow.status != "DRAFT" or workflow.active_version or cint(workflow.latest_version):
		raise AutomationError(_("Published, active, paused, or disabled workflows cannot be deleted."))
	for doctype in (
		"Automation Workflow Version",
		"Automation Trigger Subscription",
		"Automation Enrollment Ledger",
		"Automation Run",
	):
		if frappe.db.exists(doctype, {"workflow": workflow.name}):
			raise AutomationError(_("This workflow has runtime or publication history and cannot be deleted."))
	for doctype in ("Automation Workflow Draft", "Automation Audit Event"):
		for name in frappe.get_list(
			doctype, filters={"workflow": workflow.name}, pluck="name", ignore_permissions=True, limit=0
		):
			frappe.delete_doc(doctype, name, ignore_permissions=True)
	frappe.delete_doc("Automation Workflow", workflow.name, ignore_permissions=True)
	return {"workflow_id": workflow.name, "deleted": True}


def get_workflow_draft(workflow_name: str) -> dict:
	workflow = _workflow(workflow_name)
	draft = _draft(workflow.name)
	graph = parse_object(draft.graph_json, "workflow graph")
	try:
		stored_validation = json.loads(draft.validation_json or "[]")
	except (TypeError, ValueError):
		stored_validation = []
	validation = stored_validation if isinstance(stored_validation, list) else []
	fresh_validation = validate_graph(graph, primary_doctype=workflow.primary_doctype)
	validation.extend(fresh_validation["issues"])
	validation.extend(validate_bindings(fresh_validation["graph"], workflow.execution_user, workflow.name))
	settings, settings_issues = validate_settings(draft.settings_json, workflow.primary_doctype, workflow.execution_user)
	validation.extend(settings_issues)
	metadata = doctype_eligibility(workflow.primary_doctype, user=workflow.execution_user)
	if not metadata["available"] and not any(
		isinstance(issue, dict) and issue.get("code") == "PRIMARY_DOCTYPE_UNAVAILABLE" for issue in validation
	):
		validation.append(
			{
				"severity": "error",
				"code": "PRIMARY_DOCTYPE_UNAVAILABLE",
				"path": "primary_doctype",
				"message": metadata["explanation"],
			}
		)
	unique_validation = []
	seen_issues = set()
	for issue in validation:
		if not isinstance(issue, dict):
			continue
		key = json.dumps(issue, sort_keys=True, default=str)
		if key not in seen_issues:
			seen_issues.add(key)
			unique_validation.append(issue)
	return {
		"workflow": workflow.as_dict(no_nulls=True),
		"metadata": metadata,
		"publication": workflow_publication_state(workflow, draft),
		"draft": {
			"name": draft.name,
			"draft_revision": draft.draft_revision,
			"graph": graph,
			"settings": settings,
			"graph_hash": draft.graph_hash,
			"validation": unique_validation,
			"modified": draft.modified,
		},
	}


def save_workflow_draft(workflow_name: str, draft_revision: int, graph_value: Any, settings_value: Any = None, client_id: str | None = None) -> dict:
	workflow = _workflow(workflow_name, "write", for_update=True)
	draft = _draft(workflow.name, for_update=True)
	if cint(draft.draft_revision) != cint(draft_revision):
		raise AutomationConflictError(
			_("This workflow changed in another session. Reload before saving."),
			trace_id=_trace_id(),
		)
	validation = validate_graph(graph_value, primary_doctype=workflow.primary_doctype)
	graph = validation["graph"]
	validation["issues"].extend(validate_bindings(graph, workflow.execution_user, workflow.name))
	settings, settings_issues = validate_settings(settings_value if settings_value is not None else draft.settings_json, workflow.primary_doctype, workflow.execution_user)
	validation["issues"].extend(settings_issues)
	validation["valid"] = not validation["issues"]
	draft.draft_revision = cint(draft.draft_revision) + 1
	draft.graph_json = json.dumps(graph)
	draft.settings_json = json.dumps(settings)
	draft.graph_hash = validation["graph_hash"]
	draft.validation_json = json.dumps(validation["issues"])
	draft.save(ignore_permissions=True)
	frappe.publish_realtime(
		"automation_draft_updated",
		{
			"workflow_id": workflow.name,
			"draft_revision": draft.draft_revision,
			"graph_hash": draft.graph_hash,
			"actor": frappe.session.user,
			"client_id": str(client_id or "")[:80],
		},
		doctype="Automation Workflow",
		docname=workflow.name,
		after_commit=True,
	)
	return {
		"workflow_id": workflow.name,
		"draft_revision": draft.draft_revision,
		"graph_hash": draft.graph_hash,
		"validation": validation["issues"],
		"valid": validation["valid"],
		"modified": draft.modified,
		"publication": workflow_publication_state(workflow, draft),
	}


def validate_workflow_draft(workflow_name: str, *, publish: bool = False, draft_revision: int | None = None) -> dict:
	workflow = _workflow(workflow_name)
	draft = _draft(workflow.name)
	if draft_revision is not None and cint(draft.draft_revision) != cint(draft_revision):
		raise AutomationConflictError(_("The saved draft revision changed before validation."))
	validation = validate_graph(draft.graph_json, primary_doctype=workflow.primary_doctype, publish=publish)
	validation["issues"].extend(validate_bindings(validation["graph"], workflow.execution_user, workflow.name))
	_settings, settings_issues = validate_settings(draft.settings_json, workflow.primary_doctype, workflow.execution_user)
	validation["issues"].extend(settings_issues)
	validation["valid"] = not validation["issues"]
	return {"valid": validation["valid"], "issues": validation["issues"], "graph_hash": validation["graph_hash"]}


def validate_published_version(workflow_name: str, version_name: str | None = None) -> dict:
	"""Revalidate an immutable version against the code and metadata available now.

	Published payloads remain immutable. This check is deliberately read-only so a
	new schema rule cannot silently rewrite history; callers may instead refuse an
	activation or pause an unsafe active version while an operator repairs a draft.
	"""
	workflow = _workflow(workflow_name)
	version_name = str(version_name or workflow.active_version or "").strip()
	if not version_name:
		return {
			"valid": False,
			"issues": [{"severity": "error", "code": "NO_ACTIVE_VERSION", "message": _("Workflow has no active published version.")}],
			"graph_hash": "",
			"graph": {},
		}
	if not frappe.db.exists("Automation Workflow Version", version_name):
		return {
			"valid": False,
			"issues": [
				{
					"severity": "error",
					"code": "VERSION_NOT_FOUND",
					"message": _("The workflow's pinned published version no longer exists."),
				}
			],
			"graph_hash": "",
			"graph": {},
			"version": version_name,
		}
	version = frappe.get_doc("Automation Workflow Version", version_name)
	if version.workflow != workflow.name:
		raise AutomationError(_("The active version does not belong to this workflow."))
	validation = validate_graph(version.graph_json, primary_doctype=workflow.primary_doctype, publish=True)
	if validation["graph_hash"] != version.graph_hash:
		validation["issues"].append(
			{
				"severity": "error",
				"code": "VERSION_GRAPH_HASH_MISMATCH",
				"message": _("The immutable version payload no longer matches its published hash."),
			}
		)
	validation["issues"].extend(validate_bindings(validation["graph"], version.execution_user, workflow.name))
	_settings, settings_issues = validate_settings(
		version.settings_json or "{}", workflow.primary_doctype, version.execution_user
	)
	validation["issues"].extend(settings_issues)
	if not frappe.db.get_value("User", version.execution_user, "enabled"):
		validation["issues"].append(
			{
				"severity": "error",
				"code": "VERSION_EXECUTION_USER_DISABLED",
				"message": _("The published version's execution user is disabled."),
			}
		)
	validation["valid"] = not validation["issues"]
	validation["version"] = version.name
	return validation
	

def publish_workflow(workflow_name: str, draft_revision: int, *, activate: bool = True, reenrollment: str | None = None) -> dict:
	workflow = _workflow(workflow_name, "write", for_update=True)
	draft = _draft(workflow.name, for_update=True)
	if cint(draft.draft_revision) != cint(draft_revision):
		raise AutomationConflictError(_("The draft changed before it could be published."))
	_enabled_user(workflow.execution_user)
	validation = validate_workflow_draft(workflow.name, publish=True)
	if not validation["valid"]:
		raise AutomationError(_("Resolve workflow validation errors before publishing."))
	next_version = cint(workflow.latest_version) + 1
	settings = parse_object(draft.settings_json or "{}", "workflow settings")
	if reenrollment is not None:
		policy = str(reenrollment).upper()
		if policy not in {"NEVER", "AFTER_COMPLETION", "ALWAYS"}:
			raise AutomationError(_("Unsupported re-enrollment policy."))
		settings["reenrollment"] = policy
		draft.settings_json = json.dumps(settings)
		draft.save(ignore_permissions=True)
	publication = workflow_publication_state(workflow, draft)
	if publication["draft_matches_latest_version"]:
		# Retrying publication must not create duplicate immutable versions. If the
		# latest version was intentionally published without activation, the same
		# request can safely activate that existing version.
		if activate and publication["latest_version"] != workflow.active_version:
			frappe.db.set_value(
				"Automation Trigger Subscription",
				{"workflow": workflow.name},
				"active",
				0,
				update_modified=False,
			)
			frappe.db.set_value(
				"Automation Trigger Subscription",
				{"workflow": workflow.name, "workflow_version": publication["latest_version"]},
				"active",
				1,
				update_modified=False,
			)
			workflow.active_version = publication["latest_version"]
			workflow.status = "ACTIVE"
			workflow.state_version = cint(workflow.state_version) + 1
			workflow.save()
			create_audit(workflow.name, "WORKFLOW_VERSION_ACTIVATED", {"version": publication["latest_version"]})
			frappe.publish_realtime(
				"automation_workflow_state",
				{"workflow_id": workflow.name, "status": workflow.status, "active_version": workflow.active_version},
				doctype="Automation Workflow",
				docname=workflow.name,
				after_commit=True,
			)
			publication = workflow_publication_state(workflow, draft)
		return {
			"workflow_id": workflow.name,
			"version": publication["latest_version"],
			"version_no": publication["latest_version_no"],
			"status": workflow.status,
			"unchanged": True,
			"publication": publication,
		}
	version = frappe.get_doc(
		{
			"doctype": "Automation Workflow Version",
			"workflow": workflow.name,
			"version_no": next_version,
			"primary_doctype": workflow.primary_doctype,
			"graph_json": draft.graph_json,
			"settings_json": json.dumps(settings),
			"graph_hash": validation["graph_hash"],
			"published_by": frappe.session.user,
			"published_at": now_datetime(),
			"execution_user": workflow.execution_user,
		}
	).insert(ignore_permissions=True)
	graph = parse_object(draft.graph_json)
	trigger = next(node for node in graph["nodes"] if node["id"] == graph["start_node_id"])
	config = trigger.get("config") or {}
	event_type = {
		"trigger.manual": "MANUAL",
		"trigger.document_insert": "AFTER_INSERT",
		"trigger.document_change": "ON_UPDATE",
		"trigger.schedule": "SCHEDULED",
	}[trigger["type"]]
	frappe.get_doc(
		{
			"doctype": "Automation Trigger Subscription",
			"workflow": workflow.name,
			"workflow_version": version.name,
			"trigger_node_id": trigger["id"],
			"primary_doctype": workflow.primary_doctype,
			"event_type": event_type,
			"dependency_fields_json": json.dumps(sorted(condition_fields(config.get("condition")))),
			"config_json": json.dumps(config),
			"active": 0,
		}
	).insert(ignore_permissions=True)
	previous_status = workflow.status
	previous_active_version = workflow.active_version
	workflow.latest_version = next_version
	if activate:
		frappe.db.set_value(
			"Automation Trigger Subscription",
			{"workflow": workflow.name},
			"active",
			0,
			update_modified=False,
		)
		frappe.db.set_value("Automation Trigger Subscription", {"workflow": workflow.name, "workflow_version": version.name}, "active", 1, update_modified=False)
		workflow.active_version = version.name
		workflow.status = "ACTIVE"
	else:
		workflow.active_version = previous_active_version
		workflow.status = previous_status if previous_active_version else "DRAFT"
	workflow.state_version = cint(workflow.state_version) + 1
	workflow.save()
	create_audit(workflow.name, "WORKFLOW_PUBLISHED", {"version": version.name, "activate": activate})
	frappe.publish_realtime(
		"automation_workflow_state",
		{"workflow_id": workflow.name, "status": workflow.status, "active_version": workflow.active_version},
		doctype="Automation Workflow",
		docname=workflow.name,
		after_commit=True,
	)
	return {
		"workflow_id": workflow.name,
		"version": version.name,
		"version_no": next_version,
		"status": workflow.status,
		"unchanged": False,
		"publication": workflow_publication_state(workflow, draft),
	}


def change_workflow_state(workflow_name: str, status: str) -> dict:
	status = str(status or "").upper()
	if status not in {"ACTIVE", "PAUSED", "DISABLED"}:
		raise AutomationError(_("Unsupported workflow state."))
	workflow = _workflow(workflow_name, "write", for_update=True)
	if status == "ACTIVE" and not workflow.active_version:
		raise AutomationError(_("Publish the workflow before activating it."))
	if status == "ACTIVE":
		validation = validate_published_version(workflow.name, workflow.active_version)
		if not validation["valid"]:
			codes = ", ".join(dict.fromkeys(issue["code"] for issue in validation["issues"][:5]))
			raise AutomationError(
				_("The published version is no longer safe to activate. Restore it to a draft and resolve: {0}.").format(codes)
			)
	workflow.status = status
	workflow.state_version = cint(workflow.state_version) + 1
	workflow.save()
	frappe.db.set_value(
		"Automation Trigger Subscription",
		{"workflow": workflow.name},
		"active",
		0,
		update_modified=False,
	)
	if status == "ACTIVE":
		frappe.db.set_value(
			"Automation Trigger Subscription",
			{"workflow": workflow.name, "workflow_version": workflow.active_version},
			"active",
			1,
			update_modified=False,
		)
	if status == "DISABLED":
		run_names = frappe.get_list(
			"Automation Run",
			filters={"workflow": workflow.name, "status": ["not in", ["COMPLETED", "FAILED", "CANCELLED"]]},
			pluck="name",
			ignore_permissions=True,
			limit=0,
		)
		if run_names:
			frappe.db.set_value(
				"Automation Run Token",
				{"run": ["in", run_names], "status": ["not in", ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]]},
				"status", "CANCELLED", update_modified=False,
			)
			frappe.db.set_value(
				"Automation Timer",
				{"run": ["in", run_names], "status": "ACTIVE"},
				"status",
				"CANCELLED",
				update_modified=False,
			)
		frappe.db.set_value(
			"Automation Run",
			{"workflow": workflow.name, "status": ["not in", ["COMPLETED", "FAILED", "CANCELLED"]]},
			"status",
			"CANCELLED",
			update_modified=False,
		)
	create_audit(workflow.name, f"WORKFLOW_{status}", {})
	frappe.publish_realtime(
		"automation_workflow_state",
		{"workflow_id": workflow.name, "status": status},
		doctype="Automation Workflow",
		docname=workflow.name,
		after_commit=True,
	)
	return {"workflow_id": workflow.name, "status": status, "state_version": workflow.state_version}


def restore_version_to_draft(workflow_name: str, version_name: str, draft_revision: int) -> dict:
	workflow = _workflow(workflow_name, "write", for_update=True)
	draft = _draft(workflow.name, for_update=True)
	if cint(draft.draft_revision) != cint(draft_revision):
		raise AutomationConflictError(_("The draft changed before the version could be restored."))
	version = frappe.get_doc("Automation Workflow Version", version_name)
	if version.workflow != workflow.name:
		raise AutomationError(_("The selected version does not belong to this workflow."))
	validation = validate_graph(version.graph_json, primary_doctype=workflow.primary_doctype)
	if not validation["valid"]:
		raise AutomationError(_("The published version cannot be restored under the current graph schema."))
	# Also surface field-permission binding issues immediately so operators don't
	# encounter a surprise validation failure only when they try to re-publish.
	binding_issues = validate_bindings(validation["graph"], workflow.execution_user, workflow.name)
	validation["issues"].extend(binding_issues)
	draft.graph_json = version.graph_json
	draft.settings_json = version.settings_json or "{}"
	draft.graph_hash = validation["graph_hash"]
	draft.validation_json = json.dumps(validation["issues"])
	draft.draft_revision = cint(draft.draft_revision) + 1
	draft.save(ignore_permissions=True)
	create_audit(workflow.name, "VERSION_RESTORED_TO_DRAFT", {"version": version.name, "draft_revision": draft.draft_revision})
	return {
		"workflow_id": workflow.name,
		"version": version.name,
		"draft_revision": draft.draft_revision,
		"graph": validation["graph"],
		"graph_hash": validation["graph_hash"],
		"validation": validation["issues"],
	}


def list_versions(workflow_name: str) -> list[dict]:
	workflow = _workflow(workflow_name)
	return frappe.get_list(
		"Automation Workflow Version",
		filters={"workflow": workflow.name},
		fields=["name", "version_no", "graph_hash", "published_by", "published_at", "execution_user"],
		order_by="version_no desc",
		limit=100,
	)


def _remap_graph_ids(graph_value: Any) -> dict:
	graph = parse_object(graph_value, "workflow graph")
	node_ids = {node["id"]: str(uuid4()) for node in graph.get("nodes") or []}
	edge_ids = {edge["id"]: str(uuid4()) for edge in graph.get("edges") or []}

	def remap_value(value: Any) -> Any:
		if isinstance(value, dict):
			row = {key: remap_value(item) for key, item in value.items()}
			if row.get("kind") == "node_output" and row.get("node_id") in node_ids:
				row["node_id"] = node_ids[row["node_id"]]
			return row
		if isinstance(value, list):
			return [remap_value(item) for item in value]
		return value

	cloned = remap_value(graph)
	cloned["start_node_id"] = node_ids[graph["start_node_id"]]
	for node in cloned.get("nodes") or []:
		node["id"] = node_ids[node["id"]]
	for edge in cloned.get("edges") or []:
		edge["id"] = edge_ids[edge["id"]]
		edge["source"] = node_ids[edge["source"]]
		edge["target"] = node_ids[edge["target"]]
	return cloned


def clone_workflow_record(workflow_name: str, title: str, version_name: str | None = None, idempotency_key: str | None = None) -> dict:
	source = _workflow(workflow_name)
	if version_name:
		version = frappe.get_doc("Automation Workflow Version", version_name)
		if version.workflow != source.name:
			raise AutomationError(_("The selected version does not belong to this workflow."))
		graph_value, settings_value = version.graph_json, version.settings_json
	else:
		draft = _draft(source.name)
		graph_value, settings_value = draft.graph_json, draft.settings_json
	created = create_workflow_record(
		title=str(title or _("Copy of {0}").format(source.title)).strip(),
		primary_doctype=source.primary_doctype,
		description=source.description or "",
		execution_user=source.execution_user,
		idempotency_key=idempotency_key,
		operation="clone",
		source=f"{source.name}:{version_name or 'DRAFT'}",
	)
	if created.get("deduplicated"):
		return {**created, "source_workflow": source.name}
	result = save_workflow_draft(created["workflow"], 0, _remap_graph_ids(graph_value), parse_object(settings_value or "{}"), "clone")
	create_audit(created["workflow"], "WORKFLOW_CLONED", {"source_workflow": source.name, "source_version": version_name})
	return {**created, **result, "source_workflow": source.name}


def compare_versions(workflow_name: str, left_name: str, right_name: str | None = None) -> dict:
	workflow = _workflow(workflow_name)

	def load(name: str | None) -> tuple[str, dict, dict, str]:
		if not name or name == "DRAFT":
			draft = _draft(workflow.name)
			return "DRAFT", parse_object(draft.graph_json), parse_object(draft.settings_json or "{}"), draft.graph_hash
		version = frappe.get_doc("Automation Workflow Version", name)
		if version.workflow != workflow.name:
			raise AutomationError(_("A selected version does not belong to this workflow."))
		return version.name, parse_object(version.graph_json), parse_object(version.settings_json or "{}"), version.graph_hash

	left_id, left_graph, left_settings, left_hash = load(left_name)
	right_id, right_graph, right_settings, right_hash = load(right_name)
	left_execution = execution_graph(left_graph)
	right_execution = execution_graph(right_graph)
	left_nodes = {node["id"]: node for node in left_execution.get("nodes") or []}
	right_nodes = {node["id"]: node for node in right_execution.get("nodes") or []}
	left_edges = {edge["id"]: edge for edge in left_execution.get("edges") or []}
	right_edges = {edge["id"]: edge for edge in right_execution.get("edges") or []}
	changed_nodes = sorted(node_id for node_id in left_nodes.keys() & right_nodes.keys() if canonical_json(left_nodes[node_id]) != canonical_json(right_nodes[node_id]))
	changed_edges = sorted(edge_id for edge_id in left_edges.keys() & right_edges.keys() if canonical_json(left_edges[edge_id]) != canonical_json(right_edges[edge_id]))
	return {
		"left": {"id": left_id, "hash": left_hash, "execution_hash": execution_graph_hash(left_graph)},
		"right": {"id": right_id, "hash": right_hash, "execution_hash": execution_graph_hash(right_graph)},
		"nodes": {"added": sorted(right_nodes.keys() - left_nodes.keys()), "removed": sorted(left_nodes.keys() - right_nodes.keys()), "changed": changed_nodes},
		"edges": {"added": sorted(right_edges.keys() - left_edges.keys()), "removed": sorted(left_edges.keys() - right_edges.keys()), "changed": changed_edges},
		"settings_changed": canonical_json(left_settings) != canonical_json(right_settings),
		"left_settings": left_settings, "right_settings": right_settings,
	}


def list_suppression_rules(workflow_name: str) -> list[dict]:
	_workflow(workflow_name)
	return frappe.get_list(
		"Automation Suppression Rule", filters={"workflow": workflow_name},
		fields=["name", "title", "enabled", "priority", "reason", "condition_json", "valid_from", "valid_until", "modified"],
		order_by="priority asc, creation asc", limit=200,
	)


def save_suppression_rule(workflow_name: str, values: Any) -> dict:
	workflow = _workflow(workflow_name, "write")
	data = parse_object(values, "suppression rule")
	condition = data.get("condition")
	issues = validate_expression(condition, "condition")
	for predicate in _condition_predicates(condition):
		fieldname = predicate.get("field")
		operator = predicate.get("operator")
		try:
			assert_field_access(
				workflow.primary_doctype,
				fieldname,
				permission_type="read",
				user=workflow.execution_user,
				capability=("condition_scalar", "condition_collection") if operator in {"is_set", "is_not_set"} else "condition_collection" if operator in {"contains_any", "contains_all", "contains_none"} else "condition_scalar",
			)
		except frappe.PermissionError as exc:
			issues.append({"code": "FIELD_PERMISSION", "path": "condition", "message": str(exc)})
	if issues:
		raise AutomationError(_("Suppression rule is invalid: {0}").format(issues[0]["message"]))
	name = data.get("name")
	if name:
		doc = frappe.get_doc("Automation Suppression Rule", name, for_update=True)
		if doc.workflow != workflow.name:
			raise AutomationError(_("Suppression rule does not belong to this workflow."))
	else:
		doc = frappe.new_doc("Automation Suppression Rule")
		doc.workflow = workflow.name
	doc.title = str(data.get("title") or "").strip()
	if not doc.title:
		raise AutomationError(_("Suppression title is required."))
	doc.enabled = cint(data.get("enabled", 1))
	doc.priority = cint(data.get("priority") or 100)
	doc.reason = str(data.get("reason") or "")[:2000]
	doc.condition_json = json.dumps(condition)
	doc.valid_from = data.get("valid_from")
	doc.valid_until = data.get("valid_until")
	doc.save(ignore_permissions=True) if name else doc.insert(ignore_permissions=True)
	create_audit(workflow.name, "SUPPRESSION_RULE_SAVED", {"rule": doc.name})
	return {"rule_id": doc.name}


def delete_suppression_rule(workflow_name: str, rule_name: str) -> dict:
	workflow = _workflow(workflow_name, "write")
	if frappe.db.get_value("Automation Suppression Rule", rule_name, "workflow") != workflow.name:
		raise AutomationError(_("Suppression rule does not belong to this workflow."))
	frappe.delete_doc("Automation Suppression Rule", rule_name, ignore_permissions=True)
	create_audit(workflow.name, "SUPPRESSION_RULE_DELETED", {"rule": rule_name})
	return {"rule_id": rule_name, "deleted": True}


def create_audit(workflow_name: str, event_type: str, payload: dict) -> None:
	frappe.get_doc(
		{
			"doctype": "Automation Audit Event",
			"workflow": workflow_name,
			"event_type": event_type,
			"actor": frappe.session.user,
			"occurred_at": now_datetime(),
			"trace_id": _trace_id(),
			"payload_json": json.dumps(payload),
		}
	).insert(ignore_permissions=True)
