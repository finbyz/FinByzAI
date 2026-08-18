from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe.utils import cint, flt, get_datetime, getdate

from .constants import (
	MAX_CONDITION_DEPTH,
	MAX_EDGES,
	MAX_GRAPH_BYTES,
	MAX_NODES,
	MAX_PREDICATES,
	NODE_TYPES,
	TRIGGER_NODE_TYPES,
)
from .errors import AutomationError
from .registry import NODE_OUTPUT_PATHS

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
VALUE_KINDS = {"literal", "record_field", "node_output"}
def parse_object(value: Any, label: str = "payload") -> dict:
	if isinstance(value, dict):
		return value
	try:
		parsed = json.loads(value or "{}")
	except (TypeError, ValueError):
		raise AutomationError(f"Invalid {label}")
	if not isinstance(parsed, dict):
		raise AutomationError(f"{label.title()} must be a JSON object")
	return parsed


def canonical_json(value: Any) -> str:
	return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def graph_hash(graph: dict) -> str:
	return hashlib.sha256(canonical_json(graph).encode()).hexdigest()


def execution_graph(graph_value: Any) -> dict:
	"""Return the graph fields that can affect runtime behavior.

	Node coordinates are authoring metadata: they remain in the saved draft and
	immutable version payload, but they must not force a new runtime version.
	Collection order is normalized because nodes and edges are addressed by stable
	IDs rather than their array positions.
	"""
	graph = parse_object(graph_value, "workflow graph")
	normalized = {key: value for key, value in graph.items() if key not in {"nodes", "edges"}}
	normalized["nodes"] = sorted(
		(
			{key: value for key, value in node.items() if key != "position"}
			if isinstance(node, dict)
			else node
			for node in (graph.get("nodes") or [])
		),
		key=lambda node: (str(node.get("id") or ""), canonical_json(node)) if isinstance(node, dict) else ("", canonical_json(node)),
	)
	normalized["edges"] = sorted(
		(graph.get("edges") or []),
		key=lambda edge: (str(edge.get("id") or ""), canonical_json(edge)) if isinstance(edge, dict) else ("", canonical_json(edge)),
	)
	return normalized


def execution_graph_hash(graph_value: Any) -> str:
	return hashlib.sha256(canonical_json(execution_graph(graph_value)).encode()).hexdigest()


def empty_graph(primary_doctype: str, trigger_type: str = "trigger.manual") -> dict:
	return {
		"schema_version": 1,
		"primary_doctype": primary_doctype,
		"start_node_id": "trigger-1",
		"nodes": [
			{
				"id": "trigger-1",
				"type": trigger_type,
				"type_version": 1,
				"position": {"x": 120, "y": 160},
				"config": {},
			}
		],
		"edges": [],
	}


def _issue(
	code: str,
	message: str,
	path: str | None = None,
	node_id: str | None = None,
	*,
	line: int | None = None,
	column: int | None = None,
) -> dict:
	issue = {"severity": "error", "code": code, "message": message, "path": path, "node_id": node_id}
	if line is not None:
		issue["line"] = line
	if column is not None:
		issue["column"] = column
	return issue


def _count_predicates(expression: Any) -> int:
	count = 0
	stack = [expression]
	while stack:
		current = stack.pop()
		if not isinstance(current, dict):
			continue
		if current.get("kind") == "predicate":
			count += 1
		children = current.get("children")
		if isinstance(children, list):
			stack.extend(children)
	return count


