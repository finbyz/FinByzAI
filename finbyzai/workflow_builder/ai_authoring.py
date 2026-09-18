from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime, strip_html_tags
from jsonschema import Draft202012Validator

try:  # pragma: no cover - import shape varies across langchain builds
	from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover
	class BaseCallbackHandler:  # type: ignore
		pass

from . import authoring, registry
from .configuration import (
	ai_authoring_editor_agent,
	ai_authoring_enabled,
	ai_authoring_generator_agent,
	int_setting,
	setting,
)
from .errors import AutomationError
from .node_parameters import describe_node
from .schema import canonical_json, validate_graph


MAX_PROMPT_CHARACTERS = 6000
MAX_GENERATED_NODES = 60
MAX_CHAT_TURNS = 40
MAX_CHAT_HISTORY_IN_PROMPT = 12
CONFIGURED_VALUE_SENTINEL = "__FINBYZ_CONFIGURED_VALUE__"
SENSITIVE_CONFIG_FRAGMENTS = (
	"secret", "password", "token", "credential", "private_key", "api_key",
	"content", "message", "subject", "instructions", "url", "payload",
)
RATE_LIMIT_WINDOW_MINUTES = 10
RATE_LIMIT_REQUESTS = 10
FATAL_GRAPH_ISSUES = {
	"BROKEN_EDGE",
	"DUPLICATE_NODE_ID",
	"GRAPH_CYCLE",
	"INVALID_EDGE_ID",
	"INVALID_NODE_ID",
	"INVALID_NODES",
	"INVALID_EDGES",
	"INVALID_START_NODE",
	"INVALID_START_TYPE",
	"SELF_EDGE",
	"TOO_MANY_EDGES",
	"TOO_MANY_NODES",
	"TRIGGER_COUNT",
	"UNKNOWN_NODE_TYPE",
	"UNREACHABLE_NODE",
}


def _authoring_suggestions(primary_doctype: str | None, execution_user: str | None = None) -> list[str]:
	doctype = str(primary_doctype or "").strip()
	label = doctype or _("record")
	event_topics = {
		str(row.get("topic") or "")
		for row in registry.business_event_catalog(doctype or None, usage="trigger")
	}
	available_nodes = {
		str(row.get("type") or "")
		for row in registry.node_catalog(
			primary_doctype=doctype or None,
			execution_user=execution_user,
		)
		if row.get("available") and not row.get("authoring_hidden") and row.get("authoring_tier") != "danger"
	}
	suggestions: list[str] = []

	def add(value: str, *, action: str | None = None) -> None:
		if action and action not in available_nodes:
			return
		if value not in suggestions:
			suggestions.append(value)

	# Put the object's most useful native/integration events first. The event
	# catalogue already enforces the exact-record resolution boundary, so this
	# never advertises Customer Portal or commerce behavior for unrelated types.
	if "crm.lead.qualified" in event_topics:
		add(
			_("When a {0} becomes Qualified, create a high-priority follow-up ToDo.").format(label),
			action="action.create_todo",
		)
	if "commerce.order.created" in event_topics:
		add(
			_("When a {0} places an order, add a timeline comment and notify its assigned users.").format(label),
			action="action.add_comment",
		)
	if "commerce.order.abandoned" in event_topics:
		add(
			_("When a {0} abandons a cart for 24 hours, create a follow-up ToDo.").format(label),
			action="action.create_todo",
		)
	if "commerce.store.login" in event_topics:
		add(
			_("When a {0} signs in to the customer portal, notify its assigned users.").format(label),
			action="action.notify_user",
		)

	add(
		_("When a new {0} is created, create a follow-up ToDo and notify its assigned users.").format(label),
		action="action.create_todo",
	)
	if "communication.responded" in event_topics:
		add(
			_("When {0} receives a reply, add a timeline comment and notify its assigned users.").format(label),
			action="action.add_comment",
		)
	if "email.clicked" in event_topics:
		add(
			_("When an email link for a {0} record is clicked, create a high-priority follow-up ToDo.").format(label),
			action="action.create_todo",
		)
	if "crm.call.inbound" in event_topics:
		add(
			_("When an inbound call is matched to a {0} record, notify its assigned users.").format(label),
			action="action.notify_user",
		)
	if "crm.form.submitted" in event_topics:
		add(
			_("When a form submission creates or updates {0}, add a timeline comment.").format(label),
			action="action.add_comment",
		)

	add(
		_("When a {0} changes and matches selected criteria, add a timeline comment.").format(label),
		action="action.add_comment",
	)
	if len(suggestions) < 3 and "delay.fixed" in available_nodes:
		add(_("When a new {0} is created, wait one day before the next action.").format(label))
	return suggestions[:3]


def authoring_status(primary_doctype: str | None = None, execution_user: str | None = None) -> dict:
	agent_name = ai_authoring_generator_agent()
	enabled = ai_authoring_enabled()
	reason = None
	if not enabled:
		reason = _("AI workflow authoring is disabled in Automation Settings.")
	elif not agent_name:
		reason = _("Choose an AI Workflow Generator Agent in Automation Settings.")
	elif not frappe.db.exists("AI Agent", agent_name):
		reason = _("The configured AI Workflow Generator Agent no longer exists.")
	else:
		agent = frappe.db.get_value("AI Agent", agent_name, ["agent_type", "llm"], as_dict=True)
		if not agent or agent.agent_type in {"Image Generation Agent", "Gemini Cache Agent"} or not agent.llm:
			reason = _("The authoring agent must use an enabled text-generation LLM.")
		else:
			model = frappe.db.get_value(
				"LLM",
				agent.llm,
				["enabled", "is_embedding_model", "supports_image_generation", "provider"],
				as_dict=True,
			)
			if not model or not cint(model.enabled) or cint(model.is_embedding_model) or cint(model.supports_image_generation):
				reason = _("The authoring agent must use an enabled text-generation LLM.")
			elif cint(frappe.db.get_value("LLM Provider", model.provider, "disabled") or 0):
				reason = _("The configured authoring LLM provider is disabled.")
	return {
		"available": bool(enabled and agent_name and not reason),
		"reason": reason,
		"max_prompt_characters": MAX_PROMPT_CHARACTERS,
		"primary_doctype": str(primary_doctype or "").strip() or None,
		"suggestions": _authoring_suggestions(primary_doctype, execution_user),
	}


def _rate_limit(workflow_name: str) -> None:
	threshold = add_to_date(now_datetime(), minutes=-RATE_LIMIT_WINDOW_MINUTES)
	count = frappe.db.count(
		"Automation Audit Event",
		filters={
			"workflow": workflow_name,
			"actor": frappe.session.user,
			"event_type": "AI_DRAFT_REQUESTED",
			"occurred_at": [">=", threshold],
		},
	)
	if count >= RATE_LIMIT_REQUESTS:
		raise AutomationError(
			_("Too many AI draft requests. Wait a few minutes before trying again.")
		)
	daily_limit = min(max(int_setting("ai_authoring_daily_request_budget", 200), 1), 5000)
	daily_count = frappe.db.count(
		"Automation Audit Event",
		filters={"event_type": "AI_DRAFT_REQUESTED", "occurred_at": [">=", frappe.utils.today()]},
	)
	if daily_count >= daily_limit:
		raise AutomationError(_("The site's daily AI workflow-authoring request budget has been reached."))


def _authoring_agent(mode: str = "generate"):
	status = authoring_status()
	if not status["available"]:
		raise AutomationError(status["reason"] or _("AI workflow authoring is unavailable."))
	agent_name = ai_authoring_editor_agent() if mode == "edit" else ai_authoring_generator_agent()
	if not agent_name or not frappe.db.exists("AI Agent", agent_name):
		raise AutomationError(_("The configured AI workflow authoring agent no longer exists."))
	agent = frappe.get_doc("AI Agent", agent_name)
	if agent.agent_type in {"Image Generation Agent", "Gemini Cache Agent"} or not agent.llm:
		raise AutomationError(_("The authoring agent must use an enabled text-generation LLM."))
	model = frappe.get_doc("LLM", agent.llm)
	if not cint(model.enabled) or cint(model.is_embedding_model) or cint(model.supports_image_generation):
		raise AutomationError(_("The authoring agent must use an enabled text-generation LLM."))
	provider = frappe.get_doc("LLM Provider", model.provider)
	if cint(getattr(provider, "disabled", 0)):
		raise AutomationError(_("The configured authoring LLM provider is disabled."))
	return agent, model


