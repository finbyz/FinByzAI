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
from .registry import (
	NODE_OUTPUT_PATHS,
	business_event_catalog,
	business_event_available,
	get_business_event_context,
	get_business_event_definition,
	get_node_definition,
	round_robin_assignment,
)

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
VALUE_KINDS = {"literal", "record_field", "node_output"}


def criteria_branch_outputs(node: dict) -> list[dict]:
	"""Return the ordered output contract for a version-2 criteria branch."""
	if node.get("type") != "condition.if_else" or cint(node.get("type_version") or 1) < 2:
		return []
	config = node.get("config") or {}
	branches = config.get("branches") if isinstance(config, dict) else []
	return [branch for branch in (branches or []) if isinstance(branch, dict)]


def percentage_branch_outputs(node: dict) -> list[dict]:
	"""Return the ordered output contract for a random percentage split."""
	if node.get("type") != "condition.random_split":
		return []
	config = node.get("config") or {}
	branches = config.get("branches") if isinstance(config, dict) else []
	return [branch for branch in (branches or []) if isinstance(branch, dict)]


def event_trigger_entries(config: dict, type_version: int = 1) -> list[dict]:
	"""Normalize legacy single-event and HubSpot-style OR event groups."""
	if cint(type_version or 1) >= 2:
		entries = config.get("events") if isinstance(config, dict) else []
		return [entry for entry in (entries or []) if isinstance(entry, dict)]
	return [
		{
			"id": "legacy-event",
			"event_topic": config.get("event_topic"),
			"event_filter": config.get("event_filter"),
		}
	] if isinstance(config, dict) else []


def event_wait_data_source(config: dict | None) -> str:
	"""Normalize old email-scoped waits into the HubSpot-style data-source contract."""
	config = config if isinstance(config, dict) else {}
	return str(config.get("data_source") or ("action_output" if config.get("event_source") else "enrolled_record"))


def event_wait_timeout_mode(config: dict | None) -> str:
	config = config if isinstance(config, dict) else {}
	return str(config.get("timeout_mode") or "duration")


def _validate_event_filter(
	expression: Any,
	topic: str,
	path: str,
	primary_doctype: str | None = None,
	usage: str = "all",
) -> list[dict]:
	issues = validate_expression(expression, path)
	if not expression:
		return issues
	definition = (
		next((row for row in business_event_catalog(primary_doctype, usage) if row["topic"] == topic), None)
		if primary_doctype
		else get_business_event_definition(topic)
	)
	if not definition:
		return issues
	allowed_fields = {
		str(field.get("fieldname") or "")
		for field in definition.get("filter_fields") or []
		if isinstance(field, dict) and field.get("fieldname")
	}
	for fieldname in sorted(condition_fields(expression) - allowed_fields):
		issues.append(
			_issue(
				"UNKNOWN_EVENT_FILTER_FIELD",
				f"{fieldname} is not available for the selected event",
				path,
			)
		)
	return issues