def validate_expression(expression: Any, path: str = "expression", depth: int = 0) -> list[dict]:
	if not expression:
		return []
	if not isinstance(expression, dict):
		return [_issue("INVALID_CONDITION", "Condition must be an object", path)]
	if depth > MAX_CONDITION_DEPTH:
		return [_issue("CONDITION_TOO_DEEP", f"Condition groups support at most {MAX_CONDITION_DEPTH} nested levels", path)]
	kind = expression.get("kind")
	if kind in {"all", "any"}:
		children = expression.get("children")
		if not isinstance(children, list) or not children:
			return [_issue("EMPTY_CONDITION_GROUP", "Condition group needs at least one rule", path)]
		issues = []
		for index, child in enumerate(children):
			issues.extend(validate_expression(child, f"{path}.children.{index}", depth + 1))
		return issues
	if kind == "not":
		children = expression.get("children")
		if not isinstance(children, list) or len(children) != 1:
			return [_issue("INVALID_NOT_GROUP", "Not group requires exactly one child", path)]
		return validate_expression(children[0], f"{path}.children.0", depth + 1)
	if kind != "predicate":
		return [_issue("INVALID_CONDITION_KIND", "Unsupported condition kind", path)]
	if not expression.get("field"):
		return [_issue("MISSING_CONDITION_FIELD", "Choose a field", f"{path}.field")]
	if expression.get("operator") not in {
		"eq",
		"ne",
		"gt",
		"gte",
		"lt",
		"lte",
		"in",
		"not_in",
		"contains",
		"not_contains",
		"contains_any",
		"contains_all",
		"contains_none",
		"is_set",
		"is_not_set",
	}:
		return [_issue("INVALID_CONDITION_OPERATOR", "Unsupported condition operator", f"{path}.operator")]
	operator = expression.get("operator")
	value = expression.get("value")
	if operator in {"is_set", "is_not_set"}:
		return []
	if value is None:
		return [_issue("MISSING_CONDITION_VALUE", "Choose a comparison value", f"{path}.value")]
	if operator in {"in", "not_in", "contains_any", "contains_all", "contains_none"}:
		if not isinstance(value, list):
			return [_issue("INVALID_CONDITION_VALUE", "This operator requires a list value", f"{path}.value")]
		if not value:
			return [_issue("MISSING_CONDITION_VALUE", "Choose at least one comparison value", f"{path}.value")]
		if any(not isinstance(item, str) or not item.strip() for item in value):
			return [_issue("INVALID_CONDITION_VALUE", "Collection comparisons require non-empty text values", f"{path}.value")]
		return []
	if operator not in {"in", "not_in", "is_set", "is_not_set"} and isinstance(value, (dict, list)):
		return [_issue("INVALID_CONDITION_VALUE", "Condition value must be a scalar literal", f"{path}.value")]
	if operator in {"contains", "not_contains"} and isinstance(value, str) and not value:
		return [_issue("MISSING_CONDITION_VALUE", "Enter text to compare", f"{path}.value")]
	return []


def validate_value_spec(value: Any, path: str) -> list[dict]:
	if not isinstance(value, dict) or value.get("kind") not in VALUE_KINDS:
		return [_issue("INVALID_VALUE_BINDING", "Use a literal, record field, or prior node output value", path)]
	kind = value.get("kind")
	if kind == "record_field" and not str(value.get("field") or "").strip():
		return [_issue("MISSING_VALUE_FIELD", "Record-field values require a field", f"{path}.field")]
	if kind == "node_output":
		issues = []
		if not str(value.get("node_id") or "").strip():
			issues.append(_issue("MISSING_OUTPUT_NODE", "Node-output values require a source node", f"{path}.node_id"))
		if not str(value.get("path") or "").strip():
			issues.append(_issue("MISSING_OUTPUT_PATH", "Node-output values require an output path", f"{path}.path"))
		return issues
	return []


def _nested_value_specs(value: Any, path: str):
	if isinstance(value, dict) and value.get("kind") in VALUE_KINDS:
		yield value, path
		return
	if isinstance(value, dict):
		for key, item in value.items():
			yield from _nested_value_specs(item, f"{path}.{key}")
	elif isinstance(value, list):
		for index, item in enumerate(value):
			yield from _nested_value_specs(item, f"{path}.{index}")


def _node_value_specs(node: dict, index: int):
	config = node.get("config") or {}
	base = f"nodes.{index}.config"
	for assignment_index, assignment in enumerate(config.get("assignments") or []):
		if isinstance(assignment, dict):
			yield assignment.get("value"), f"{base}.assignments.{assignment_index}.value"
	if node.get("type") == "transform.value":
		for value_index, value in enumerate(config.get("values") or []):
			yield value, f"{base}.values.{value_index}"
	if node.get("type") == "action.send_email":
		for key in ("recipient", "subject", "message"):
			yield config.get(key), f"{base}.{key}"
	if node.get("type") == "action.send_sms":
		for key in ("recipient", "message"):
			yield config.get(key), f"{base}.{key}"
	if node.get("type") == "action.webhook":
		yield from _nested_value_specs(config.get("payload"), f"{base}.payload")


def _config_path_value(config: dict, path: str) -> Any:
	value: Any = config
	for key in path.split("."):
		if not isinstance(value, dict):
			return None
		value = value.get(key)
	return value


def _required_config_missing(value: Any) -> bool:
	if value is None or value == "" or value == []:
		return True
	if isinstance(value, dict) and value.get("kind") == "literal":
		return value.get("value") in (None, "", [])
	return False