def _response_schema(allowed_types: list[str]) -> dict:
	return {
		"type": "object",
		"additionalProperties": False,
		"required": ["summary", "assumptions", "warnings", "graph"],
		"properties": {
			"summary": {"type": "string", "minLength": 1, "maxLength": 1200},
			"assumptions": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
			"warnings": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
			"graph": {
				"type": "object",
				"additionalProperties": False,
				"required": ["start_node_id", "nodes", "edges"],
				"properties": {
					"start_node_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,120}$"},
					"nodes": {
						"type": "array",
						"minItems": 1,
						"maxItems": MAX_GENERATED_NODES,
						"items": {
							"type": "object",
							"additionalProperties": False,
							"required": ["id", "type", "config"],
							"properties": {
								"id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,120}$"},
								"type": {"type": "string", "enum": allowed_types},
								"config": {"type": "object"},
								"placeholder": {"type": "boolean"},
							},
						},
					},
					"edges": {
						"type": "array",
						"maxItems": MAX_GENERATED_NODES * 3,
						"items": {
							"type": "object",
							"additionalProperties": False,
							"required": ["source", "source_handle", "target"],
							"properties": {
								"source": {"type": "string"},
								"source_handle": {"type": "string"},
								"target": {"type": "string"},
							},
						},
					},
				},
			},
		},
	}


# Valid outgoing-edge ``source_handle`` values per branching node type. Mirrors
# ``schema.validate_graph``'s ``expected`` handle map. Every other node type only
# supports a single ``"default"`` outgoing edge.
_BRANCH_HANDLES = {
	"condition.if_else": ["<branch handle>", "none"],
	"condition.random_split": ["<branch handle>"],
	"condition.switch": ["<case handle>", "default"],
	"condition.deduplicate": ["duplicate", "unique"],
	"delay.until_event": ["event", "timeout"],
	"action.ai_generate": ["success", "low_confidence", "failure"],
	"action.ai_support_agent": ["respond", "handoff", "failure"],
	"action.human_approval": ["approved", "rejected"],
}
_AI_GENERATE_HAPPY_HANDLE = "success"

# Config keys the engine passes through ``resolve_value`` - i.e. the only keys
# that accept a VALUE BINDING ({"kind": literal|record_field|node_output, ...}).
# Verified against engine.py; every other key is a plain literal that CANNOT
# pull data from the record or an upstream node.
_VALUE_BINDING_CONFIG = {
	"action.send_email": ["recipient", "subject", "message", "subject_override"],
	"action.send_sms": ["recipient", "message"],
	"action.instagram_message": ["recipient_id", "message"],
	"action.verify_email": ["email"],
	"action.human_approval": ["draft_text"],
	"action.webhook": ["payload.ANY_KEY"],
	"action.asana": ["target_gid", "payload.ANY_KEY"],
	"action.update_record": ["assignments[].value"],
	"action.create_record": ["assignments[].value"],
	"transform.value": ["values[]"],
	"delay.until_event": ["event_source", "event_source_doctype"],
	"condition.if_else": ["branches[].condition"],
}

# Config keys rendered as Jinja against the enrolled record before the node runs.
# Only these support {{ doc.fieldname }} - everywhere else use a value binding.
_TEMPLATED_CONFIG = {
	"action.ai_generate": ["system_prompt", "user_prompt"],
	"action.ai_support_agent": ["system_prompt", "user_prompt"],
}

# Structural config keys that legitimately hold nested objects (conditions,
# trigger groups, assignment rows); the literal-only scrub must skip them.
# Validation codes that mean "a business value only the user can supply is
# missing". A draft carrying any of these is incomplete, so the conversation
# asks for them instead of handing back a half-filled canvas. Codes NOT listed
# here (MISSING_NODE_ID, MISSING_OUTPUT_PATH, MISSING_CONDITION_*, ...) are the
# model's own mistakes and are handled by the schema retry, not by asking.
_USER_ANSWERABLE_MISSING = {
	# Plain prose or a number - the user can simply say it in the chat.
	"MISSING_AI_PROMPT",
	"MISSING_APPROVAL_TITLE",
	"MISSING_COMMENT",
	"MISSING_CONSENT_PURPOSE",
	"MISSING_DELAY_DATETIME",
	"MISSING_GOAL_NAME",
	"MISSING_NOTE_VALUE",
	"MISSING_NOTIFICATION_VALUE",
	"MISSING_TODO_DESCRIPTION",
}

# These need a specific RECORD (a user, an LLM, a template, a secret). Asking for
# one by name in a chat box invites typos and names that do not exist, and the
# node inspector already has a proper searchable picker with permissions. So the
# draft leaves them blank, marks the node, and tells the user which node to open.
_PICK_IN_INSPECTOR_MISSING = {
	"INVALID_ASSIGNEE",
	"INVALID_RECIPIENT",
	"MISSING_AI_KNOWLEDGE",
	"MISSING_AI_MODEL",
	"MISSING_AI_PROFILE",
	"MISSING_APPROVAL_REVIEWER",
	"MISSING_ASSIGNEE",
	"MISSING_ASSIGNMENTS",
	"MISSING_EMAIL_TEMPLATE",
	"MISSING_EVENT_TOPIC",
	"MISSING_FILTER_CRITERIA",
	"MISSING_INTEGRATION_SECRET",
	"MISSING_ROUND_ROBIN_GROUP",
	"MISSING_ROUND_ROBIN_USERS",
	"MISSING_SUBFLOW",
	"MISSING_TARGET_DOCTYPE",
}
# How many times one thread may be sent back for missing values before the
# draft is handed over with placeholders anyway. Stops an ask/answer loop.
MAX_COMPLETENESS_GATES = 2

_STRUCTURAL_CONFIG_KEYS = {
	"condition", "triggers", "branches", "assignments", "values", "cases",
	"payload", "events", "match_fields", "watch_fields", "field_allowlist",
}


def _safe_catalog(workflow) -> tuple[list[dict], list[dict], list[dict]]:
	items = []
	for definition in registry.node_catalog(
		primary_doctype=workflow.primary_doctype,
		execution_user=workflow.execution_user,
	):
		if not definition.get("available") or definition.get("authoring_hidden"):
			continue
		if definition.get("authoring_tier") == "danger":
			continue
		node_type = definition["type"]
		required = [
			{"path": row.get("path"), "label": row.get("label")}
			for row in ((definition.get("authoring_schema") or {}).get("required") or [])
			if isinstance(row, dict) and row.get("path")
		]
		binding_keys = _VALUE_BINDING_CONFIG.get(node_type, [])
		templated_keys = _TEMPLATED_CONFIG.get(node_type, [])
		item = {
			"type": node_type,
			"type_version": cint(definition.get("type_version") or 1),
			"label": definition.get("label"),
			"description": definition.get("description"),
			"default_config": definition.get("default_config") or {},
			"output_paths": definition.get("output_paths") or [],
			"edge_handles": _BRANCH_HANDLES.get(node_type, ["default"]),
			# Fully described parameters: type, default, required, what the key
			# accepts (literal / value_binding / jinja_template) and what it means.
			**describe_node(
				definition,
				required_paths=[str(row["path"]) for row in required],
				binding_keys=binding_keys,
				templated_keys=templated_keys,
			),
		}
		items.append(item)
	read_fields = registry.field_catalog(
		workflow.primary_doctype,
		permission_type="read",
		user=workflow.execution_user,
	)[:150]
	write_fields = registry.field_catalog(
		workflow.primary_doctype,
		permission_type="write",
		user=workflow.execution_user,
	)[:150]
	field_keys = ("fieldname", "label", "fieldtype", "options", "required")
	return (
		items,
		[{key: row.get(key) for key in field_keys} for row in read_fields],
		[{key: row.get(key) for key in field_keys} for row in write_fields],
	)