def event_filter_matches(expression: Any, payload: dict | None) -> bool:
	return evaluate_expression(expression, payload or {})


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
	definition = get_node_definition(trigger_type) or {}
	return {
		"schema_version": 1,
		"primary_doctype": primary_doctype,
		"start_node_id": "trigger-1",
		"nodes": [
			{
				"id": "trigger-1",
				"type": trigger_type,
				"type_version": cint(definition.get("type_version") or 1),
				"position": {"x": 120, "y": 160},
				"config": json.loads(json.dumps(definition.get("default_config") or {})),
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
	source = expression.get("source") if isinstance(expression.get("source"), dict) else None
	if source:
		source_issues = validate_value_spec(source, f"{path}.source")
		if source_issues:
			return source_issues
	if not expression.get("field") and not source:
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


def _condition_value_specs(value: Any, path: str):
	if not isinstance(value, dict):
		return
	if value.get("kind") == "predicate" and isinstance(value.get("source"), dict):
		yield value.get("source"), f"{path}.source"
	for index, child in enumerate(value.get("children") or []):
		yield from _condition_value_specs(child, f"{path}.children.{index}")


def _node_value_specs(node: dict, index: int):
	config = node.get("config") or {}
	base = f"nodes.{index}.config"
	for assignment_index, assignment in enumerate(config.get("assignments") or []):
		if isinstance(assignment, dict) and str(assignment.get("operation") or "set") != "clear":
			yield assignment.get("value"), f"{base}.assignments.{assignment_index}.value"
	if node.get("type") == "transform.value":
		for value_index, value in enumerate(config.get("values") or []):
			yield value, f"{base}.values.{value_index}"
	if node.get("type") == "condition.if_else":
		for branch_index, branch in enumerate(config.get("branches") or []):
			if isinstance(branch, dict):
				yield from _condition_value_specs(branch.get("condition"), f"{base}.branches.{branch_index}.condition")
	if node.get("type") == "action.send_email":
		yield config.get("recipient"), f"{base}.recipient"
		content_mode = str(config.get("content_mode") or ("template" if config.get("email_template") else "inline"))
		for key in (("subject_override",) if content_mode == "template" and config.get("subject_override") else ("subject", "message") if content_mode == "inline" else ()):
			yield config.get(key), f"{base}.{key}"
	if node.get("type") == "action.send_sms":
		for key in ("recipient", "message"):
			yield config.get(key), f"{base}.{key}"
	if node.get("type") == "action.webhook":
		yield from _nested_value_specs(config.get("payload"), f"{base}.payload")
	if node.get("type") == "delay.until_event":
		for key in ("event_source", "event_source_doctype"):
			if config.get(key):
				yield config.get(key), f"{base}.{key}"


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
	workflow_doctype = str(primary_doctype or graph.get("primary_doctype") or "").strip()
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
		allowed_versions = (
			{1, 2}
			if isinstance(node_type, str)
			and node_type
			in {
				"action.round_robin",
				"action.send_email",
				"condition.if_else",
				"condition.deduplicate",
				"transform.value",
				"trigger.event",
				"trigger.any",
				"delay.until_event",
			}
			else {1}
		)
		if node_version not in allowed_versions:
			issues.append(_issue("UNKNOWN_NODE_VERSION", "This node version is not supported", f"{path}.type_version", node_id))
		config = node.get("config")
		if config is None:
			config = {}
		elif not isinstance(config, dict):
			issues.append(_issue("INVALID_NODE_CONFIG", "Node config must be an object", f"{path}.config", node_id))
			continue
		if isinstance(node_type, str) and node_type in {"trigger.document_insert", "trigger.document_change", "trigger.filter_criteria"}:
			issues.extend(validate_expression(config.get("condition"), f"{path}.config.condition"))
			if node_type == "trigger.filter_criteria" and not config.get("condition"):
				issues.append(_issue("MISSING_FILTER_CRITERIA", "Add enrollment filter criteria", f"{path}.config.condition", node_id))
			if node_type == "trigger.document_change":
				watch_fields = config.get("watch_fields", [])
				if not isinstance(watch_fields, list) or len(watch_fields) > 50 or any(not isinstance(field, str) or not field.strip() for field in watch_fields) or len(set(watch_fields)) != len(watch_fields):
					issues.append(_issue("INVALID_WATCH_FIELDS", "Changed-field selection must contain up to fifty unique field names", f"{path}.config.watch_fields", node_id))
		if node.get("type") == "trigger.event":
			entries = event_trigger_entries(config, node_version)
			if not entries:
				issues.append(_issue("MISSING_EVENT_TRIGGER", "Add at least one enrollment event", f"{path}.config.events", node_id))
			elif len(entries) > 20:
				issues.append(_issue("TOO_MANY_EVENT_TRIGGERS", "Event enrollment supports at most twenty OR event groups", f"{path}.config.events", node_id))
			seen_event_ids: set[str] = set()
			for event_index, entry in enumerate(entries):
				entry_path = f"{path}.config.events.{event_index}" if node_version >= 2 else f"{path}.config"
				entry_id = str(entry.get("id") or "").strip()
				topic = str(entry.get("event_topic") or "").strip()
				if node_version >= 2 and (not entry_id or not ID_PATTERN.match(entry_id) or entry_id in seen_event_ids):
					issues.append(_issue("INVALID_EVENT_TRIGGER_ID", "Each enrollment event needs a unique safe id", f"{entry_path}.id", node_id))
				seen_event_ids.add(entry_id)
				if not topic:
					issues.append(_issue("MISSING_EVENT_TOPIC", "Choose a business event", f"{entry_path}.event_topic", node_id))
				elif not business_event_available(topic, workflow_doctype, "trigger"):
					issues.append(
						_issue(
							"EVENT_NOT_AVAILABLE_FOR_WORKFLOW_OBJECT",
							f"{topic} is not an enrollment event for {workflow_doctype}",
							f"{entry_path}.event_topic",
							node_id,
						)
					)
				issues.extend(_validate_event_filter(entry.get("event_filter"), topic, f"{entry_path}.event_filter", workflow_doctype, "trigger"))
			issues.extend(validate_expression(config.get("condition"), f"{path}.config.condition"))
		if node.get("type") == "trigger.any":
			triggers = config.get("triggers")
			legacy_mixed_mode = node_version < 2
			allowed_trigger_types = {"trigger.document_insert", "trigger.document_change", "trigger.event"}
			if legacy_mixed_mode:
				allowed_trigger_types.add("trigger.filter_criteria")
			minimum_groups = 2 if legacy_mixed_mode else 1
			if not isinstance(triggers, list) or len(triggers) < minimum_groups or len(triggers) > 20:
				issues.append(_issue("INVALID_TRIGGER_GROUP_COUNT", f"Add between {minimum_groups} and twenty enrollment triggers", f"{path}.config.triggers", node_id))
			else:
				seen_trigger_ids: set[str] = set()
				seen_trigger_signatures: set[str] = set()
				for trigger_index, trigger_entry in enumerate(triggers):
					trigger_path = f"{path}.config.triggers.{trigger_index}"
					if not isinstance(trigger_entry, dict):
						issues.append(_issue("INVALID_TRIGGER_GROUP", "Each enrollment trigger must be an object", trigger_path, node_id))
						continue
					entry_id = str(trigger_entry.get("id") or "").strip()
					entry_type = str(trigger_entry.get("type") or "").strip()
					entry_config = trigger_entry.get("config") if isinstance(trigger_entry.get("config"), dict) else {}
					if not entry_id or not ID_PATTERN.match(entry_id) or entry_id in seen_trigger_ids:
						issues.append(_issue("INVALID_TRIGGER_GROUP_ID", "Each trigger needs a unique safe id", f"{trigger_path}.id", node_id))
					seen_trigger_ids.add(entry_id)
					signature = canonical_json({"type": entry_type, "config": entry_config})
					if signature in seen_trigger_signatures:
						issues.append(_issue("DUPLICATE_TRIGGER_GROUP", "This trigger is identical to an earlier OR trigger; change its filters or remove it", trigger_path, node_id))
					seen_trigger_signatures.add(signature)
					if entry_type not in allowed_trigger_types:
						issues.append(_issue("INVALID_TRIGGER_GROUP_TYPE", "Event mode supports record-created, record-changed, or installed business events; use Filter mode for criteria enrollment", f"{trigger_path}.type", node_id))
						continue
					issues.extend(validate_expression(entry_config.get("condition"), f"{trigger_path}.config.condition"))
					if entry_type == "trigger.filter_criteria" and not entry_config.get("condition"):
						issues.append(_issue("MISSING_FILTER_CRITERIA", "Add enrollment filter criteria", f"{trigger_path}.config.condition", node_id))
					if entry_type == "trigger.document_change":
						watch_fields = entry_config.get("watch_fields", [])
						if not isinstance(watch_fields, list) or len(watch_fields) > 50 or any(not isinstance(field, str) or not field.strip() for field in watch_fields) or len(set(watch_fields)) != len(watch_fields):
							issues.append(_issue("INVALID_WATCH_FIELDS", "Changed-field selection must contain up to fifty unique field names", f"{trigger_path}.config.watch_fields", node_id))
					if entry_type == "trigger.event":
						topic = str(entry_config.get("event_topic") or "").strip()
						if not topic:
							issues.append(_issue("MISSING_EVENT_TOPIC", "Choose a business event", f"{trigger_path}.config.event_topic", node_id))
						elif not business_event_available(topic, workflow_doctype, "trigger"):
							issues.append(_issue("EVENT_NOT_AVAILABLE_FOR_WORKFLOW_OBJECT", f"{topic} is not an enrollment event for {workflow_doctype}", f"{trigger_path}.config.event_topic", node_id))
						issues.extend(_validate_event_filter(entry_config.get("event_filter"), topic, f"{trigger_path}.config.event_filter", workflow_doctype, "trigger"))
		if node.get("type") == "condition.if_else":
			if node_version == 1:
				issues.extend(validate_expression(config.get("condition"), f"{path}.config.condition"))
				if not config.get("condition"):
					issues.append(_issue("MISSING_BRANCH_CONDITION", "If/else requires a condition", f"{path}.config.condition", node_id))
			else:
				branches = config.get("branches")
				if not isinstance(branches, list) or not branches:
					issues.append(_issue("MISSING_CRITERIA_BRANCHES", "Add at least one named criteria branch", f"{path}.config.branches", node_id))
				elif len(branches) > 20:
					issues.append(_issue("TOO_MANY_CRITERIA_BRANCHES", "If/else supports at most twenty named branches", f"{path}.config.branches", node_id))
				else:
					seen_handles: set[str] = set()
					seen_names: set[str] = set()
					for branch_index, branch in enumerate(branches):
						branch_path = f"{path}.config.branches.{branch_index}"
						if not isinstance(branch, dict):
							issues.append(_issue("INVALID_CRITERIA_BRANCH", "Each branch must be an object", branch_path, node_id))
							continue
						handle = str(branch.get("handle") or "").strip()
						name = str(branch.get("name") or "").strip()
						if not handle or handle == "none" or not ID_PATTERN.match(handle):
							issues.append(_issue("INVALID_CRITERIA_BRANCH_HANDLE", "Each branch needs a unique safe handle", f"{branch_path}.handle", node_id))
						if not name or len(name) > 80 or name.casefold() == "none":
							issues.append(_issue("INVALID_CRITERIA_BRANCH_NAME", "Enter a branch name up to 80 characters; None is reserved", f"{branch_path}.name", node_id))
						if handle in seen_handles or name.casefold() in seen_names:
							issues.append(_issue("DUPLICATE_CRITERIA_BRANCH", "Branch names and handles must be unique", branch_path, node_id))
						seen_handles.add(handle)
						seen_names.add(name.casefold())
						if not branch.get("condition"):
							issues.append(_issue("MISSING_BRANCH_CONDITION", "Each named branch requires criteria", f"{branch_path}.condition", node_id))
						else:
							issues.extend(validate_expression(branch.get("condition"), f"{branch_path}.condition"))
		if node.get("type") == "condition.random_split":
			branches = percentage_branch_outputs(node)
			if len(branches) < 2 or len(branches) > 20:
				issues.append(_issue("INVALID_PERCENTAGE_BRANCH_COUNT", "Add between two and twenty percentage paths", f"{path}.config.branches", node_id))
			seen_handles: set[str] = set()
			seen_names: set[str] = set()
			total = 0.0
			for branch_index, branch in enumerate(branches):
				branch_path = f"{path}.config.branches.{branch_index}"
				handle = str(branch.get("handle") or "").strip()
				name = str(branch.get("name") or "").strip()
				try:
					percentage = float(branch.get("percentage"))
				except (TypeError, ValueError):
					percentage = 0
				if not handle or handle == "default" or not ID_PATTERN.match(handle):
					issues.append(_issue("INVALID_PERCENTAGE_BRANCH_HANDLE", "Each percentage path needs a unique safe handle", f"{branch_path}.handle", node_id))
				if not name or len(name) > 80:
					issues.append(_issue("INVALID_PERCENTAGE_BRANCH_NAME", "Enter a path name up to 80 characters", f"{branch_path}.name", node_id))
				if handle in seen_handles or name.casefold() in seen_names:
					issues.append(_issue("DUPLICATE_PERCENTAGE_BRANCH", "Percentage path names and handles must be unique", branch_path, node_id))
				if percentage <= 0 or percentage > 100:
					issues.append(_issue("INVALID_PERCENTAGE", "Each path percentage must be greater than zero and at most 100", f"{branch_path}.percentage", node_id))
				seen_handles.add(handle)
				seen_names.add(name.casefold())
				total += percentage
			if branches and abs(total - 100.0) > 0.000001:
				issues.append(_issue("INVALID_PERCENTAGE_TOTAL", "Percentage paths must total exactly 100", f"{path}.config.branches", node_id))
		if node.get("type") == "delay.fixed":
			seconds = cint(config.get("seconds"))
			if seconds < 60 or seconds > 365 * 24 * 60 * 60:
				issues.append(_issue("INVALID_DELAY", "Delay must be between one minute and 365 days", f"{path}.config.seconds", node_id))
		if node.get("type") == "delay.drip":
			batch_size = cint(config.get("batch_size"))
			interval = cint(config.get("interval_seconds"))
			if batch_size < 1 or batch_size > 10000:
				issues.append(_issue("INVALID_DRIP_BATCH", "Batch size must be between 1 and 10,000", f"{path}.config.batch_size", node_id))
			if interval < 60 or interval > 365 * 24 * 60 * 60:
				issues.append(_issue("INVALID_DRIP_INTERVAL", "Batch interval must be between one minute and 365 days", f"{path}.config.interval_seconds", node_id))
		if node.get("type") == "delay.until_date":
			mode = str(config.get("mode") or ("literal" if config.get("datetime") else "field"))
			if mode == "field":
				if not str(config.get("field") or "").strip():
					issues.append(_issue("MISSING_DELAY_FIELD", "Choose a date or datetime field", f"{path}.config.field", node_id))
			elif mode == "literal":
				literal = str(config.get("datetime") or "").strip()
				if not literal:
					issues.append(_issue("MISSING_DELAY_DATETIME", "Choose a specific date and time", f"{path}.config.datetime", node_id))
				else:
					try:
						get_datetime(literal)
					except (TypeError, ValueError):
						issues.append(_issue("INVALID_DELAY_DATETIME", "Enter a valid date and time", f"{path}.config.datetime", node_id))
			else:
				issues.append(_issue("INVALID_DELAY_MODE", "Choose a fixed date/time or record field", f"{path}.config.mode", node_id))
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
		if node.get("type") == "condition.deduplicate":
			match_fields = config.get("match_fields") if node_version >= 2 else [config.get("match_field")]
			if not isinstance(match_fields, list) or not match_fields or len(match_fields) > 10 or any(not str(field or "").strip() for field in match_fields) or len(set(match_fields)) != len(match_fields):
				if node_version >= 2:
					issues.append(_issue("INVALID_DEDUPLICATE_FIELDS", "Choose between one and ten unique fields to check for duplicates", f"{path}.config.match_fields", node_id))
				else:
					issues.append(_issue("MISSING_DEDUPLICATE_FIELD", "Choose a field to check for duplicates", f"{path}.config.match_field", node_id))
			if node_version >= 2 and config.get("match_mode", "all") not in {"all", "any"}:
				issues.append(_issue("INVALID_DEDUPLICATE_MODE", "Choose whether all or any selected fields must match", f"{path}.config.match_mode", node_id))
		if node.get("type") == "delay.until_event":
			event_topic = str(config.get("event_topic") or "").strip()
			data_source = event_wait_data_source(config)
			timeout_mode = event_wait_timeout_mode(config)
			if not event_topic:
				issues.append(_issue("MISSING_EVENT_TOPIC", "Enter an event topic", f"{path}.config.event_topic", node_id))
			elif not business_event_available(event_topic, workflow_doctype, "wait"):
				issues.append(
					_issue(
						"EVENT_NOT_AVAILABLE_FOR_WORKFLOW_OBJECT",
						f"{event_topic} is not a wait event for {workflow_doctype}",
						f"{path}.config.event_topic",
						node_id,
					)
				)
			issues.extend(
				_validate_event_filter(
					config.get("event_filter"),
					event_topic,
					f"{path}.config.event_filter",
					workflow_doctype,
					"wait",
				)
			)
			if data_source not in {"enrolled_record", "action_output"}:
				issues.append(_issue("INVALID_EVENT_DATA_SOURCE", "Choose this workflow record or an earlier action output", f"{path}.config.data_source", node_id))
			context = get_business_event_context(event_topic)
			if event_topic and data_source not in set(context.get("source_modes") or ["enrolled_record"]):
				issues.append(_issue("EVENT_DATA_SOURCE_NOT_AVAILABLE", "The selected event is not available for this data source", f"{path}.config.data_source", node_id))
			if data_source == "action_output":
				if not config.get("event_source"):
					issues.append(_issue("MISSING_EVENT_SOURCE", "Choose an earlier action", f"{path}.config.event_source", node_id))
				else:
					issues.extend(validate_value_spec(config.get("event_source"), f"{path}.config.event_source"))
				if config.get("event_source_doctype"):
					issues.extend(validate_value_spec(config.get("event_source_doctype"), f"{path}.config.event_source_doctype"))
			if timeout_mode not in {"duration", "indefinite"}:
				issues.append(_issue("INVALID_EVENT_TIMEOUT_MODE", "Choose a maximum wait or wait indefinitely", f"{path}.config.timeout_mode", node_id))
			elif timeout_mode == "duration":
				timeout_seconds = cint(config.get("timeout_seconds") or 0)
				if timeout_seconds < 60 or timeout_seconds > 365 * 24 * 60 * 60:
					issues.append(_issue("INVALID_EVENT_TIMEOUT", "Event timeout must be between one minute and 365 days", f"{path}.config.timeout_seconds", node_id))
			elif cint(node.get("type_version") or 1) < 2:
				issues.append(_issue("LEGACY_EVENT_WAIT_REQUIRES_TIMEOUT", "Legacy event waits require a maximum wait", f"{path}.config.timeout_mode", node_id))
			elif cint(config.get("branch_on_timeout")):
				issues.append(_issue("INDEFINITE_WAIT_CANNOT_BRANCH", "An indefinite wait has no timeout path", f"{path}.config.branch_on_timeout", node_id))
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
			if config.get("operation") not in {"coalesce", "concat", "upper", "lower", "parse_number", "format_number", "format_phone", "format_currency", "random_number", "math"}:
				issues.append(_issue("INVALID_TRANSFORM", "Choose a supported transform operation", f"{path}.config.operation", node_id))
			values = config.get("values")
			if config.get("operation") != "random_number" and (not isinstance(values, list) or not values):
				issues.append(_issue("MISSING_TRANSFORM_VALUES", "Add at least one transform input", f"{path}.config.values", node_id))
			elif isinstance(values, list):
				for value_index, value in enumerate(values):
					issues.extend(validate_value_spec(value, f"{path}.config.values.{value_index}"))
			if config.get("operation") == "math" and config.get("math_operation", "add") not in {"add", "subtract", "multiply", "divide", "modulo", "power"}:
				issues.append(_issue("INVALID_MATH_OPERATION", "Choose a supported math operation", f"{path}.config.math_operation", node_id))
			if config.get("operation") == "random_number":
				minimum, maximum = config.get("minimum", 0), config.get("maximum", 100)
				if isinstance(minimum, bool) or isinstance(maximum, bool) or not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)) or minimum >= maximum:
					issues.append(_issue("INVALID_RANDOM_RANGE", "Random minimum must be less than maximum", f"{path}.config.minimum", node_id))
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
					operation = str(assignment.get("operation") or "set")
					allowed_operations = {"set"} if node_type == "action.create_record" else {"set", "clear", "append", "remove"}
					if operation not in allowed_operations:
						issues.append(_issue("INVALID_ASSIGNMENT_OPERATION", "Choose a supported field operation", f"{assignment_path}.operation", node_id))
					if fieldname in seen_fields:
						issues.append(_issue("DUPLICATE_ASSIGNMENT", "A field can be assigned only once per action", f"{assignment_path}.field", node_id))
					seen_fields.add(fieldname)
					if operation != "clear":
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
		if node.get("type") == "action.create_note":
			for key in ("title", "content"):
				if not str(config.get(key) or "").strip():
					issues.append(_issue("MISSING_NOTE_VALUE", "Enter a note title and content", f"{path}.config.{key}", node_id))
		if node.get("type") == "action.merge_contact":
			fields = config.get("match_fields")
			if not isinstance(fields, list) or not fields or len(fields) > 3 or any(field not in {"email_id", "phone", "mobile_no"} for field in fields) or len(set(fields)) != len(fields):
				issues.append(_issue("INVALID_CONTACT_MERGE_FIELDS", "Choose one to three unique Contact email or phone fields", f"{path}.config.match_fields", node_id))
			if config.get("match_mode", "all") not in {"all", "any"}:
				issues.append(_issue("INVALID_CONTACT_MERGE_MODE", "Choose whether all or any identity fields must match", f"{path}.config.match_mode", node_id))
		if node.get("type") == "action.verify_email":
			issues.extend(validate_value_spec(config.get("email"), f"{path}.config.email"))
		if node.get("type") == "action.remove_from_workflow" and not str(config.get("target_workflow") or "").strip():
			issues.append(_issue("MISSING_TARGET_WORKFLOW", "Choose the current or another workflow", f"{path}.config.target_workflow", node_id))
		if node.get("type") == "action.complete_goal" and not str(config.get("goal") or "").strip():
			issues.append(_issue("MISSING_GOAL_NAME", "Enter a goal name", f"{path}.config.goal", node_id))
		if node.get("type") == "action.notify_user":
			audience = str(config.get("audience") or "specific")
			if audience not in {"specific", "assigned", "all"}:
				issues.append(_issue("INVALID_NOTIFICATION_AUDIENCE", "Choose a supported notification audience", f"{path}.config.audience", node_id))
			for key, message in ((("for_user", "Choose a notification recipient"),) if audience == "specific" else ()) + (("subject", "Enter a notification subject"), ("message", "Enter a notification message")):
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
		if node.get("type") == "action.round_robin":
			assignment = round_robin_assignment(config)
			if assignment["assignment_type"] not in {"legacy", "group", "users"}:
				issues.append(_issue("INVALID_ROUND_ROBIN_TYPE", "Choose User Group or Specific Users", f"{path}.config.assignment_type", node_id))
			elif assignment["assignment_type"] in {"legacy", "group"} and not assignment["group"]:
				issues.append(_issue("MISSING_ROUND_ROBIN_GROUP", "Choose a User Group", f"{path}.config.group", node_id))
			elif assignment["assignment_type"] == "users" and not assignment["users"]:
				issues.append(_issue("MISSING_ROUND_ROBIN_USERS", "Choose at least one user", f"{path}.config.users", node_id))
		if node.get("type") in {"action.send_email", "action.send_sms"}:
			keys = ("recipient", "message") if node.get("type") == "action.send_sms" else ("recipient",)
			for key in keys:
				issues.extend(validate_value_spec(config.get(key), f"{path}.config.{key}"))
			if node.get("type") == "action.send_email":
				content_mode = str(config.get("content_mode") or ("template" if config.get("email_template") else "inline"))
				if content_mode == "template":
					if not str(config.get("email_template") or "").strip():
						issues.append(_issue("MISSING_EMAIL_TEMPLATE", "Choose an Email Template", f"{path}.config.email_template", node_id))
					if config.get("subject_override"):
						issues.extend(validate_value_spec(config.get("subject_override"), f"{path}.config.subject_override"))
				elif content_mode == "inline":
					for key in ("subject", "message"):
						issues.extend(validate_value_spec(config.get(key), f"{path}.config.{key}"))
				else:
					issues.append(_issue("INVALID_EMAIL_CONTENT_MODE", "Choose Email Template or quick inline content", f"{path}.config.content_mode", node_id))
			if node.get("type") == "action.send_sms" and not str(config.get("purpose") or "").strip():
				issues.append(_issue("MISSING_CONSENT_PURPOSE", "Enter the consent purpose", f"{path}.config.purpose", node_id))
		if node.get("type") == "action.webhook":
			if not str(config.get("integration_secret") or "").strip():
				issues.append(_issue("MISSING_INTEGRATION_SECRET", "Choose an integration secret", f"{path}.config.integration_secret", node_id))
			if not str(config.get("url") or "").startswith("https://"):
				issues.append(_issue("INVALID_WEBHOOK_URL", "Webhook URL must use HTTPS", f"{path}.config.url", node_id))
			if not isinstance(config.get("payload"), dict):
				issues.append(_issue("INVALID_WEBHOOK_PAYLOAD", "Webhook payload must be a JSON object", f"{path}.config.payload", node_id))
		if node.get("type") == "action.instagram_message":
			for key in ("recipient_id", "message"):
				issues.extend(validate_value_spec(config.get(key), f"{path}.config.{key}"))
			if not str(config.get("integration_secret") or "").strip():
				issues.append(_issue("MISSING_INTEGRATION_SECRET", "Choose an integration secret", f"{path}.config.integration_secret", node_id))
			if not str(config.get("url") or "").startswith("https://"):
				issues.append(_issue("INVALID_INSTAGRAM_URL", "Instagram endpoint must use HTTPS", f"{path}.config.url", node_id))
		if node.get("type") == "action.asana":
			operation = str(config.get("operation") or "")
			if operation not in {"create_task", "update_task", "create_subtask", "create_project"}:
				issues.append(_issue("INVALID_ASANA_OPERATION", "Choose a supported Asana operation", f"{path}.config.operation", node_id))
			if not isinstance(config.get("payload"), dict) or not config.get("payload"):
				issues.append(_issue("INVALID_ASANA_PAYLOAD", "Asana fields must be a non-empty JSON object", f"{path}.config.payload", node_id))
			if operation in {"update_task", "create_subtask"}:
				issues.extend(validate_value_spec(config.get("target_gid"), f"{path}.config.target_gid"))
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
		+ sum(_count_predicates(branch.get("condition")) for branch in criteria_branch_outputs(node))
		+ sum(_count_predicates(entry.get("event_filter")) for entry in event_trigger_entries(config, node.get("type_version") or 1) if node.get("type") == "trigger.event")
		+ (_count_predicates(config.get("event_filter")) if node.get("type") == "delay.until_event" else 0)
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
	branch_handle_counts: dict[str, dict[str, int]] = {}
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
		if isinstance(source_type, str) and source_type in {"condition.if_else", "condition.random_split", "condition.switch", "condition.deduplicate", "delay.until_event"}:
			branch_handles.setdefault(source, set()).add(handle)
			counts = branch_handle_counts.setdefault(source, {})
			counts[handle] = counts.get(handle, 0) + 1
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
		if node_type == "delay.until_event" and cint(node.get("type_version") or 1) >= 2:
			expected = {"event", "timeout"} if cint(config.get("branch_on_timeout")) else {"default"}
		if node_type == "condition.if_else" and cint(node.get("type_version") or 1) >= 2:
			expected = {"none"} | {str(branch.get("handle")) for branch in criteria_branch_outputs(node) if branch.get("handle")}
		if node_type == "condition.random_split":
			expected = {str(branch.get("handle")) for branch in percentage_branch_outputs(node) if branch.get("handle")}
		if node_type == "condition.switch":
			expected = {"default"} | {
				str(case.get("handle")) for case in config.get("cases") or [] if isinstance(case, dict) and case.get("handle")
			}
		if expected is not None and not handles.issubset(expected):
			issues.append(_issue("INVALID_BRANCH_EDGES", "An edge uses an output that is not available on this branch", node_id=node_id))
		if any(count > 1 for count in branch_handle_counts.get(node_id, {}).values()):
			issues.append(_issue("INVALID_BRANCH_EDGES", "Each branch output can be connected at most once", node_id=node_id))
	for node_id, node in node_map.items():
		if node.get("type") != "action.go_to":
			continue
		target = str((node.get("config") or {}).get("target_node_id") or "").strip()
		if target not in node_map or target == node_id or target == start_id:
			issues.append(_issue("INVALID_GO_TO_TARGET", "Choose another existing non-trigger step", f"nodes.{node_id}.config.target_node_id", node_id))
			continue
		if adjacency[node_id]:
			issues.append(_issue("GO_TO_HAS_EDGE", "Go to uses its selected destination and cannot also have an outgoing connection", node_id=node_id))
			continue
		adjacency[node_id].append(target)
		predecessors[target].append(node_id)
		incoming[target] += 1
	for node_id, node in node_map.items():
		outgoing = len(adjacency[node_id])
		node_type = node.get("type")
		if node_type == "condition.if_else" and cint(node.get("type_version") or 1) >= 2:
			if outgoing > len(criteria_branch_outputs(node)) + 1:
				issues.append(_issue("INVALID_BRANCH_COUNT", "If/else has more connections than available branches", node_id=node_id))
		elif node_type == "condition.random_split" and outgoing > len(percentage_branch_outputs(node)):
			issues.append(_issue("INVALID_BRANCH_COUNT", "Random split has more connections than percentage paths", node_id=node_id))
		elif node_type == "delay.until_event" and cint(node.get("type_version") or 1) >= 2 and outgoing > (2 if cint((node.get("config") or {}).get("branch_on_timeout")) else 1):
			issues.append(_issue("INVALID_BRANCH_COUNT", "This event delay has more connections than its configured outputs", node_id=node_id))
		elif isinstance(node_type, str) and node_type in {"condition.if_else", "condition.deduplicate", "delay.until_event"} and outgoing > 2:
			issues.append(_issue("INVALID_BRANCH_COUNT", "This branch supports at most two outgoing paths", node_id=node_id))
		elif node_type == "condition.switch" and outgoing > len((node.get("config") or {}).get("cases") or []) + 1:
			issues.append(_issue("INVALID_BRANCH_COUNT", "Switch has more connections than available cases", node_id=node_id))
		elif node_type == "end.complete" and outgoing:
			issues.append(_issue("END_HAS_EDGE", "End nodes cannot have outgoing edges", node_id=node_id))
		elif node_type == "action.delete_record" and outgoing:
			issues.append(_issue("DELETE_HAS_EDGE", "Delete-record nodes cannot have outgoing edges", node_id=node_id))
		elif (not isinstance(node_type, str) or node_type not in {"condition.if_else", "condition.random_split", "condition.switch", "condition.deduplicate", "delay.until_event", "end.complete", "action.delete_record"}) and outgoing > 1:
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
					if value_path.endswith(".event_source"):
						context = get_business_event_context(str((node.get("config") or {}).get("event_topic") or ""))
						allowed_source_types = set(context.get("source_node_types") or [])
						expected_path = "email_queue" if reference_type == "action.send_email" else "name"
						if reference_type not in allowed_source_types or path_root != expected_path:
							issues.append(_issue("INVALID_EVENT_SOURCE", "Choose a compatible earlier action for this event", value_path, node_id))
					if value_path.endswith(".event_source_doctype") and (
						reference_type not in {"action.create_record", "action.copy_record", "action.create_todo"}
						or path_root != "doctype"
					):
						issues.append(_issue("INVALID_EVENT_SOURCE_DOCTYPE", "The earlier action must provide its record type", value_path, node_id))
					if allowed_paths is not None and path_root not in allowed_paths:
						issues.append(_issue("UNKNOWN_OUTPUT_PATH", "Output path is not produced by the source node", f"{value_path}.path", node_id))
			if node.get("type") == "delay.until_event" and event_wait_data_source(node.get("config")) == "action_output":
				config = node.get("config") or {}
				source = config.get("event_source") if isinstance(config.get("event_source"), dict) else {}
				source_node = node_map.get(source.get("node_id"))
				if source_node and source_node.get("type") == "action.send_email":
					source_doctype = config.get("event_source_doctype")
					if source_doctype and (
						not isinstance(source_doctype, dict)
						or source_doctype.get("kind") != "literal"
						or source_doctype.get("value") != "Email Queue"
					):
						issues.append(_issue("INVALID_EMAIL_EVENT_SOURCE_DOCTYPE", "Email action events must use the produced Email Queue message", f"nodes.{index}.config.event_source_doctype", node_id))
				elif source_node:
					source_doctype = config.get("event_source_doctype") if isinstance(config.get("event_source_doctype"), dict) else {}
					if (
						source_doctype.get("kind") != "node_output"
						or source_doctype.get("node_id") != source.get("node_id")
						or source_doctype.get("path") != "doctype"
					):
						issues.append(_issue("MISSING_EVENT_SOURCE_DOCTYPE", "Choose the record output from the same earlier action", f"nodes.{index}.config.event_source_doctype", node_id))
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


def evaluate_expression(expression: Any, record: Any, outputs: dict[str, Any] | None = None) -> bool:
	if not expression:
		return True
	expression = parse_object(expression, "condition")
	kind = expression.get("kind")
	children = expression.get("children") or []
	if kind == "all":
		return all(evaluate_expression(child, record, outputs) for child in children)
	if kind == "any":
		return any(evaluate_expression(child, record, outputs) for child in children)
	if kind == "not":
		return not evaluate_expression(children[0], record, outputs)
	if kind != "predicate":
		return False
	fieldname = expression.get("field")
	source = expression.get("source") if isinstance(expression.get("source"), dict) else None
	if source and source.get("kind") == "node_output":
		left = (outputs or {}).get(str(source.get("node_id") or ""), {})
		for segment in str(source.get("path") or "").split("."):
			left = left.get(segment) if isinstance(left, dict) else None
	elif source and source.get("kind") == "literal":
		left = source.get("value")
	else:
		fieldname = str(source.get("field") or fieldname) if source and source.get("kind") == "record_field" else fieldname
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