def validate_graph(graph_value: Any, *, primary_doctype: str | None = None, publish: bool = False) -> dict:
	graph = parse_object(graph_value, "workflow graph")
	issues: list[dict] = []
	if len(canonical_json(graph).encode()) > MAX_GRAPH_BYTES:
		issues.append(_issue("GRAPH_TOO_LARGE", f"Workflow graph exceeds {MAX_GRAPH_BYTES} bytes"))
	if graph.get("schema_version") != 1:
		issues.append(_issue("INVALID_SCHEMA_VERSION", "Only graph schema version 1 is supported"))
	if primary_doctype and graph.get("primary_doctype") != primary_doctype:
		issues.append(_issue("DOCTYPE_MISMATCH", "Graph primary DocType does not match the workflow"))

	nodes = graph.get("nodes")
	edges = graph.get("edges")
	if not isinstance(nodes, list):
		nodes = []
		issues.append(_issue("INVALID_NODES", "Nodes must be a list"))
	if not isinstance(edges, list):
		edges = []
		issues.append(_issue("INVALID_EDGES", "Edges must be a list"))
	if len(nodes) > MAX_NODES:
		issues.append(_issue("TOO_MANY_NODES", f"Workflow supports at most {MAX_NODES} nodes"))
	if len(edges) > MAX_EDGES:
		issues.append(_issue("TOO_MANY_EDGES", f"Workflow supports at most {MAX_EDGES} edges"))

	from .registry import node_catalog
	catalog = node_catalog()
	definitions = {definition["type"]: definition for definition in catalog}
	valid_node_types = set(definitions)

	node_map: dict[str, dict] = {}
	for index, node in enumerate(nodes):
		path = f"nodes.{index}"
		if not isinstance(node, dict):
			issues.append(_issue("INVALID_NODE", "Node must be an object", path))
			continue
		node_id = str(node.get("id") or "").strip()
		if not node_id:
			issues.append(_issue("MISSING_NODE_ID", "Node ID is required", f"{path}.id"))
			continue
		if node_id in node_map:
			issues.append(_issue("DUPLICATE_NODE_ID", f"Duplicate node ID {node_id}", f"{path}.id", node_id))
			continue
		if not ID_PATTERN.match(node_id):
			issues.append(_issue("INVALID_NODE_ID", "Node ID contains unsupported characters or is too long", f"{path}.id", node_id))
		node_map[node_id] = node
		node_type = node.get("type")
		if not isinstance(node_type, str) or node_type not in valid_node_types:
			issues.append(_issue("UNKNOWN_NODE_TYPE", f"Unknown node type {node_type}", f"{path}.type", node_id))
		node_version = cint(node.get("type_version") or 1)
		allowed_versions = {1, 2} if node_type == "action.round_robin" else {1}
		if node_version not in allowed_versions:
			issues.append(_issue("UNKNOWN_NODE_VERSION", "This node version is not supported", f"{path}.type_version", node_id))
		config = node.get("config")
		if config is None:
			config = {}
		elif not isinstance(config, dict):
			issues.append(_issue("INVALID_NODE_CONFIG", "Node config must be an object", f"{path}.config", node_id))
			continue
		if isinstance(node_type, str) and node_type in {"trigger.document_insert", "trigger.document_change", "condition.if_else"}:
			issues.extend(validate_expression(config.get("condition"), f"{path}.config.condition"))
		if node.get("type") == "condition.if_else" and not config.get("condition"):
			issues.append(_issue("MISSING_BRANCH_CONDITION", "If/else requires a condition", f"{path}.config.condition", node_id))
		if node.get("type") == "delay.fixed":
			seconds = cint(config.get("seconds"))
			if seconds < 60 or seconds > 365 * 24 * 60 * 60:
				issues.append(_issue("INVALID_DELAY", "Delay must be between one minute and 365 days", f"{path}.config.seconds", node_id))
		if node.get("type") == "delay.until_date" and not str(config.get("field") or "").strip():
			issues.append(_issue("MISSING_DELAY_FIELD", "Choose a date or datetime field", f"{path}.config.field", node_id))
		if node.get("type") == "condition.switch":
			if not str(config.get("field") or "").strip():
				issues.append(_issue("MISSING_SWITCH_FIELD", "Choose a field to branch on", f"{path}.config.field", node_id))
			cases = config.get("cases")
			if not isinstance(cases, list) or not cases:
				issues.append(_issue("MISSING_SWITCH_CASES", "Add at least one switch case", f"{path}.config.cases", node_id))
			else:
				seen_values: set[str] = set()
				seen_handles: set[str] = set()
				for case_index, case in enumerate(cases):
					case_path = f"{path}.config.cases.{case_index}"
					if not isinstance(case, dict):
						issues.append(_issue("INVALID_SWITCH_CASE", "Switch cases must contain value and handle", case_path, node_id))
						continue
					value = str(case.get("value") or "").strip()
					handle = str(case.get("handle") or "").strip()
					if not value or not handle or handle == "default" or not ID_PATTERN.match(handle):
						issues.append(_issue("INVALID_SWITCH_CASE", "Each switch case needs a value and a unique safe handle", case_path, node_id))
					if value in seen_values or handle in seen_handles:
						issues.append(_issue("DUPLICATE_SWITCH_CASE", "Switch values and handles must be unique", case_path, node_id))
					seen_values.add(value)
					seen_handles.add(handle)
		if node.get("type") == "condition.deduplicate" and not str(config.get("match_field") or "").strip():
			issues.append(_issue("MISSING_DEDUPLICATE_FIELD", "Choose a field to check for duplicates", f"{path}.config.match_field", node_id))
		if node.get("type") == "delay.until_event":
			if not str(config.get("event_topic") or "").strip():
				issues.append(_issue("MISSING_EVENT_TOPIC", "Enter an event topic", f"{path}.config.event_topic", node_id))
			timeout_seconds = cint(config.get("timeout_seconds") or 0)
			if timeout_seconds < 60 or timeout_seconds > 365 * 24 * 60 * 60:
				issues.append(_issue("INVALID_EVENT_TIMEOUT", "Event timeout must be between one minute and 365 days", f"{path}.config.timeout_seconds", node_id))
		if node.get("type") == "delay.business_hours":
			time_pattern = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
			start_time = str(config.get("start_time") or "09:00")
			end_time = str(config.get("end_time") or "17:00")
			if not time_pattern.match(start_time) or not time_pattern.match(end_time) or start_time >= end_time:
				issues.append(_issue("INVALID_BUSINESS_HOURS", "Business hours require valid start and end times, with start before end", f"{path}.config.start_time", node_id))
			weekdays = config.get("weekdays", [0, 1, 2, 3, 4])
			if not isinstance(weekdays, list) or not weekdays or any(isinstance(day, bool) or not isinstance(day, int) or day < 0 or day > 6 for day in weekdays) or len(set(weekdays)) != len(weekdays):
				issues.append(_issue("INVALID_BUSINESS_DAYS", "Choose one or more unique working days", f"{path}.config.weekdays", node_id))
			try:
				ZoneInfo(str(config.get("timezone") or ""))
			except ZoneInfoNotFoundError:
				issues.append(_issue("INVALID_TIMEZONE", "Choose a valid IANA timezone", f"{path}.config.timezone", node_id))
		if node.get("type") == "transform.value":
			if config.get("operation") not in {"coalesce", "concat", "upper", "lower"}:
				issues.append(_issue("INVALID_TRANSFORM", "Choose a supported transform operation", f"{path}.config.operation", node_id))
			values = config.get("values")
			if not isinstance(values, list) or not values:
				issues.append(_issue("MISSING_TRANSFORM_VALUES", "Add at least one transform input", f"{path}.config.values", node_id))
			else:
				for value_index, value in enumerate(values):
					issues.extend(validate_value_spec(value, f"{path}.config.values.{value_index}"))
		if isinstance(node_type, str) and node_type in {"action.update_record", "action.create_record"}:
			assignments = config.get("assignments")
			if not isinstance(assignments, list) or not assignments:
				issues.append(_issue("MISSING_ASSIGNMENTS", "Configure at least one field assignment", f"{path}.config.assignments", node_id))
			else:
				seen_fields = set()
				for assignment_index, assignment in enumerate(assignments):
					assignment_path = f"{path}.config.assignments.{assignment_index}"
					if not isinstance(assignment, dict) or not isinstance(assignment.get("field"), str) or not assignment["field"].strip():
						issues.append(_issue("INVALID_ASSIGNMENT", "Every assignment requires a field", assignment_path, node_id))
						continue
					fieldname = assignment["field"]
					if fieldname in seen_fields:
						issues.append(_issue("DUPLICATE_ASSIGNMENT", "A field can be assigned only once per action", f"{assignment_path}.field", node_id))
					seen_fields.add(fieldname)
					issues.extend(validate_value_spec(assignment.get("value"), f"{assignment_path}.value"))
		if node.get("type") == "action.create_record" and not str(config.get("target_doctype") or "").strip():
			issues.append(_issue("MISSING_TARGET_DOCTYPE", "Choose a target DocType", f"{path}.config.target_doctype", node_id))
		if node.get("type") == "action.create_todo":
			if not str(config.get("allocated_to") or "").strip():
				issues.append(_issue("MISSING_ASSIGNEE", "Choose a ToDo assignee", f"{path}.config.allocated_to", node_id))
			if not str(config.get("description") or "").strip():
				issues.append(_issue("MISSING_TODO_DESCRIPTION", "Enter a ToDo description", f"{path}.config.description", node_id))
			if config.get("priority", "Medium") not in {"Low", "Medium", "High"}:
				issues.append(_issue("INVALID_TODO_PRIORITY", "Unsupported ToDo priority", f"{path}.config.priority", node_id))
		if node.get("type") == "action.add_comment" and not str(config.get("content") or "").strip():
			issues.append(_issue("MISSING_COMMENT", "Enter comment content", f"{path}.config.content", node_id))
		if node.get("type") == "action.notify_user":
			for key, message in (("for_user", "Choose a notification recipient"), ("subject", "Enter a notification subject"), ("message", "Enter a notification message")):
				if not str(config.get(key) or "").strip():
					issues.append(_issue("MISSING_NOTIFICATION_VALUE", message, f"{path}.config.{key}", node_id))
		if node.get("type") == "transform.associated_record":
			for key in ("reference_field", "fetch_field"):
				if not str(config.get(key) or "").strip():
					issues.append(_issue("MISSING_ASSOCIATED_FIELD", "Choose both the link and fetched fields", f"{path}.config.{key}", node_id))
		if node.get("type") == "transform.child_records":
			for key in ("child_table_field", "fetch_field"):
				if not str(config.get(key) or "").strip():
					issues.append(_issue("MISSING_CHILD_FIELD", "Choose both the child table and fetched fields", f"{path}.config.{key}", node_id))
		if node.get("type") == "action.call_subflow" and not str(config.get("subflow_id") or "").strip():
			issues.append(_issue("MISSING_SUBFLOW", "Choose a subflow workflow", f"{path}.config.subflow_id", node_id))
		if node.get("type") == "action.numeric_adjust":
			if not str(config.get("field") or "").strip():
				issues.append(_issue("MISSING_NUMERIC_FIELD", "Choose a numeric field", f"{path}.config.field", node_id))
			if config.get("operation", "add") not in {"add", "subtract", "multiply", "set"}:
				issues.append(_issue("INVALID_NUMERIC_OPERATION", "Choose a supported numeric operation", f"{path}.config.operation", node_id))
			if isinstance(config.get("amount"), bool) or not isinstance(config.get("amount"), (int, float)):
				issues.append(_issue("INVALID_NUMERIC_AMOUNT", "Enter a numeric amount", f"{path}.config.amount", node_id))
		if node.get("type") == "action.manage_association":
			for key in ("target_doctype", "target_name", "link_field"):
				if not str(config.get(key) or "").strip():
					issues.append(_issue("MISSING_ASSOCIATION_VALUE", "Complete the association configuration", f"{path}.config.{key}", node_id))
			if config.get("operation", "link") not in {"link", "unlink"}:
				issues.append(_issue("INVALID_ASSOCIATION_OPERATION", "Choose link or unlink", f"{path}.config.operation", node_id))
		if node.get("type") == "action.round_robin" and not str(config.get("group") or "").strip():
			issues.append(_issue("MISSING_ROUND_ROBIN_GROUP", "Choose an assignment group", f"{path}.config.group", node_id))
		if node.get("type") in {"action.send_email", "action.send_sms"}:
			for key in (("recipient", "subject", "message") if node.get("type") == "action.send_email" else ("recipient", "message")):
				issues.extend(validate_value_spec(config.get(key), f"{path}.config.{key}"))
			if not str(config.get("purpose") or "").strip():
				issues.append(_issue("MISSING_CONSENT_PURPOSE", "Enter the consent purpose", f"{path}.config.purpose", node_id))
		if node.get("type") == "action.webhook":
			if not str(config.get("integration_secret") or "").strip():
				issues.append(_issue("MISSING_INTEGRATION_SECRET", "Choose an integration secret", f"{path}.config.integration_secret", node_id))
			if not str(config.get("url") or "").startswith("https://"):
				issues.append(_issue("INVALID_WEBHOOK_URL", "Webhook URL must use HTTPS", f"{path}.config.url", node_id))
			if not isinstance(config.get("payload"), dict):
				issues.append(_issue("INVALID_WEBHOOK_PAYLOAD", "Webhook payload must be a JSON object", f"{path}.config.payload", node_id))
		definition = definitions.get(node_type, {}) if isinstance(node_type, str) else {}
		for required in (definition.get("authoring_schema") or {}).get("required") or []:
			required_path = str(required.get("path") or "").strip()
			if not required_path or not _required_config_missing(_config_path_value(config, required_path)):
				continue
			issue_path = f"{path}.config.{required_path}"
			if any(issue.get("node_id") == node_id and issue.get("path") == issue_path for issue in issues):
				continue
			issues.append(
				_issue(
					"MISSING_REQUIRED_CONFIG",
					f"{required.get('label') or required_path} is required",
					issue_path,
					node_id,
				)
			)
		if publish and node.get("placeholder"):
			issues.append(_issue("PLACEHOLDER_NODE", "Placeholder nodes cannot be published", path, node_id))

	if sum(
		_count_predicates(config.get("condition"))
		for node in node_map.values()
		if isinstance((config := node.get("config") or {}), dict)
	) > MAX_PREDICATES:
		issues.append(_issue("TOO_MANY_PREDICATES", f"Workflow supports at most {MAX_PREDICATES} predicates"))

	start_id = graph.get("start_node_id")
	if not isinstance(start_id, str) or start_id not in node_map:
		issues.append(_issue("INVALID_START_NODE", "Start node does not exist", "start_node_id"))
	elif not isinstance(node_map[start_id].get("type"), str) or node_map[start_id].get("type") not in TRIGGER_NODE_TYPES:
		issues.append(_issue("INVALID_START_TYPE", "Start node must be a trigger", "start_node_id", start_id))
	if sum(isinstance(node.get("type"), str) and node.get("type") in TRIGGER_NODE_TYPES for node in node_map.values()) != 1:
		issues.append(_issue("TRIGGER_COUNT", "Workflow must contain exactly one trigger"))

	adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_map}
	predecessors: dict[str, list[str]] = {node_id: [] for node_id in node_map}
	incoming: dict[str, int] = {node_id: 0 for node_id in node_map}
	edge_ids: set[str] = set()
	branch_handles: dict[str, set[str]] = {}
	for index, edge in enumerate(edges):
		path = f"edges.{index}"
		if not isinstance(edge, dict):
			issues.append(_issue("INVALID_EDGE", "Edge must be an object", path))
			continue
		edge_id = str(edge.get("id") or "").strip()
		source = edge.get("source")
		target = edge.get("target")
		if not edge_id or edge_id in edge_ids or not ID_PATTERN.match(edge_id):
			issues.append(_issue("INVALID_EDGE_ID", "Edge ID must be present and unique", f"{path}.id"))
		edge_ids.add(edge_id)
		if not isinstance(source, str) or not isinstance(target, str) or source not in node_map or target not in node_map:
			issues.append(_issue("BROKEN_EDGE", "Edge references a missing node", path))
			continue
		if source == target:
			issues.append(_issue("SELF_EDGE", "A node cannot connect to itself", path, source))
			continue
		adjacency[source].append(target)
		predecessors[target].append(source)
		incoming[target] += 1
		source_type = node_map[source].get("type")
		handle = edge.get("source_handle")
		handle = handle if isinstance(handle, str) else ""
		if isinstance(source_type, str) and source_type in {"condition.if_else", "condition.switch", "condition.deduplicate", "delay.until_event"}:
			branch_handles.setdefault(source, set()).add(handle)
		elif handle != "default":
			issues.append(_issue("INVALID_SOURCE_HANDLE", "This node supports only the default output", f"{path}.source_handle", source))

	for node_id, handles in branch_handles.items():
		node = node_map[node_id]
		node_type = node.get("type")
		config = node.get("config") or {}
		expected = {
			"condition.if_else": {"true", "false"},
			"condition.deduplicate": {"duplicate", "unique"},
			"delay.until_event": {"event", "timeout"},
		}.get(node_type)
		if node_type == "condition.switch":
			expected = {"default"} | {
				str(case.get("handle")) for case in config.get("cases") or [] if isinstance(case, dict) and case.get("handle")
			}
		if handles != expected:
			issues.append(_issue("INVALID_BRANCH_EDGES", "Branch outputs must each be connected exactly once", node_id=node_id))
	for node_id, node in node_map.items():
		outgoing = len(adjacency[node_id])
		node_type = node.get("type")
		if isinstance(node_type, str) and node_type in {"condition.if_else", "condition.deduplicate", "delay.until_event"} and outgoing != 2:
			issues.append(_issue("INVALID_BRANCH_COUNT", "This branch needs exactly two outgoing edges", node_id=node_id))
		elif node_type == "condition.switch" and outgoing != len((node.get("config") or {}).get("cases") or []) + 1:
			issues.append(_issue("INVALID_BRANCH_COUNT", "Switch needs one edge per case plus the default edge", node_id=node_id))
		elif node_type == "end.complete" and outgoing:
			issues.append(_issue("END_HAS_EDGE", "End nodes cannot have outgoing edges", node_id=node_id))
		elif node_type == "action.delete_record" and outgoing:
			issues.append(_issue("DELETE_HAS_EDGE", "Delete-record nodes cannot have outgoing edges", node_id=node_id))
		elif (not isinstance(node_type, str) or node_type not in {"condition.if_else", "condition.switch", "condition.deduplicate", "delay.until_event", "end.complete", "action.delete_record"}) and outgoing > 1:
			issues.append(_issue("TOO_MANY_OUTGOING", "Node supports at most one outgoing edge", node_id=node_id))
		if node_id == start_id and incoming[node_id]:
			issues.append(_issue("START_HAS_INCOMING", "Start node cannot have incoming edges", node_id=node_id))

	if isinstance(start_id, str) and start_id in node_map:
		visited: set[str] = set()
		queue = deque([start_id])
		while queue:
			node_id = queue.popleft()
			if node_id in visited:
				continue
			visited.add(node_id)
			queue.extend(adjacency[node_id])
		for unreachable in sorted(set(node_map) - visited):
			issues.append(_issue("UNREACHABLE_NODE", "Node is not reachable from the trigger", node_id=unreachable))

	indegree = dict(incoming)
	queue = deque(node_id for node_id, count in indegree.items() if count == 0)
	processed = 0
	topological_order = []
	while queue:
		node_id = queue.popleft()
		processed += 1
		topological_order.append(node_id)
		for target in adjacency[node_id]:
			indegree[target] -= 1
			if indegree[target] == 0:
				queue.append(target)
	if processed != len(node_map):
		issues.append(_issue("GRAPH_CYCLE", "Workflow graph must be acyclic"))
	elif isinstance(start_id, str) and start_id in node_map:
		dominators: dict[str, set[str]] = {start_id: {start_id}}
		for node_id in topological_order:
			if node_id == start_id:
				continue
			parent_dominators = [dominators[parent] for parent in predecessors[node_id] if parent in dominators]
			dominators[node_id] = {node_id} | (set.intersection(*parent_dominators) if parent_dominators else set())
		for index, node in enumerate(nodes):
			if not isinstance(node, dict):
				continue
			node_id = node.get("id")
			for value, value_path in _node_value_specs(node, index):
				if not isinstance(value, dict):
					continue
				if value.get("kind") != "node_output":
					continue
				reference = value.get("node_id")
				path = f"{value_path}.node_id"
				if not isinstance(reference, str) or reference not in node_map:
					issues.append(_issue("UNKNOWN_OUTPUT_NODE", "Output source node does not exist", path, node_id))
				elif reference not in dominators.get(node_id, set()) - {node_id}:
					issues.append(_issue("UNSAFE_OUTPUT_REFERENCE", "Output source must run on every path before this action", path, node_id))
				else:
					reference_node = node_map[reference]
					reference_type = reference_node.get("type")
					allowed_paths = NODE_OUTPUT_PATHS.get(reference_type)
					path_root = str(value.get("path") or "").split(".", 1)[0]
					if allowed_paths is not None and path_root not in allowed_paths:
						issues.append(_issue("UNKNOWN_OUTPUT_PATH", "Output path is not produced by the source node", f"{value_path}.path", node_id))
	return {"valid": not issues, "issues": issues, "graph": graph, "graph_hash": graph_hash(graph)}