def _safe_current_graph(graph: dict | None) -> dict | None:
	"""Expose structure to the model without stored literals or credentials."""
	if not graph:
		return None

	def clean(value: Any, key: str = "", depth: int = 0):
		if depth > 12:
			return "[TRUNCATED]"
		lower = key.lower()
		if key == "value" or any(fragment in lower for fragment in SENSITIVE_CONFIG_FRAGMENTS):
			return CONFIGURED_VALUE_SENTINEL if value not in (None, "", [], {}) else value
		if isinstance(value, dict):
			return {str(item_key): clean(item, str(item_key), depth + 1) for item_key, item in value.items()}
		if isinstance(value, list):
			return [clean(item, key, depth + 1) for item in value[:100]]
		if isinstance(value, str):
			return strip_html_tags(value)[:1000]
		return value

	return clean(graph)


def _restore_configured_values(value: Any, original: Any) -> Any:
	if value == CONFIGURED_VALUE_SENTINEL:
		return deepcopy(original)
	if isinstance(value, dict):
		original_dict = original if isinstance(original, dict) else {}
		return {key: _restore_configured_values(item, original_dict.get(key)) for key, item in value.items()}
	if isinstance(value, list):
		original_list = original if isinstance(original, list) else []
		return [
			_restore_configured_values(item, original_list[index] if index < len(original_list) else None)
			for index, item in enumerate(value)
		]
	return value


def _response_text(response) -> str:
	content = getattr(response, "content", response)
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		return "\n".join(
			str(item.get("text") if isinstance(item, dict) else item)
			for item in content
			if item
		)
	return str(content or "")


def _provider_usage(response) -> dict[str, int]:
	metadata = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", None) or {}
	if hasattr(metadata, "model_dump"):
		metadata = metadata.model_dump()
	if not isinstance(metadata, dict):
		return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
	tokens = metadata.get("token_usage") if isinstance(metadata.get("token_usage"), dict) else metadata
	input_tokens = cint(tokens.get("input_tokens") or tokens.get("prompt_tokens") or 0)
	output_tokens = cint(tokens.get("output_tokens") or tokens.get("completion_tokens") or 0)
	return {
		"input_tokens": input_tokens,
		"output_tokens": output_tokens,
		"total_tokens": cint(tokens.get("total_tokens") or input_tokens + output_tokens),
	}


def _parse_response(response, schema: dict) -> dict:
	text = _response_text(response).strip()
	if text.startswith("```"):
		text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
		text = re.sub(r"\s*```$", "", text)
	try:
		result = json.loads(text)
	except (TypeError, ValueError) as exc:
		raise AutomationError(_("AI returned an invalid draft format. Try a clearer request.")) from exc
	errors = sorted(Draft202012Validator(schema).iter_errors(result), key=lambda error: list(error.path))
	if errors:
		path = ".".join(str(part) for part in errors[0].path) or "response"
		raise AutomationError(
			_("AI draft failed schema validation at {0}: {1}").format(path, errors[0].message)
		)
	return result


def _normalise_graph(raw_graph: dict, workflow, definitions: dict[str, dict], current_graph: dict | None = None) -> dict:
	current_nodes = {
		str(node.get("id")): node
		for node in (current_graph or {}).get("nodes") or []
		if isinstance(node, dict) and node.get("id")
	}
	nodes = []
	for index, raw in enumerate(raw_graph.get("nodes") or []):
		definition = definitions[raw["type"]]
		config = deepcopy(definition.get("default_config") or {})
		original_config = (current_nodes.get(str(raw["id"])) or {}).get("config") or {}
		config.update(_restore_configured_values(deepcopy(raw.get("config") or {}), original_config))
		node = {
			"id": raw["id"],
			"type": raw["type"],
			"type_version": cint(definition.get("type_version") or 1),
			"position": {"x": 360, "y": 140 + index * 170},
			"config": config,
		}
		if raw.get("placeholder"):
			node["placeholder"] = True
		nodes.append(node)
	edges = [
		{
			"id": f"ai-edge-{index + 1}",
			"source": row["source"],
			"source_handle": row.get("source_handle") or "default",
			"target": row["target"],
		}
		for index, row in enumerate(raw_graph.get("edges") or [])
	]
	return {
		"schema_version": 1,
		"primary_doctype": workflow.primary_doctype,
		"start_node_id": raw_graph["start_node_id"],
		"nodes": nodes,
		"edges": edges,
	}


def _clean_messages(values: list[str]) -> list[str]:
	return [strip_html_tags(str(value)).strip()[:500] for value in values if str(value).strip()]


# ---------------------------------------------------------------------------
# Clarifying-question response (the agent may answer with a follow-up question
# instead of a draft when the request is missing information it needs).
# ---------------------------------------------------------------------------
_QUESTION_SCHEMA = {
	"type": "object",
	"required": ["message"],
	"properties": {
		"reply_type": {"type": "string"},
		"message": {"type": "string", "minLength": 1, "maxLength": 1200},
		"questions": {
			"type": "array",
			"maxItems": 6,
			"items": {"type": "string", "maxLength": 300},
		},
		"suggestions": {
			"type": "array",
			"maxItems": 8,
			"items": {"type": "string", "maxLength": 200},
		},
	},
}


def _string_list_items(value: Any) -> list[str]:
	"""Coerce a value that should be a list of strings but may arrive as a dict,
	a single string, or a list holding dicts the model wrapped."""
	if value in (None, "", [], {}):
		return []
	if isinstance(value, str):
		return [value.strip()] if value.strip() else []
	if isinstance(value, dict):
		# e.g. {"placeholder": true, "message": "..."} -> ["..."]
		msg = value.get("message") or value.get("text") or value.get("detail")
		value = [msg] if msg else list(value.values())
	out: list[str] = []
	for item in value if isinstance(value, list) else [value]:
		if isinstance(item, dict):
			item = item.get("message") or item.get("text") or item.get("detail") or json.dumps(item, default=str)
		text = str(item).strip()
		if text and text.lower() not in {"true", "false", "none"}:
			out.append(text)
	return out


_SET_OPERATORS = {"is_set", "is_not_set"}
_CONDITION_OPERATORS = {
	"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "not_contains",
	"contains_any", "contains_all", "contains_none", "is_set", "is_not_set",
}
_OPERATOR_SYNONYMS = {
	"is": "eq", "equals": "eq", "equal": "eq", "==": "eq", "=": "eq",
	"is_not": "ne", "isnt": "ne", "not_equals": "ne", "!=": "ne", "<>": "ne",
	"greater_than": "gt", ">": "gt", "greater_or_equal": "gte", ">=": "gte",
	"less_than": "lt", "<": "lt", "less_or_equal": "lte", "<=": "lte",
	"present": "is_set", "exists": "is_set", "is_present": "is_set",
	"not_empty": "is_set", "is_not_empty": "is_set", "notnull": "is_set", "not_null": "is_set",
	"absent": "is_not_set", "empty": "is_not_set", "is_empty": "is_not_set",
	"missing": "is_not_set", "isnull": "is_not_set", "is_null": "is_not_set",
	"includes": "contains", "has": "contains", "one_of": "in", "any_of": "in",
}


def _repair_condition(cond: Any) -> Any:
	"""Coerce a loose condition the model emitted into the engine's exact shape:
	``{kind: "predicate", field, operator, value?}`` / all|any|not groups.
	A prose string ("recording is present") is not salvageable - drop it so the
	node reads as "needs a condition" rather than "invalid condition"."""
	if not isinstance(cond, dict):
		return None
	kind = cond.get("kind")
	if kind in {"all", "any", "not"}:
		cond["children"] = [_repair_condition(c) for c in cond.get("children") or []]
		return cond
	if kind == "predicate" or (not kind and (cond.get("field") or cond.get("source"))):
		cond["kind"] = "predicate"
		op = str(cond.get("operator") or "").strip().lower().replace(" ", "_")
		op = _OPERATOR_SYNONYMS.get(op, op)
		if op not in _CONDITION_OPERATORS:
			op = "is_set"
		cond["operator"] = op
		if op in _SET_OPERATORS:
			cond.pop("value", None)
		return cond
	return cond


