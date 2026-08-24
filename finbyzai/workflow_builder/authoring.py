from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, validate_email_address

from . import emailing
from .errors import AutomationConflictError, AutomationError, AutomationPermissionError
from .registry import assert_field_access, doctype_eligibility, field_catalog_result, is_eligible_doctype, round_robin_assignment
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
	window = settings.get("execution_window")
	if isinstance(window, dict) and bool(cint(window.get("enabled"))):
		settings["execution_window"] = {
			"enabled": True,
			"timezone": str(window.get("timezone") or "UTC").strip(),
			"start_time": str(window.get("start_time") or "09:00"),
			"end_time": str(window.get("end_time") or "17:00"),
			"weekdays": window.get("weekdays") if isinstance(window.get("weekdays"), list) else [0, 1, 2, 3, 4],
			"calendar": str(window.get("calendar") or "").strip(),
		}
	else:
		settings.pop("execution_window", None)
	communication = settings.get("communication")
	if isinstance(communication, dict):
		settings["communication"] = {
			"default_sender_name": str(communication.get("default_sender_name") or "").strip()[:140],
			"default_sender_email": str(communication.get("default_sender_email") or "").strip()[:320],
			"default_sms_sender": str(communication.get("default_sms_sender") or "").strip()[:140],
			"stop_on_response": bool(cint(communication.get("stop_on_response"))),
			"mark_responses_read": bool(cint(communication.get("mark_responses_read"))),
		}
		if not any(settings["communication"].values()):
			settings.pop("communication", None)
	else:
		settings.pop("communication", None)
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
		condition_sources = [(config.get("condition"), f"nodes.{node_id}.config.condition")]
		if node_type == "trigger.any":
			condition_sources.extend(
				((entry.get("config") or {}).get("condition"), f"nodes.{node_id}.config.triggers.{entry_index}.config.condition")
				for entry_index, entry in enumerate(config.get("triggers") or [])
				if isinstance(entry, dict) and isinstance(entry.get("config"), dict)
			)
		if node_type == "condition.if_else" and cint(node.get("type_version") or 1) >= 2:
			condition_sources.extend(
				(branch.get("condition"), f"nodes.{node_id}.config.branches.{branch_index}.condition")
				for branch_index, branch in enumerate(config.get("branches") or [])
				if isinstance(branch, dict)
			)
		for expression, expression_path in condition_sources:
			for predicate in _condition_predicates(expression):
				fieldname = str(predicate.get("field") or "").strip()
				if not fieldname:
					# Structural graph validation reports the actionable "Choose a field" issue.
					continue
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
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": expression_path, "message": str(exc)})
		if node_type == "trigger.document_change":
			for field_index, fieldname in enumerate(config.get("watch_fields") or []):
				try:
					assert_field_access(primary_doctype, fieldname, permission_type="read", user=execution_user, capability="scalar_read")
				except frappe.PermissionError as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.watch_fields.{field_index}", "message": str(exc)})
		if node_type == "trigger.any":
			for entry_index, entry in enumerate(config.get("triggers") or []):
				if not isinstance(entry, dict) or entry.get("type") != "trigger.document_change":
					continue
				for field_index, fieldname in enumerate((entry.get("config") or {}).get("watch_fields") or []):
					try:
						assert_field_access(primary_doctype, fieldname, permission_type="read", user=execution_user, capability="scalar_read")
					except frappe.PermissionError as exc:
						issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.triggers.{entry_index}.config.watch_fields.{field_index}", "message": str(exc)})
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
					operation = str(assignment.get("operation") or "set")
					value = assignment.get("value")
					literal_value = value.get("value") if isinstance(value, dict) and value.get("kind") == "literal" else object()
					if node_type == "action.update_record" and target_field.get("required") and (
						operation == "clear" or (operation == "set" and literal_value in (None, "", []))
					):
						issues.append({
							"severity": "error",
							"code": "MANDATORY_FIELD_CLEAR",
							"node_id": node_id,
							"path": f"nodes.{node_id}.config.assignments",
							"message": _("{0} is mandatory and cannot be cleared.").format(
								target_field.get("label") or target_field.get("fieldname")
							),
						})
					source_field = _validate_value_binding(assignment.get("value"), primary_doctype, execution_user)
					_validate_assignment_value_type(assignment.get("value"), target_field, source_field)
				except (frappe.PermissionError, AutomationError) as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.assignments", "message": str(exc)})
		if node_type == "delay.until_date" and str(config.get("mode") or ("literal" if config.get("datetime") else "field")) == "field":
			try:
				assert_field_access(primary_doctype, config.get("field"), permission_type="read", user=execution_user, capability="scalar_read")
				fieldtype = frappe.get_meta(primary_doctype).get_field(config.get("field")).fieldtype
				if fieldtype not in {"Date", "Datetime"}:
					raise AutomationError(_("Wait-until requires a Date or Datetime field."))
			except (frappe.PermissionError, AutomationError, AttributeError) as exc:
				issues.append({"severity": "error", "code": "DELAY_FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.field", "message": str(exc)})
		if node_type in {"condition.switch", "condition.deduplicate"}:
			fieldnames = [config.get("field")] if node_type == "condition.switch" else (
				config.get("match_fields") if cint(node.get("type_version") or 1) >= 2 else [config.get("match_field")]
			)
			for fieldname in fieldnames or []:
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
			# This action creates ordinary Frappe ToDo assignments; it does not
			# overwrite owner or another field on the enrolled document. Validate
			# the same permissions the runtime actually uses.
			if not is_eligible_doctype(primary_doctype, permission_type="read", user=execution_user):
				issues.append({"severity": "error", "code": "DOCTYPE_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Execution user cannot read the enrolled DocType.")})
			if not frappe.has_permission("ToDo", ptype="create", user=execution_user):
				issues.append({"severity": "error", "code": "TODO_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Execution user cannot create Frappe assignments.")})
			assignment = round_robin_assignment(config)
			group = assignment["group"]
			if assignment["assignment_type"] == "group" and group and not frappe.db.exists("User Group", group):
				issues.append({"severity": "error", "code": "INVALID_ROUND_ROBIN_GROUP", "node_id": node_id, "path": f"nodes.{node_id}.config.group", "message": _("Round robin User Group {0} does not exist.").format(group)})
			elif assignment["assignment_type"] == "legacy" and group and frappe.db.exists("User Group", group):
				pass
			else:
				candidates = assignment["users"] if assignment["assignment_type"] == "users" else [item.strip() for item in group.replace(";", ",").split(",") if item.strip()]
				for candidate in candidates:
					user = frappe.db.get_value("User", candidate, ["name", "enabled"], as_dict=True)
					if not user:
						user = frappe.db.get_value("User", {"email": candidate}, ["name", "enabled"], as_dict=True)
					if not user or not user.enabled:
						issues.append({"severity": "error", "code": "INVALID_ROUND_ROBIN_MEMBER", "node_id": node_id, "path": f"nodes.{node_id}.config.group", "message": _("Round robin member {0} is missing or disabled.").format(candidate)})
		if node_type == "action.delete_record":
			if not is_eligible_doctype(primary_doctype, permission_type="delete", user=execution_user):
				issues.append({"severity": "error", "code": "DOCTYPE_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Execution user cannot delete the enrolled DocType.")})
		if node_type == "action.send_email":
			content_mode = str(config.get("content_mode") or ("template" if config.get("email_template") else "inline"))
			keys = ["recipient"]
			if content_mode == "inline":
				keys.extend(["subject", "message"])
			elif content_mode == "template" and config.get("subject_override"):
				keys.append("subject_override")
			for key in keys:
				try:
					_validate_value_binding(config.get(key), primary_doctype, execution_user)
				except frappe.PermissionError as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.{key}", "message": str(exc)})
			if content_mode == "template" and config.get("email_template"):
				try:
					emailing.get_email_template(config.get("email_template"), primary_doctype, check_permission=False)
				except AutomationError as exc:
					issues.append({"severity": "error", "code": "INVALID_EMAIL_TEMPLATE", "node_id": node_id, "path": f"nodes.{node_id}.config.email_template", "message": str(exc)})
			for key in ("sender_email", "reply_to"):
				address = str(config.get(key) or "").strip()
				if address and not validate_email_address(address, throw=False):
					issues.append({"severity": "error", "code": "INVALID_EMAIL_ADDRESS", "node_id": node_id, "path": f"nodes.{node_id}.config.{key}", "message": _("Enter a valid {0} address.").format(key.replace("_", " ").title())})
			sender_email = str(config.get("sender_email") or "").strip()
			if sender_email and not frappe.db.exists("Email Account", {"email_id": sender_email, "enable_outgoing": 1}):
				issues.append({"severity": "error", "code": "UNAUTHORIZED_EMAIL_SENDER", "node_id": node_id, "path": f"nodes.{node_id}.config.sender_email", "message": _("Choose the address of an enabled outgoing Email Account.")})
			subscription_topic = str(config.get("subscription_topic") or "").strip()
			if subscription_topic and primary_doctype != "Lead":
				issues.append({"severity": "error", "code": "SUBSCRIPTION_TOPIC_REQUIRES_LEAD", "node_id": node_id, "path": f"nodes.{node_id}.config.subscription_topic", "message": _("FinbyzReach subscription topics are available only for Lead workflows. Other workflow records use global and record-specific unsubscribe rules.")})
			elif subscription_topic:
				if "finbyzreach" not in frappe.get_installed_apps():
					issues.append({"severity": "error", "code": "REACH_NOT_INSTALLED", "node_id": node_id, "path": f"nodes.{node_id}.config.subscription_topic", "message": _("Finbyz Reach is required for topic-based email preferences.")})
				elif not frappe.db.exists("Subscription Topic", {"name": subscription_topic, "disabled": 0}):
					issues.append({"severity": "error", "code": "INVALID_SUBSCRIPTION_TOPIC", "node_id": node_id, "path": f"nodes.{node_id}.config.subscription_topic", "message": _("Choose an enabled Subscription Topic.")})
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
		if node_type == "action.instagram_message":
			for key in ("recipient_id", "message"):
				try:
					_validate_value_binding(config.get(key), primary_doctype, execution_user)
				except frappe.PermissionError as exc:
					issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.{key}", "message": str(exc)})
			secret = frappe.db.get_value("Automation Integration Secret", config.get("integration_secret"), ["enabled", "allowed_hosts"], as_dict=True)
			if not secret or not secret.enabled:
				issues.append({"severity": "error", "code": "INTEGRATION_SECRET_DISABLED", "node_id": node_id, "path": f"nodes.{node_id}.config.integration_secret", "message": _("Choose an enabled integration secret for Meta.")})
		if node_type == "action.asana":
			try:
				_validate_value_binding_tree(config.get("payload"), primary_doctype, execution_user)
				if config.get("target_gid"):
					_validate_value_binding(config.get("target_gid"), primary_doctype, execution_user)
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.payload", "message": str(exc)})
			if "asana_integration" not in frappe.get_installed_apps():
				issues.append({"severity": "error", "code": "ASANA_APP_REQUIRED", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Install and configure the Asana Integration app before publishing.")})
			else:
				settings = frappe.get_cached_doc("Asana Settings")
				if not settings.enabled or not settings.workspace_gid:
					issues.append({"severity": "error", "code": "ASANA_NOT_CONFIGURED", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Enable Asana Settings and configure its workspace before publishing.")})
		if node_type == "action.create_todo" and not frappe.db.get_value("User", config.get("allocated_to"), "enabled"):
			issues.append({"severity": "error", "code": "INVALID_ASSIGNEE", "node_id": node_id, "path": f"nodes.{node_id}.config.allocated_to", "message": _("Choose an enabled assignee.")})
		if node_type == "action.verify_email":
			try:
				_validate_value_binding(config.get("email"), primary_doctype, execution_user)
			except frappe.PermissionError as exc:
				issues.append({"severity": "error", "code": "FIELD_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config.email", "message": str(exc)})
		if node_type == "action.copy_record" and not doctype_eligibility(primary_doctype, permission_type="create", user=execution_user)["available"]:
			issues.append({"severity": "error", "code": "COPY_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("The execution user cannot create this DocType.")})
		if node_type == "action.merge_contact" and primary_doctype != "Contact":
			issues.append({"severity": "error", "code": "CONTACT_MERGE_DOCTYPE", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("Merge contact can only be used in a Contact workflow.")})
		if node_type == "action.create_note" and not frappe.has_permission("Note", ptype="create", user=execution_user):
			issues.append({"severity": "error", "code": "NOTE_PERMISSION", "node_id": node_id, "path": f"nodes.{node_id}.config", "message": _("The execution user cannot create Notes.")})
		if node_type == "action.remove_from_workflow":
			target = str(config.get("target_workflow") or "")
			if target != "current" and not frappe.db.exists("Automation Workflow", {"name": target, "primary_doctype": primary_doctype}):
				issues.append({"severity": "error", "code": "TARGET_WORKFLOW_NOT_FOUND", "node_id": node_id, "path": f"nodes.{node_id}.config.target_workflow", "message": _("Choose a workflow for the same primary DocType.")})
		if node_type == "action.notify_user" and str(config.get("audience") or "specific") == "specific" and not frappe.db.get_value("User", config.get("for_user"), "enabled"):
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
	window = settings.get("execution_window")
	if isinstance(window, dict):
		time_pattern = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
		start_time = str(window.get("start_time") or "")
		end_time = str(window.get("end_time") or "")
		if not time_pattern.match(start_time) or not time_pattern.match(end_time) or start_time >= end_time:
			issues.append({"severity": "error", "code": "INVALID_EXECUTION_WINDOW", "path": "settings.execution_window.start_time", "message": _("The action window requires valid start and end times, with start before end.")})
		weekdays = window.get("weekdays")
		if not isinstance(weekdays, list) or not weekdays or any(isinstance(day, bool) or not isinstance(day, int) or day < 0 or day > 6 for day in weekdays) or len(set(weekdays)) != len(weekdays):
			issues.append({"severity": "error", "code": "INVALID_EXECUTION_DAYS", "path": "settings.execution_window.weekdays", "message": _("Choose one or more unique action-window days.")})
		try:
			ZoneInfo(str(window.get("timezone") or ""))
		except ZoneInfoNotFoundError:
			issues.append({"severity": "error", "code": "INVALID_EXECUTION_TIMEZONE", "path": "settings.execution_window.timezone", "message": _("Choose a valid IANA timezone for the action window.")})
		calendar = str(window.get("calendar") or "").strip()
		if calendar and not frappe.db.exists("Holiday List", calendar):
			issues.append({"severity": "error", "code": "INVALID_EXECUTION_CALENDAR", "path": "settings.execution_window.calendar", "message": _("The selected action-window Holiday List does not exist.")})
	communication = settings.get("communication") or {}
	sender_email = str(communication.get("default_sender_email") or "").strip()
	if sender_email:
		from frappe.utils import validate_email_address

		if not validate_email_address(sender_email, throw=False):
			issues.append({"severity": "error", "code": "INVALID_DEFAULT_SENDER", "path": "settings.communication.default_sender_email", "message": _("Enter a valid default sender email address.")})
		elif not frappe.db.exists("Email Account", {"email_id": sender_email, "enable_outgoing": 1}):
			issues.append({"severity": "error", "code": "UNAUTHORIZED_DEFAULT_SENDER", "path": "settings.communication.default_sender_email", "message": _("Choose the address of an enabled outgoing Email Account.")})
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
	folder: str = "",
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
			"folder": str(folder or "").strip()[:140],
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
	folder: str | None = None,
) -> dict:
	filters = {"status": status} if status else {}
	if primary_doctype:
		filters["primary_doctype"] = primary_doctype
	if exclude_workflow:
		filters["name"] = ["!=", exclude_workflow]
	if folder is not None:
		filters["folder"] = str(folder).strip()
	needle = str(search or "").strip()
	or_filters = (
		{
			"name": ["like", f"%{needle}%"],
			"title": ["like", f"%{needle}%"],
			"folder": ["like", f"%{needle}%"],
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
		fields=["name", "title", "folder", "primary_doctype", "status", "active_version", "latest_version", "execution_user", "modified", "owner"],
		order_by="modified desc",
		start=max(cint(start), 0),
		limit=limit + 1,
	)
	visible_rows = rows[:limit]
	version_names = list({row.get("active_version") for row in visible_rows if row.get("active_version")})
	version_graphs = (
		{
			row.name: row.graph_json
			for row in frappe.get_all(
				"Automation Workflow Version",
				filters={"name": ["in", version_names]},
				fields=["name", "graph_json"],
				limit_page_length=0,
			)
		}
		if version_names
		else {}
	)
	for row in visible_rows:
		row["trigger_type"] = None
		if row.get("active_version"):
			graph = parse_object(
				version_graphs.get(row.get("active_version")) or "{}",
				"published workflow graph",
			)
			start_node = next(
				(node for node in graph.get("nodes") or [] if node.get("id") == graph.get("start_node_id")),
				None,
			)
			row.trigger_type = start_node.get("type") if start_node else None

	count_filters = {key: value for key, value in filters.items() if key != "status"}
	grouped_counts = frappe.get_list(
		"Automation Workflow",
		filters=count_filters,
		or_filters=or_filters,
		fields=["status", {"COUNT": "name", "as": "count"}],
		group_by="status",
		limit=0,
	)
	counts = {row.status: cint(row.count) for row in grouped_counts}

	return {
		"rows": visible_rows,
		"has_more": len(rows) > limit,
		"total_count": counts.get(status, 0) if status else sum(counts.values()),
		"status_counts": {"ACTIVE": counts.get("ACTIVE", 0), "PAUSED": counts.get("PAUSED", 0)},
	}


def set_workflow_folder(workflow_name: str, folder: str | None) -> dict:
	workflow = _workflow(workflow_name, "write", for_update=True)
	value = str(folder or "").strip()
	if len(value) > 140 or any(part in {".", ".."} for part in value.split("/")):
		raise AutomationError(_("Folder names must be at most 140 characters and cannot contain dot path segments."))
	workflow.folder = value
	workflow.save()
	create_audit(workflow.name, "WORKFLOW_FOLDER_CHANGED", {"folder": value})
	return {"workflow_id": workflow.name, "folder": value}


def _delete_workflow_history(workflow) -> dict[str, int]:
	"""Delete one disabled workflow and every record that owns its runtime state."""
	workflow_name = workflow.name
	version_names = frappe.get_all(
		"Automation Workflow Version",
		filters={"workflow": workflow_name},
		pluck="name",
		limit_page_length=0,
	)
	run_names = frappe.get_all(
		"Automation Run", filters={"workflow": workflow_name}, pluck="name", limit_page_length=0
	)
	schedule_names = frappe.get_all(
		"Automation Schedule", filters={"workflow": workflow_name}, pluck="name", limit_page_length=0
	)
	counts: dict[str, int] = {}

	# Stop every enrollment source before removing any durable state. The caller
	# has already locked the workflow and rejected active execution records.
	for doctype in ("Automation Trigger Subscription", "Automation Inbound Webhook"):
		if frappe.db.table_exists(doctype):
			frappe.db.set_value(
				doctype,
				{"workflow": workflow_name},
				"enabled" if doctype.endswith("Webhook") else "active",
				0,
				update_modified=False,
			)
	if schedule_names:
		frappe.db.set_value(
			"Automation Schedule",
			{"name": ["in", schedule_names]},
			{"enabled": 0, "last_backfill_job": None},
			update_modified=False,
		)

	def remove(doctype: str, filters: dict) -> int:
		if not frappe.db.table_exists(doctype):
			return 0
		names = frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=0)
		if names:
			frappe.db.delete(doctype, {"name": ["in", names]})
		counts[doctype] = counts.get(doctype, 0) + len(names)
		return len(names)

	remove("Automation Backfill Job", {"workflow": workflow_name})
	remove("Automation Schedule", {"workflow": workflow_name})
	remove("Automation Dead Letter", {"workflow": workflow_name})
	remove("Automation Incident", {"workflow": workflow_name})
	if run_names:
		for doctype in (
			"Automation Action Attempt",
			"Automation Timer",
			"Automation Run Token",
			"Automation Run Event",
			"Automation Effect Ledger",
			"Automation Policy Evaluation",
			"Automation Enrollment Decision",
		):
			remove(doctype, {"run": ["in", run_names]})
	remove("Automation Enrollment Decision", {"workflow": workflow_name})
	remove("Automation Policy Evaluation", {"workflow": workflow_name})
	remove("Automation Enrollment Ledger", {"workflow": workflow_name})
	remove("Automation Run", {"workflow": workflow_name})
	remove("Automation Metric Daily", {"workflow": workflow_name})
	remove("Automation Suppression Rule", {"workflow": workflow_name})
	remove("Automation Workflow Comment", {"workflow": workflow_name})
	remove("Automation Trigger Subscription", {"workflow": workflow_name})

	# Password fields have auxiliary auth rows; use the document lifecycle for
	# the small number of webhook definitions instead of a raw bulk delete.
	if frappe.db.table_exists("Automation Inbound Webhook"):
		for name in frappe.get_all(
			"Automation Inbound Webhook",
			filters={"workflow": workflow_name},
			pluck="name",
			limit_page_length=0,
		):
			frappe.delete_doc("Automation Inbound Webhook", name, ignore_permissions=True)
			counts["Automation Inbound Webhook"] = counts.get("Automation Inbound Webhook", 0) + 1

	if version_names:
		for doctype in ("Automation Round Robin Cursor", "Automation Drip Cursor"):
			remove(doctype, {"workflow_version": ["in", version_names]})
	remove("Automation Workflow Draft", {"workflow": workflow_name})
	remove("Automation Audit Event", {"workflow": workflow_name})
	workflow.db_set("active_version", None, update_modified=False)
	remove("Automation Workflow Version", {"workflow": workflow_name})
	frappe.delete_doc("Automation Workflow", workflow_name, ignore_permissions=True)
	counts["Automation Workflow"] = 1
	return counts


def delete_workflow_record(workflow_name: str, delete_history: bool = False) -> dict:
	"""Delete a disposable draft or, for System Managers, a disabled workflow."""
	workflow = _workflow(workflow_name, "write", for_update=True)
	is_system_manager = "System Manager" in frappe.get_roles()
	if workflow.owner != frappe.session.user and not is_system_manager:
		raise AutomationPermissionError(_("Only the workflow owner can delete this draft."))
	has_history = bool(workflow.active_version or cint(workflow.latest_version)) or any(
		frappe.db.exists(doctype, {"workflow": workflow.name})
		for doctype in (
		"Automation Workflow Version",
		"Automation Trigger Subscription",
		"Automation Enrollment Ledger",
		"Automation Run",
		)
	)
	if not has_history and workflow.status == "DRAFT":
		counts = _delete_workflow_history(workflow)
		return {"workflow_id": workflow.name, "deleted": True, "history_deleted": False, "counts": counts}
	if not is_system_manager:
		raise AutomationPermissionError(_("Only a System Manager can permanently delete workflow history."))
	if not cint(delete_history):
		raise AutomationError(_("Confirm permanent deletion of this workflow and its history."))
	if workflow.status in {"ACTIVE", "PAUSED"}:
		raise AutomationError(_("Disable this workflow before permanently deleting it."))
	if frappe.db.exists(
		"Automation Run",
		{"workflow": workflow.name, "status": ["in", ["QUEUED", "RUNNING", "WAITING"]]},
	):
		raise AutomationError(_("This workflow still has active runs. Disable it and wait for cancellation to finish."))
	run_names = frappe.get_all(
		"Automation Run", filters={"workflow": workflow.name}, pluck="name", limit_page_length=0
	)
	if run_names and frappe.db.exists(
		"Automation Effect Ledger",
		{
			"run": ["in", run_names],
			"status": ["in", ["PROCESSING", "STARTED", "UNKNOWN_COMMIT"]],
		},
	):
		raise AutomationError(
			_("This workflow has an external action with unresolved delivery. Wait for it or reconcile it before deleting history.")
		)
	if frappe.db.exists(
		"Automation Backfill Job",
		{"workflow": workflow.name, "status": ["in", ["QUEUED", "RUNNING", "PAUSED"]]},
	):
		raise AutomationError(_("This workflow still has an active backfill. Cancel it before deleting the workflow."))
	counts = _delete_workflow_history(workflow)
	return {"workflow_id": workflow.name, "deleted": True, "history_deleted": True, "counts": counts}


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
	event_type_by_trigger = {
		"trigger.manual": "MANUAL",
		"trigger.document_insert": "AFTER_INSERT",
		"trigger.document_change": "ON_UPDATE",
		"trigger.filter_criteria": "ON_UPDATE",
		"trigger.event": "EVENT",
		"trigger.schedule": "SCHEDULED",
		"trigger.webhook": "WEBHOOK",
	}
	if trigger["type"] == "trigger.any":
		trigger_specs = [
			{
				"id": str(entry.get("id") or f"trigger-{index + 1}"),
				"type": str(entry.get("type") or ""),
				"config": entry.get("config") if isinstance(entry.get("config"), dict) else {},
			}
			for index, entry in enumerate((trigger.get("config") or {}).get("triggers") or [])
			if isinstance(entry, dict)
		]
	else:
		trigger_specs = [{"id": trigger["id"], "type": trigger["type"], "config": trigger.get("config") or {}}]
	for trigger_spec in trigger_specs:
		config = trigger_spec["config"]
		frappe.get_doc(
			{
				"doctype": "Automation Trigger Subscription",
				"workflow": workflow.name,
				"workflow_version": version.name,
				"trigger_node_id": f"{trigger['id']}:{trigger_spec['id']}" if trigger["type"] == "trigger.any" else trigger["id"],
				"primary_doctype": workflow.primary_doctype,
				"event_type": event_type_by_trigger[trigger_spec["type"]],
				"dependency_fields_json": json.dumps(sorted(condition_fields(config.get("condition")) | {str(field) for field in config.get("watch_fields") or [] if field})),
				"config_json": json.dumps({**config, "_trigger_type": trigger_spec["type"], "_trigger_group_id": trigger_spec["id"]}),
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
		cancelled_at = now_datetime()
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
				{
					"status": "CANCELLED",
					"completed_at": cancelled_at,
					"lease_owner": None,
					"lease_until": None,
				},
				update_modified=False,
			)
			frappe.db.set_value(
				"Automation Timer",
				{"run": ["in", run_names], "status": "ACTIVE"},
				{"status": "CANCELLED", "released_at": cancelled_at},
				update_modified=False,
			)
		frappe.db.set_value(
			"Automation Run",
			{"workflow": workflow.name, "status": ["not in", ["COMPLETED", "FAILED", "CANCELLED"]]},
			{"status": "CANCELLED", "completed_at": cancelled_at},
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