def _normalize_compare(left: Any, right: Any) -> tuple[Any, Any]:
	if isinstance(left, datetime):
		return left, get_datetime(right)
	if isinstance(left, date):
		return left, getdate(right)
	if isinstance(left, bool):
		return left, bool(cint(right))
	if isinstance(left, int):
		return left, cint(right)
	if isinstance(left, float):
		return left, flt(right)
	return left, right


def _collection_values(record: Any, fieldname: str, value: Any) -> list[str]:
	if not isinstance(value, list):
		return []
	link_fieldname = None
	doctype = record.get("doctype") if hasattr(record, "get") else None
	if doctype:
		try:
			field = frappe.get_meta(doctype).get_field(fieldname)
			if field and field.fieldtype == "Table MultiSelect" and field.options:
				child_meta = frappe.get_meta(field.options)
				links = [child for child in child_meta.fields if child.fieldtype == "Link"]
				listed = [child for child in links if child.in_list_view]
				link = listed[0] if len(listed) == 1 else links[0] if len(links) == 1 else None
				link_fieldname = link.fieldname if link else None
		except frappe.DoesNotExistError:
			link_fieldname = None
	result = []
	for item in value:
		if isinstance(item, str):
			candidate = item
		elif link_fieldname and hasattr(item, "get"):
			candidate = item.get(link_fieldname)
		else:
			candidate = None
		candidate = str(candidate or "").strip()
		if candidate and candidate not in result:
			result.append(candidate)
	return result