_TRIGGER_TYPES = {
	"trigger.manual", "trigger.document_insert", "trigger.document_change",
	"trigger.filter_criteria", "trigger.event", "trigger.schedule",
	"trigger.webhook", "trigger.any",
}


def _demote_extra_triggers(graph: dict, allowed_types: set[str]) -> None:
	"""A graph may hold exactly ONE trigger. Models routinely add a second
	``trigger.filter_criteria`` as a mid-flow "check that field" step; that is a
	``condition.if_else``. Convert the extras rather than failing the draft."""
	nodes = [n for n in graph.get("nodes") or [] if isinstance(n, dict)]
	triggers = [n for n in nodes if str(n.get("type") or "") in _TRIGGER_TYPES]
	if len(triggers) < 2:
		return
	start_id = str(graph.get("start_node_id") or "")
	# Keep the declared start, else the first trigger in document order.
	keep = next((n for n in triggers if n.get("id") == start_id), triggers[0])
	graph["start_node_id"] = keep["id"]
	if "condition.if_else" not in allowed_types:
		return
	for node in triggers:
		if node is keep:
			continue
		condition = (node.get("config") or {}).get("condition")
		node["type"] = "condition.if_else"
		node["type_version"] = 2
		node["config"] = {
			"branches": [{"handle": "branch-1", "name": "Matches", "condition": condition}]
		}
		if not condition:
			node["placeholder"] = True
		# Its outgoing edges carried "default"; a branch node needs a real handle.
		for edge in graph.get("edges") or []:
			if isinstance(edge, dict) and edge.get("source") == node["id"] and edge.get("source_handle") in (None, "", "default"):
				edge["source_handle"] = "branch-1"


def _unwrap_standalone_filter(graph: dict) -> None:
	"""``trigger.any`` v2 forbids a ``trigger.filter_criteria`` group. When the
	model wrapped a single filter that way, promote it to a standalone
	``trigger.filter_criteria`` node so it validates."""
	for node in graph.get("nodes") or []:
		if not isinstance(node, dict) or node.get("type") != "trigger.any":
			continue
		triggers = (node.get("config") or {}).get("triggers") or []
		if len(triggers) == 1 and isinstance(triggers[0], dict) and triggers[0].get("type") == "trigger.filter_criteria":
			inner = triggers[0].get("config") or {}
			node["type"] = "trigger.filter_criteria"
			node["type_version"] = 1
			node["config"] = {"condition": inner.get("condition")}


def _reference_exists(doctype: str, name: str) -> bool:
	"""True when a model/agent/KB the user named actually exists (and is enabled)."""
	try:
		if not frappe.db.exists(doctype, name):
			return False
		if doctype == "LLM":
			return bool(cint(frappe.db.get_value("LLM", name, "enabled")))
		return True
	except Exception:
		return False


def _closest_node_type(bad: str, allowed: set[str]) -> str | None:
	"""Map an obvious hallucination onto a real node type (only when confident)."""
	low = bad.strip().lower()
	if low in allowed:
		return low
	if "transcri" in low or low.startswith("ai.") or "summar" in low or low in {"action.transcribe", "action.ai", "ai_generate"}:
		return "action.ai_generate" if "action.ai_generate" in allowed else None
	if "task" in low or "todo" in low:
		for candidate in ("action.create_todo", "action.create_record"):
			if candidate in allowed:
				return candidate
	if low in {"action.create", "action.new_record", "action.new", "action.insert"}:
		return "action.create_record" if "action.create_record" in allowed else None
	# Exactly one allowed type extends the given prefix (e.g. "delay" -> "delay.fixed").
	prefix_hits = [t for t in allowed if t.startswith(low + ".") or t == low]
	if len(prefix_hits) == 1:
		return prefix_hits[0]
	# The model over-qualified a real type (e.g. "action.send_email.now").
	base_hits = [t for t in allowed if low.startswith(t + ".")]
	if len(base_hits) == 1:
		return base_hits[0]
	return None