def _condition_value_is_set(record: Any, fieldname: str, value: Any) -> bool:
	"""Apply stable Frappe emptiness semantics before and after persistence.

	Optional numeric fields are ``None`` on a new document but MariaDB returns
	``0`` after that same blank document is saved. Without field-aware handling,
	``is_set`` changes its answer simply because the document was reloaded.
	"""
	if value in (None, "", []):
		return False
	doctype = record.get("doctype") if hasattr(record, "get") else None
	if doctype and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and value == 0:
		field = frappe.get_meta(doctype).get_field(fieldname)
		if field and field.fieldtype in {"Int", "Float", "Currency", "Percent"} and field.default in (None, ""):
			return False
	return True


def evaluate_expression(expression: Any, record: Any) -> bool:
	if not expression:
		return True
	expression = parse_object(expression, "condition")
	kind = expression.get("kind")
	children = expression.get("children") or []
	if kind == "all":
		return all(evaluate_expression(child, record) for child in children)
	if kind == "any":
		return any(evaluate_expression(child, record) for child in children)
	if kind == "not":
		return not evaluate_expression(children[0], record)
	if kind != "predicate":
		return False
	fieldname = expression.get("field")
	left = record.get(fieldname)
	operator = expression.get("operator")
	right = expression.get("value")
	if operator == "is_set":
		return _condition_value_is_set(record, fieldname, left)
	if operator == "is_not_set":
		return not _condition_value_is_set(record, fieldname, left)
	if operator in {"contains_any", "contains_all", "contains_none"}:
		left_values = set(_collection_values(record, fieldname, left))
		right_values = {str(item).strip() for item in right if str(item).strip()} if isinstance(right, list) else set()
		if operator == "contains_any":
			return bool(left_values.intersection(right_values))
		if operator == "contains_all":
			return right_values.issubset(left_values)
		return left_values.isdisjoint(right_values)
	left, right = _normalize_compare(left, right)
	if operator == "eq":
		return left == right
	if operator == "ne":
		return left != right
	if operator == "gt":
		return left is not None and right is not None and left > right
	if operator == "gte":
		return left is not None and right is not None and left >= right
	if operator == "lt":
		return left is not None and right is not None and left < right
	if operator == "lte":
		return left is not None and right is not None and left <= right
	if operator in {"in", "not_in"}:
		values = right if isinstance(right, list) else [right]
		matched = left in values
		return matched if operator == "in" else not matched
	if operator in {"contains", "not_contains"}:
		matched = str(right or "").casefold() in str(left or "").casefold()
		return matched if operator == "contains" else not matched
	return False


def condition_fields(expression: Any) -> set[str]:
	fields = set()
	stack = [expression]
	while stack:
		current = stack.pop()
		if not isinstance(current, dict):
			continue
		if current.get("kind") == "predicate" and isinstance(current.get("field"), str) and current.get("field"):
			fields.add(current["field"])
		children = current.get("children")
		if isinstance(children, list):
			stack.extend(children)
	return fields


def resolve_value(spec: Any, *, record: Any, outputs: dict[str, Any]) -> Any:
	if not isinstance(spec, dict) or "kind" not in spec:
		return spec
	kind = spec.get("kind")
	if kind == "literal":
		return spec.get("value")
	if kind == "record_field":
		return record.get(spec.get("field"))
	if kind == "node_output":
		value: Any = outputs.get(spec.get("node_id"), {})
		for part in str(spec.get("path") or "").split("."):
			if not part:
				continue
			value = value.get(part) if isinstance(value, dict) else None
		return value
	raise AutomationError("Unsupported value binding")