def _repair_ai_payload(payload: dict, allowed_types: set[str] | None = None) -> dict:
	"""Best-effort fix-ups for a proposal that is *almost* schema-valid.

	Small text models routinely drop ``source_handle`` / ``start_node_id``, return
	``warnings`` as objects, emit a flat condition, or name a node type that does
	not exist. These are mechanical to repair and the graph is fully re-validated
	afterwards, so patch rather than fail the turn.
	"""
	if not isinstance(payload, dict):
		return payload
	allowed_types = allowed_types or set()
	# Models often echo every optional schema key as an explicit null; the strict
	# validator then rejects e.g. ``"message": null`` on a typed field. A null is
	# semantically "absent" here, so drop them at the top level.
	for key in [k for k, v in payload.items() if v is None]:
		payload.pop(key, None)
	for key in ("warnings", "assumptions"):
		if key in payload:
			payload[key] = _string_list_items(payload.get(key))
	graph = payload.get("graph")
	if isinstance(graph, dict):
		if not isinstance(graph.get("nodes"), list):
			graph["nodes"] = []
		if not isinstance(graph.get("edges"), list):
			graph["edges"] = []
		_unwrap_standalone_filter(graph)
		# Drop anything that is not a usable node object up front, and de-dupe ids
		# so a malformed entry can never reach schema validation as a hard error.
		seen_ids: set = set()
		kept_nodes: list[dict] = []
		for node in graph.get("nodes") or []:
			if not isinstance(node, dict):
				continue
			nid = str(node.get("id") or "").strip()
			ntype = str(node.get("type") or "").strip()
			if not nid or not ntype or nid in seen_ids:
				continue
			node["id"], seen_ids = nid, seen_ids | {nid}
			kept_nodes.append(node)
		graph["nodes"] = nodes = kept_nodes
		for node in nodes:
			if isinstance(node, dict):
				node.pop("output_paths", None)
				if node.get("placeholder") is None:
					node.pop("placeholder", None)
				if not isinstance(node.get("config"), dict):
					node["config"] = {}
				config = node["config"]
				node_type = str(node.get("type") or "")
				if allowed_types and node_type not in allowed_types:
					mapped = _closest_node_type(node_type, allowed_types)
					if mapped:
						node["type"] = node_type = mapped
						node["placeholder"] = True
				# Never let a model-guessed credential / model id reach binding
				# validation (e.g. ``model: "default"`` -> LLM lookup crash).
				scrub_keys = ["api_key", "token", "secret"]
				if node_type in {"action.ai_generate", "action.ai_support_agent"}:
					scrub_keys += ["model", "provider", "llm", "ai_profile", "knowledge_base"]
				# A reference the user actually named is kept when it resolves to a
				# real record; anything else is a guess and gets blanked (a bogus
				# model id crashes binding validation).
				verifies = {"model": "LLM", "llm": "LLM", "ai_profile": "AI Agent", "knowledge_base": "Knowledge Base"}
				scrubbed = False
				for ck in scrub_keys:
					value = config.get(ck)
					if ck not in config or value in ("", None, [], {}):
						continue
					doctype = verifies.get(ck)
					if doctype and isinstance(value, str) and _reference_exists(doctype, value):
						continue
					config[ck] = ""
					scrubbed = True
				if scrubbed:
					node["placeholder"] = True

				# A literal-only key must not carry a value binding or a template
				# token - the engine stores it verbatim, so the user would see
				# "{{ node_output(...) }}" in their ToDo. Blank it and flag it.
				dynamic_keys = {
					key.split("[")[0].split(".")[0]
					for key in _VALUE_BINDING_CONFIG.get(node_type, [])
				} | set(_TEMPLATED_CONFIG.get(node_type, []))
				for key, value in list(config.items()):
					if key in dynamic_keys or key in _STRUCTURAL_CONFIG_KEYS:
						continue
					is_binding = isinstance(value, dict) and "kind" in value
					is_token = isinstance(value, str) and "{{" in value
					if is_binding or is_token:
						config[key] = ""
						node["placeholder"] = True

				if node_type == "condition.if_else":
					for branch in config.get("branches") or []:
						if isinstance(branch, dict) and branch.get("condition") is not None:
							branch["condition"] = _repair_condition(branch["condition"])
				if node_type in {"trigger.filter_criteria", "trigger.document_insert", "trigger.document_change"}:
					if config.get("condition") is not None:
						config["condition"] = _repair_condition(config["condition"])
				if node_type == "trigger.any":
					for entry in config.get("triggers") or []:
						econf = entry.get("config") if isinstance(entry, dict) else None
						if isinstance(econf, dict) and econf.get("condition") is not None:
							econf["condition"] = _repair_condition(econf["condition"])
		# Exactly one trigger is allowed; convert any extra into the condition it
		# was meant to be. Runs before the edge pass so handles are corrected.
		_demote_extra_triggers(graph, allowed_types)

		node_ids = {n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")}
		node_types_by_id = {
			n.get("id"): str(n.get("type") or "") for n in nodes if isinstance(n, dict)
		}
		raw_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
		seen_ai_handles: dict[str, set] = {}
		clean_edges: list[dict] = []
		for edge in raw_edges:
			if not isinstance(edge, dict):
				continue
			# Accept the aliases small models reach for.
			source = edge.get("source") or edge.get("from") or edge.get("source_id") or edge.get("sourceId")
			target = edge.get("target") or edge.get("to") or edge.get("target_id") or edge.get("targetId")
			handle = (
				edge.get("source_handle")
				or edge.get("sourceHandle")
				or edge.get("handle")
				or edge.get("branch")
				or "default"
			)
			# An edge we cannot anchor to two real nodes is dropped, not fatal.
			# validate_graph then flags any node left unreachable as an issue.
			if source not in node_ids or target not in node_ids or source == target:
				continue
			new_edge = {"source": source, "source_handle": str(handle), "target": target}
			if node_types_by_id.get(source) == "action.ai_generate" and new_edge["source_handle"] not in {
				"success",
				"low_confidence",
				"failure",
			}:
				used = seen_ai_handles.setdefault(source, set())
				for candidate in ("success", "low_confidence", "failure"):
					if candidate not in used:
						new_edge["source_handle"] = candidate
						used.add(candidate)
						break
			clean_edges.append(new_edge)

		# If dropping malformed edges (or the model simply forgetting one) left a
		# step with nothing feeding it, wire it from the node before it in the
		# list. Keeps a plain linear workflow from failing as UNREACHABLE_NODE.
		targets = {e["target"] for e in clean_edges}
		ordered = [n for n in nodes if isinstance(n, dict) and n.get("id")]
		for index in range(1, len(ordered)):
			nid = ordered[index]["id"]
			if nid in targets:
				continue
			prev_id = ordered[index - 1]["id"]
			if prev_id == nid:
				continue
			prev_type = node_types_by_id.get(prev_id, "")
			handle = "default"
			if prev_type == "action.ai_generate":
				used = seen_ai_handles.setdefault(prev_id, set())
				handle = next((h for h in ("success", "low_confidence", "failure") if h not in used), "success")
				used.add(handle)
			elif prev_type in {"condition.if_else", "condition.switch", "condition.random_split"}:
				handle = "none"
			clean_edges.append({"source": prev_id, "source_handle": handle, "target": nid})
			targets.add(nid)
		graph["edges"] = clean_edges
		if not str(graph.get("start_node_id") or "").strip():
			trigger = next(
				(
					n.get("id")
					for n in nodes
					if isinstance(n, dict) and str(n.get("type") or "").startswith("trigger.")
				),
				None,
			)
			if not trigger and nodes and isinstance(nodes[0], dict):
				trigger = nodes[0].get("id")
			if trigger:
				graph["start_node_id"] = trigger
	return payload


def _reply_type(payload: dict) -> str:
	"""Infer the discriminator, tolerating an agent that omits ``reply_type``."""
	declared = str(payload.get("reply_type") or "").strip().lower()
	if declared in {"question", "proposal"}:
		return declared
	if payload.get("graph"):
		return "proposal"
	if payload.get("questions") or payload.get("message"):
		return "question"
	return "proposal"


def _clarify_result(payload: dict) -> dict:
	message = strip_html_tags(str(payload.get("message") or payload.get("summary") or "")).strip()[:1200]
	questions = _clean_messages([str(q) for q in (payload.get("questions") or [])])[:6]
	suggestions = _clean_messages([str(s) for s in (payload.get("suggestions") or [])])[:8]
	if not message and questions:
		message = questions[0]
	if not message:
		message = _("Tell me a little more about what should trigger this workflow and what it should do.")
	return {
		"reply_type": "question",
		"message": message,
		"questions": questions,
		"suggestions": suggestions,
		"mutated": False,
		"published": False,
	}


# ---------------------------------------------------------------------------
# Chat persistence (Workflow Builder AI Chat - one row per workflow + user).
# Turns are stored sanitised: no full graph, only what the agent needs to keep
# the thread coherent on the next turn.
# ---------------------------------------------------------------------------
_CHAT_DOCTYPE = "Workflow Builder AI Chat"


def _chat_name(workflow_name: str, user: str | None = None) -> str:
	return f"{workflow_name}-{user or frappe.session.user}"


def load_chat_history(workflow_name: str, user: str | None = None) -> list[dict]:
	if not workflow_name or not frappe.db.exists("DocType", _CHAT_DOCTYPE):
		return []
	name = _chat_name(workflow_name, user)
	if not frappe.db.exists(_CHAT_DOCTYPE, name):
		return []
	raw = frappe.db.get_value(_CHAT_DOCTYPE, name, "chat_history_json")
	if not raw:
		return []
	try:
		turns = json.loads(raw)
	except (TypeError, ValueError):
		return []
	return turns if isinstance(turns, list) else []


def _sanitise_turn(turn: dict) -> dict:
	clean = {
		"role": "assistant" if str(turn.get("role")) == "assistant" else "user",
		"text": strip_html_tags(str(turn.get("text") or turn.get("content") or "")).strip()[:2000],
		"timestamp": turn.get("timestamp") or now_datetime().isoformat(),
	}
	reply_type = str(turn.get("reply_type") or "").strip().lower()
	if reply_type in {"question", "proposal"}:
		clean["reply_type"] = reply_type
	if turn.get("questions"):
		clean["questions"] = _clean_messages([str(q) for q in turn["questions"]])[:6]
	if turn.get("gated"):
		clean["gated"] = True
	if turn.get("graph_hash"):
		clean["graph_hash"] = str(turn["graph_hash"])[:64]
	if turn.get("node_count") is not None:
		clean["node_count"] = cint(turn.get("node_count"))
	return clean


def save_chat_history(workflow_name: str, turns: list[dict], user: str | None = None) -> None:
	if not workflow_name or not frappe.db.exists("DocType", _CHAT_DOCTYPE):
		return
	user = user or frappe.session.user
	sanitised = [_sanitise_turn(turn) for turn in (turns or []) if turn][-MAX_CHAT_TURNS:]
	json_text = json.dumps(sanitised, ensure_ascii=False, default=str)
	name = _chat_name(workflow_name, user)
	if frappe.db.exists(_CHAT_DOCTYPE, name):
		frappe.db.set_value(_CHAT_DOCTYPE, name, "chat_history_json", json_text, update_modified=True)
		return
	try:
		frappe.get_doc(
			{
				"doctype": _CHAT_DOCTYPE,
				"workflow": workflow_name,
				"user": user,
				"chat_history_json": json_text,
			}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		frappe.db.set_value(_CHAT_DOCTYPE, name, "chat_history_json", json_text, update_modified=True)


def clear_chat_history(workflow_name: str, user: str | None = None) -> None:
	if not workflow_name or not frappe.db.exists("DocType", _CHAT_DOCTYPE):
		return
	name = _chat_name(workflow_name, user)
	if frappe.db.exists(_CHAT_DOCTYPE, name):
		frappe.delete_doc(_CHAT_DOCTYPE, name, ignore_permissions=True, delete_permanently=True)


def _format_chat_history(turns: list[dict] | None) -> str:
	if not turns:
		return "(no prior chat history - this is the first turn)"
	lines: list[str] = []
	for turn in turns[-MAX_CHAT_HISTORY_IN_PROMPT:]:
		if not isinstance(turn, dict):
			continue
		role = "ASSISTANT" if str(turn.get("role")) == "assistant" else "USER"
		text = strip_html_tags(str(turn.get("text") or turn.get("content") or "")).strip()
		if not text:
			continue
		lines.append(f"{role}: {text[:1000]}")
	return "\n".join(lines) if lines else "(no prior chat history)"


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$")


def _coerce_ai_payload(result) -> dict:
	"""Normalise an agent response into a dict, tolerating pydantic / dict / str / fenced JSON."""
	if result is None:
		raise AutomationError(_("The AI agent returned an empty response. Try a clearer request."))
	if hasattr(result, "model_dump"):
		result = result.model_dump()
	elif hasattr(result, "dict") and not isinstance(result, dict):
		try:
			result = result.dict()
		except Exception:
			pass
	if isinstance(result, dict):
		inner = result.get("output")
		if isinstance(inner, dict):
			return inner
		if isinstance(inner, str):
			result = inner
		else:
			return result
	text = _FENCE_RE.sub("", str(result).strip()).strip()
	try:
		parsed = json.loads(text)
	except (TypeError, ValueError):
		start, end = text.find("{"), text.rfind("}")
		if start == -1 or end <= start:
			raise AutomationError(_("AI returned an invalid draft format. Try a clearer request."))
		try:
			parsed = json.loads(text[start : end + 1])
		except (TypeError, ValueError):
			raise AutomationError(_("AI returned malformed JSON. Try a clearer request."))
	if not isinstance(parsed, dict):
		raise AutomationError(_("AI returned an unexpected response. Try a clearer request."))
	return parsed


def _recover_unparsed_completion(exc):
	"""Salvage the model's JSON when the agent's structured-output parser rejected it.

	The AI Agent runs the completion through a ``PydanticOutputParser`` built from
	its ``output_schema``; one field it dislikes discards an otherwise usable
	draft. ``validate_graph`` / ``_normalise_graph`` re-check everything anyway, so
	a parse failure falls back to the raw completion rather than losing the run.
	"""
	text = ""
	try:
		from langchain_core.exceptions import OutputParserException

		if isinstance(exc, OutputParserException):
			text = getattr(exc, "llm_output", None) or ""
	except Exception:
		pass
	if not text:
		# The agent service can re-wrap the parser error in its own exception
		# type, so fall back to scraping the completion out of the message.
		message = str(exc)
		if "from completion" not in message and "Failed to parse" not in message:
			return None
		match = re.search(r"from completion (.*)", message, re.S)
		text = match.group(1) if match else message
	start, end = text.find("{"), text.rfind("}")
	if start == -1 or end <= start:
		return None
	try:
		json.loads(text[start : end + 1])
	except (TypeError, ValueError):
		return None
	return text[start : end + 1]


class _UsageCallback(BaseCallbackHandler):
	"""Collects token usage from the agent's LLM call.

	``agent_service.invoke`` returns only the parsed output, so provider token
	counts are read off the raw ``on_llm_end`` response instead.
	"""

	def __init__(self):
		super().__init__()
		self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

	def on_llm_end(self, response, **kwargs):  # noqa: ARG002
		try:
			usage = _provider_usage(response)
			generations = getattr(response, "generations", None) or []
			if not any(usage.values()) and generations:
				message = getattr(generations[0][0], "message", None)
				if message is not None:
					usage = _provider_usage(message)
			for key in self.usage:
				self.usage[key] += cint(usage.get(key))
		except Exception:
			pass


def _effective_response_schema(agent, allowed_types: list[str]) -> dict:
	"""JSON schema the draft is validated against.

	The schema is authored on the AI Agent (Structured Output). Whatever it says,
	the permission-scoped node-type allow-list is re-imposed here so a mis-edited
	agent can never widen the catalogue. Falls back to the built-in schema when
	the agent has none or it is not graph-shaped.
	"""
	raw = getattr(agent, "output_schema", None)
	schema = None
	if raw:
		try:
			schema = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
		except (TypeError, ValueError):
			schema = None
	if not isinstance(schema, dict):
		return _response_schema(allowed_types)
	try:
		node_props = schema["properties"]["graph"]["properties"]["nodes"]["items"]["properties"]
	except (KeyError, TypeError):
		return _response_schema(allowed_types)
	node_props.setdefault("type", {"type": "string"})
	node_props["type"]["enum"] = allowed_types
	return schema


def _authoring_context(workflow, prompt, catalog, read_fields, write_fields, current, mode, chat_history=None) -> dict:
	"""String-valued variables substituted into the AI Agent's message placeholders.

	Every structured part is JSON so the prompt author can drop it into a message
	with a single ``{placeholder}``. Nothing here is a prompt - the wording lives
	entirely on the AI Agent record.
	"""
	events = registry.business_event_catalog(workflow.primary_doctype, usage="trigger")
	safe_current = _safe_current_graph(current)
	blob = {
		"primary_doctype": workflow.primary_doctype,
		"available_nodes": catalog,
		"readable_fields": read_fields,
		"writable_fields": write_fields,
		"business_events": events,
		"current_graph": safe_current,
	}

	def as_json(value):
		return json.dumps(value, ensure_ascii=False, default=str)

	return {
		"mode": mode,
		"primary_doctype": workflow.primary_doctype or "",
		"user_request": prompt,
		"chat_history": _format_chat_history(chat_history),
		"available_nodes": as_json(catalog),
		"readable_fields": as_json(read_fields),
		"writable_fields": as_json(write_fields),
		"business_events": as_json(events),
		"current_graph": as_json(safe_current) if safe_current else "(no existing graph - build from a blank canvas)",
		"authoring_context": as_json(blob),
		"max_nodes": str(MAX_GENERATED_NODES),
	}


def _fill_missing_placeholders(agent, context_vars: dict) -> dict:
	"""Blank-fill any ``{placeholder}`` the agent's messages declare but the
	context does not provide, so a prompt edit cannot raise a KeyError."""
	filled = dict(context_vars)
	for message in (getattr(agent, "messages", None) or []):
		for key in re.findall(r"\{([a-zA-Z0-9_]+)\}", getattr(message, "content", "") or ""):
			filled.setdefault(key, "")
	return filled


def _invoke_authoring_agent(agent, prompt: str, context_vars: dict, callbacks: list):
	try:
		return agent.agent_service.invoke(query=prompt, callbacks=callbacks, **context_vars)
	except Exception as exc:
		recovered = _recover_unparsed_completion(exc)
		if recovered is None:
			raise
		return recovered


def generate_draft(
	workflow_name: str,
	prompt: str,
	current_graph: Any = None,
	mode: str | None = None,
	chat_history: list[dict] | None = None,
	allow_clarify: bool = False,
	allow_gate: bool = True,
) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("write")
	prompt = str(prompt or "").strip()
	# In a conversation a short reply ("yes, email the owner") is a valid turn;
	# the one-shot endpoint still needs a full description.
	if len(prompt) < (2 if allow_clarify else 12):
		raise AutomationError(_("Describe what should trigger the workflow and what should happen."))
	if len(prompt) > MAX_PROMPT_CHARACTERS:
		raise AutomationError(
			_("Keep the automation request under {0} characters.").format(MAX_PROMPT_CHARACTERS)
		)
	_rate_limit(workflow.name)

	current = (
		validate_graph(current_graph, primary_doctype=workflow.primary_doctype)["graph"]
		if current_graph
		else None
	)
	if mode not in {"generate", "edit"}:
		nodes = (current or {}).get("nodes") or []
		edges = (current or {}).get("edges") or []
		# A blank canvas - or one holding only a starter trigger - is a build,
		# not an edit.
		has_material = bool(edges) or len(nodes) > 1 or any(
			not str(node.get("type") or "").startswith("trigger.") for node in nodes
		)
		mode = "edit" if has_material else "generate"

	agent, model = _authoring_agent(mode)
	# Authoring safety clamps on the in-memory agent doc (never persisted).
	agent.temperature = min(max(float(agent.temperature or 0), 0), 0.4)
	ceiling = min(max(int_setting("ai_authoring_max_output_tokens", 4096), 1024), 8192)
	agent.max_tokens = min(cint(agent.max_tokens or 0) or 4096, ceiling)

	catalog, read_fields, write_fields = _safe_catalog(workflow)
	definitions = {row["type"]: row for row in catalog}
	allowed_types = sorted(definitions)
	schema = _effective_response_schema(agent, allowed_types)

	context_vars = _fill_missing_placeholders(
		agent,
		_authoring_context(workflow, prompt, catalog, read_fields, write_fields, current, mode, chat_history),
	)

	authoring.create_audit(
		workflow.name,
		"AI_DRAFT_REQUESTED",
		{
			"prompt_hash": frappe.utils.sha256_hash(prompt),
			"mode": mode,
			"agent": agent.name,
			"current_graph_hash": validate_graph(current, primary_doctype=workflow.primary_doctype)["graph_hash"]
			if current
			else None,
		},
	)
	started = time.monotonic()
	usage_cb = _UsageCallback()
	try:
		# Invoke, mechanically repair, then schema-check. A first response that is
		# *almost* valid (a missing edge key, a stray field) is common; rather
		# than surface that to the user, feed the exact error back and let the
		# model correct itself once.
		payload = None
		last_error = ""
		for attempt in range(2):
			correction = (
				""
				if attempt == 0
				else (
					"\n\nYOUR PREVIOUS JSON WAS REJECTED: "
					+ last_error
					+ "\nReturn the corrected JSON only. Remember: every edge object needs "
					'"source", "source_handle" and "target", and source/target must be ids '
					"of nodes you defined; every node needs \"id\", \"type\", \"config\"; "
					"conditions use the PREDICATE shape."
				)
			)
			attempt_vars = dict(context_vars)
			attempt_vars["user_request"] = str(context_vars.get("user_request") or prompt) + correction
			raw = _invoke_authoring_agent(agent, prompt + correction, attempt_vars, [usage_cb])
			payload = _repair_ai_payload(_coerce_ai_payload(raw), set(allowed_types))

			# The agent may answer with a follow-up question instead of a draft
			# when the request is missing information it needs. Only honoured in a
			# conversation (``allow_clarify``); the one-shot endpoint always builds.
			if allow_clarify and _reply_type(payload) == "question":
				clarify_errors = sorted(
					Draft202012Validator(_QUESTION_SCHEMA).iter_errors(payload),
					key=lambda error: list(error.path),
				)
				if clarify_errors:
					raise AutomationError(
						_("The assistant asked for clarification but the response was malformed. Try rephrasing.")
					)
				result = _clarify_result(payload)
				authoring.create_audit(
					workflow.name,
					"AI_DRAFT_CLARIFY",
					{
						"prompt_hash": frappe.utils.sha256_hash(prompt),
						"mode": mode,
						"agent": agent.name,
						"question_count": len(result["questions"]),
						"latency_ms": int((time.monotonic() - started) * 1000),
					},
				)
				result.update({"mode": mode, "model": model.name, "agent": agent.name, "usage": usage_cb.usage})
				return result

			if not isinstance(payload.get("graph"), dict):
				if allow_clarify:
					# A graph-less non-question answer becomes a request for detail.
					result = _clarify_result(payload)
					result.update({"mode": mode, "model": model.name, "agent": agent.name, "usage": usage_cb.usage})
					return result
				last_error = "the response contained no \"graph\" object"
				continue

			errors = sorted(
				Draft202012Validator(schema).iter_errors(payload),
				key=lambda error: list(error.path),
			)
			if not errors:
				break
			path = ".".join(str(part) for part in errors[0].path) or "response"
			last_error = f"{path}: {errors[0].message}"
		else:
			raise AutomationError(
				_("The AI draft did not match the required structure ({0}). Try rephrasing the request.").format(
					last_error or "unknown"
				)
			)

		graph = _normalise_graph(payload["graph"], workflow, definitions, current)
		validation = validate_graph(graph, primary_doctype=workflow.primary_doctype)
		try:
			validation["issues"].extend(
				authoring.validate_bindings(graph, workflow.execution_user, workflow.name)
			)
		except AutomationError:
			raise
		except Exception:
			# Binding checks touch referenced records (LLM, template, ...); a
			# guessed reference that does not exist must degrade to a "fix this"
			# issue, not blow up the whole draft.
			validation["issues"].append(
				{
					"severity": "error",
					"code": "AI_BINDING_UNVERIFIED",
					"message": _("Some node references could not be verified; review each highlighted step."),
					"path": None,
					"node_id": None,
				}
			)
		fatal = [issue for issue in validation["issues"] if issue.get("code") in FATAL_GRAPH_ISSUES]
		if fatal:
			raise AutomationError(
				_("AI proposed an unsafe workflow structure: {0}").format(fatal[0]["message"])
			)
		for issue in validation["issues"]:
			node_id = issue.get("node_id")
			if node_id:
				for node in graph["nodes"]:
					if node["id"] == node_id:
						node["placeholder"] = True
						break

		# Completeness gate: never hand back a draft whose mandatory business
		# values are blank - ask for them by name instead. Bounded by
		# MAX_COMPLETENESS_GATES so an unanswerable field cannot loop forever.
		if allow_clarify and allow_gate:
			blocking = [
				issue
				for issue in validation["issues"]
				if issue.get("code") in _USER_ANSWERABLE_MISSING
			]
			if blocking:
				labels = {
					node["id"]: (definitions.get(node["type"], {}).get("label") or node["type"])
					for node in graph["nodes"]
				}
				asked: list[str] = []
				for issue in blocking:
					label = labels.get(issue.get("node_id") or "")
					text = strip_html_tags(str(issue.get("message") or "")).strip()
					entry = f"{text} (step: {label})" if label else text
					if entry in asked:
						continue
					asked.append(entry)
				retry = any(
					isinstance(turn, dict) and turn.get("gated") for turn in (chat_history or [])
				)
				lead = (
					_(
						"I could not match your last answer to anything on this site, so these "
						"are still missing. Pick one of the options, or type an exact name:"
					)
					if retry
					else _(
						"Before I build this I need {0} more detail(s), otherwise the workflow "
						"cannot run. Please tell me:"
					).format(len(asked[:6]))
				)
				result = {
					"reply_type": "question",
					"message": lead,
					"questions": asked[:6],
					"suggestions": [],
					"mutated": False,
					"published": False,
					"gated": True,
				}
				authoring.create_audit(
					workflow.name,
					"AI_DRAFT_INCOMPLETE",
					{
						"prompt_hash": frappe.utils.sha256_hash(prompt),
						"mode": mode,
						"agent": agent.name,
						"missing": [issue.get("code") for issue in blocking][:20],
						"latency_ms": int((time.monotonic() - started) * 1000),
					},
				)
				result.update({"mode": mode, "model": model.name, "agent": agent.name, "usage": usage_cb.usage})
				return result

		elapsed_ms = int((time.monotonic() - started) * 1000)
		usage = usage_cb.usage
		authoring.create_audit(
			workflow.name,
			"AI_DRAFT_PROPOSED",
			{
				"prompt_hash": frappe.utils.sha256_hash(prompt),
				"mode": mode,
				"graph_hash": validation["graph_hash"],
				"node_count": len(graph["nodes"]),
				"issue_count": len(validation["issues"]),
				"model": model.name,
				"provider": model.provider,
				"agent": agent.name,
				"latency_ms": elapsed_ms,
				"input_tokens": usage["input_tokens"],
				"output_tokens": usage["output_tokens"],
				"total_tokens": usage["total_tokens"],
			},
		)
		summary = strip_html_tags(str(payload.get("summary") or "")).strip()[:1200]
		if not summary:
			labels = list(
				dict.fromkeys(
					definitions.get(node["type"], {}).get("label") or node["type"]
					for node in graph["nodes"]
				)
			)
			summary = _("Draft workflow with {0} step(s): {1}.").format(len(graph["nodes"]), ", ".join(labels))
		# Every record reference the draft could not resolve, named against the
		# node that needs it, so the user can jump straight to that node and pick
		# it with the inspector's proper searchable field.
		node_labels = {
			node["id"]: (definitions.get(node["type"], {}).get("label") or node["type"])
			for node in graph["nodes"]
		}
		# Some rules only bite at publish time (an AI step must have its success,
		# low-confidence and failure paths connected). Surface them now rather
		# than letting the user discover them in the publish check.
		publish_only = []
		try:
			publish_issues = validate_graph(
				graph, primary_doctype=workflow.primary_doctype, publish=True
			)["issues"]
			seen_codes = {(i.get("code"), i.get("node_id")) for i in validation["issues"]}
			publish_only = [
				issue
				for issue in publish_issues
				if (issue.get("code"), issue.get("node_id")) not in seen_codes
				and issue.get("code") != "PLACEHOLDER_NODE"
			]
		except Exception:
			publish_only = []

		setup_required = []
		seen_setup: set = set()
		for issue in list(validation["issues"]) + publish_only:
			if issue.get("code") not in _PICK_IN_INSPECTOR_MISSING and issue not in publish_only:
				continue
			node_id = str(issue.get("node_id") or "")
			key = (node_id, issue.get("code"))
			if key in seen_setup:
				continue
			seen_setup.add(key)
			setup_required.append(
				{
					"node_id": node_id or None,
					"node_label": node_labels.get(node_id),
					"message": strip_html_tags(str(issue.get("message") or "")).strip()[:300],
				}
			)

		return {
			"reply_type": "proposal",
			"summary": summary,
			"assumptions": _clean_messages(payload.get("assumptions") or []),
			"warnings": _clean_messages(payload.get("warnings") or []),
			"setup_required": setup_required[:20],
			"graph": graph,
			"issues": validation["issues"],
			"graph_hash": validation["graph_hash"],
			"node_count": len(graph["nodes"]),
			"latency_ms": elapsed_ms,
			"mode": mode,
			"model": model.name,
			"agent": agent.name,
			"usage": usage,
			"mutated": False,
			"published": False,
		}
	except Exception as exc:
		authoring.create_audit(
			workflow.name,
			"AI_DRAFT_FAILED",
			{
				"prompt_hash": frappe.utils.sha256_hash(prompt),
				"mode": mode,
				"error_type": type(exc).__name__,
				"latency_ms": int((time.monotonic() - started) * 1000),
			},
		)
		if isinstance(exc, AutomationError):
			raise
		try:
			frappe.log_error(frappe.get_traceback(), "AI workflow draft - unexpected failure")
		except Exception:
			pass
		raise AutomationError(
			_("The configured AI provider could not generate a workflow draft. Verify its credentials and try again.")
		) from None


def get_chat(workflow_name: str) -> dict:
	"""Server-persisted conversation for the AI Workflow Copilot, for hydration on open."""
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("read")
	return {"turns": load_chat_history(workflow.name)}


def clear_chat(workflow_name: str) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("write")
	clear_chat_history(workflow.name)
	return {"cleared": True}


_APPROVAL_RE = re.compile(
	r"^\s*(y(es|ep|eah|up)?|ok(ay)?|go(\s*ahead)?|proceed|sure|do\s*it|build(\s*it)?|"
	r"make\s*it|confirm(ed)?|approved?|lgtm|sounds?\s*good|looks?\s*good|perfect|"
	r"go\s*for\s*it|\U0001F44D)\b",
	re.I,
)
_BARE_APPROVAL = {
	"yes", "y", "ok", "okay", "go", "go ahead", "proceed", "sure", "do it",
	"build it", "build", "confirm", "confirmed", "approve", "approved", "yep",
	"yeah", "lgtm", "sounds good", "looks good", "perfect", "go for it",
}


def _looks_like_approval(msg: str) -> bool:
	return bool(_APPROVAL_RE.match(msg or ""))


def _first_user_request(history: list[dict] | None) -> str:
	for turn in history or []:
		if isinstance(turn, dict) and turn.get("role") == "user" and str(turn.get("text") or "").strip():
			return str(turn["text"]).strip()
	return ""


def _last_assistant_plan(history: list[dict] | None) -> str:
	for turn in reversed(history or []):
		if isinstance(turn, dict) and turn.get("role") == "assistant" and turn.get("reply_type") == "question":
			return str(turn.get("text") or "").strip()
	return ""


def _effective_request(history: list[dict] | None, message: str) -> str:
	"""When the user approves a plan the assistant proposed, the raw message is
	often just "yes" - which is a poor thing to hand the builder. Rebuild it from
	the original request + the approved plan (+ any tweak) so the graph is built
	from what was actually confirmed, not the word "yes"."""
	plan = _last_assistant_plan(history)
	if not plan or not _looks_like_approval(message):
		return message
	parts = []
	original = _first_user_request(history)
	if original:
		parts.append(f"Original request: {original}")
	parts.append(
		"The user has APPROVED the following plan. Build EXACTLY this - do not add "
		"any step the plan does not list:\n" + plan
	)
	if message.strip().lower().rstrip(".!") not in _BARE_APPROVAL:
		parts.append(f"The user also asked for this adjustment: {message}")
	combined = "\n\n".join(parts)
	return combined[: MAX_PROMPT_CHARACTERS - 200]


def converse(workflow_name: str, message: str, current_graph: Any = None, mode: str | None = None) -> dict:
	"""One turn of the AI Workflow Copilot conversation.

	Returns either ``reply_type="question"`` (the assistant needs more detail) or
	``reply_type="proposal"`` (a validated, non-mutating draft). The thread is
	persisted per workflow + user in ``Workflow Builder AI Chat`` and fed back to
	the agent as ``{chat_history}`` on the next turn.
	"""
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("write")
	message = str(message or "").strip()
	if len(message) < 2:
		raise AutomationError(_("Type a message for the assistant."))
	if len(message) > MAX_PROMPT_CHARACTERS:
		raise AutomationError(
			_("Keep each message under {0} characters.").format(MAX_PROMPT_CHARACTERS)
		)

	history = load_chat_history(workflow.name)
	user_turn = {"role": "user", "text": message, "timestamp": now_datetime().isoformat()}
	# What the builder actually works from - "yes" becomes the approved plan.
	effective_message = _effective_request(history, message)
	# Stop asking for the same missing values forever: after a couple of rounds
	# hand the draft over with placeholders rather than blocking the user.
	gates_used = sum(1 for turn in history if isinstance(turn, dict) and turn.get("gated"))

	try:
		result = generate_draft(
			workflow.name,
			effective_message,
			current_graph,
			mode=mode,
			chat_history=history,
			allow_clarify=True,
			allow_gate=gates_used < MAX_COMPLETENESS_GATES,
		)
	except AutomationError as exc:
		# Keep the user's message in the thread even when the turn failed, so the
		# next turn still has context.
		save_chat_history(
			workflow.name,
			history
			+ [user_turn, {"role": "assistant", "text": str(exc), "timestamp": now_datetime().isoformat()}],
		)
		raise

	if result.get("reply_type") == "question":
		assistant_turn = {
			"role": "assistant",
			"text": result.get("message") or "",
			"reply_type": "question",
			"questions": result.get("questions") or [],
			"timestamp": now_datetime().isoformat(),
		}
		if result.get("gated"):
			assistant_turn["gated"] = True
	else:
		assistant_turn = {
			"role": "assistant",
			"text": result.get("summary") or "",
			"reply_type": "proposal",
			"graph_hash": result.get("graph_hash"),
			"node_count": result.get("node_count"),
			"timestamp": now_datetime().isoformat(),
		}

	save_chat_history(workflow.name, history + [user_turn, assistant_turn])
	return result


def accept_proposal(workflow_name: str, graph_hash: str | None = None, node_count: int | None = None) -> dict:
	"""Record that the user applied an AI draft to the canvas (audit only - the
	graph itself is written through the ordinary draft-save path)."""
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("write")
	authoring.create_audit(
		workflow.name,
		"AI_DRAFT_ACCEPTED",
		{
			"graph_hash": str(graph_hash or "")[:64] or None,
			"node_count": cint(node_count) if node_count is not None else None,
			"actor": frappe.session.user,
		},
	)
	return {"recorded": True}
