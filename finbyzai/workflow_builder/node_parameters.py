"""Human-written guidance for every workflow node, for the AI authoring agent.

``registry`` knows a node's *shape* (default config, required paths, outputs).
It does not know what a parameter **means**, what values are legal, or when the
node is the right choice - and without that the agent guesses, leaves mandatory
values blank, or writes a template token into a literal-only field.

This module supplies that missing half. ``describe_node`` merges it with the
registry facts so every node reaches the prompt fully documented; a node with no
entry here still gets its type, defaults, required paths and accepted-value kind
derived mechanically, so nothing is ever undocumented.

Enumerated values are taken from the validators in ``schema.py`` /
``ai_support.py`` - never invent one here, it becomes a lie the agent acts on.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# when_to_use: one or two lines telling the agent to pick THIS node over its
#   neighbours.
# params: per parameter - what it is, and (where the engine enumerates them) the
#   only legal values.
# ---------------------------------------------------------------------------
NODE_GUIDE: dict[str, dict[str, Any]] = {
	# ---------------- triggers ----------------
	"trigger.filter_criteria": {
		"when_to_use": (
			"Run whenever a record's own fields match criteria (e.g. 'recording is "
			"present', 'status is Completed'). The default choice when no business "
			"event fits. Standalone node - never nest it inside trigger.any."
		),
		"params": {
			"condition": "The enrolment criteria, as a PREDICATE or GROUP object. Required.",
		},
	},
	"trigger.any": {
		"when_to_use": (
			"Run on one or more discrete events: a named business event, a record "
			"being created, or a record being changed. Holds up to 20 OR'd trigger "
			"groups."
		),
		"params": {
			"triggers": (
				"List of trigger groups, each {id, type, config}. type is ONLY "
				"trigger.event (needs config.event_topic from business_events), "
				"trigger.document_insert, or trigger.document_change."
			),
		},
	},
	"trigger.manual": {"when_to_use": "Records are enrolled by a person, on demand. No configuration."},
	"trigger.schedule": {"when_to_use": "The workflow is started on a schedule rather than by a record change."},
	"trigger.webhook": {"when_to_use": "An external system starts the workflow by calling in."},

	# ---------------- conditions ----------------
	"condition.if_else": {
		"when_to_use": (
			"Split the path on record criteria. Each named branch has its own "
			"condition; anything that matches nothing takes the 'none' edge."
		),
		"params": {
			"branches": (
				"List of {handle, name, condition}. handle is the edge's "
				"source_handle. condition is a PREDICATE or GROUP."
			),
		},
	},
	"condition.random_split": {
		"when_to_use": "A/B split by percentage, for experiments. Percentages must total 100.",
		"params": {"branches": "List of {handle, name, percentage}."},
	},
	"condition.deduplicate": {
		"when_to_use": "Let only the first record with a given field combination continue.",
		"params": {
			"match_fields": "Fieldnames whose combination identifies a duplicate.",
			"match_mode": "How to compare. One of: all, any.",
		},
	},

	# ---------------- delays ----------------
	"delay.fixed": {
		"when_to_use": "Wait a set amount of time before the next step.",
		"params": {"seconds": "Wait duration in seconds (3600 = 1 hour, 86400 = 1 day). Required."},
	},
	"delay.until_date": {
		"when_to_use": "Wait until a specific moment, either a fixed datetime or one held in a record field.",
		"params": {
			"mode": "Where the date comes from. One of: literal, field.",
			"datetime": "The fixed date and time, when mode is literal.",
			"field": "A date/datetime fieldname on the record, when mode is field.",
		},
	},
	"delay.until_event": {
		"when_to_use": "Pause until a business event happens, with a timeout path.",
		"params": {
			"event_topic": "The awaited business event topic. Required.",
			"timeout_mode": "One of: duration, indefinite.",
			"timeout_seconds": "How long to wait when timeout_mode is duration.",
			"branch_on_timeout": "1 to expose separate 'event' and 'timeout' edges, 0 for a single path.",
		},
	},
	"delay.drip": {
		"when_to_use": "Release enrolled records in batches instead of all at once.",
		"params": {
			"batch_size": "How many records per batch. Required.",
			"interval_seconds": "Seconds between batches. Required.",
		},
	},
	"delay.business_hours": {
		"when_to_use": "Hold the record until the next working window.",
		"params": {
			"timezone": "IANA timezone, e.g. Asia/Kolkata. Required.",
			"start_time": "Window opens, HH:MM.",
			"end_time": "Window closes, HH:MM.",
			"weekdays": "Working days as integers, Monday=0.",
		},
	},

	# ---------------- transforms ----------------
	"transform.value": {
		"when_to_use": "Derive a value (join, clean, format, compute) for later steps to use.",
		"params": {
			"operation": (
				"One of: coalesce, concat, upper, lower, parse_number, format_number, "
				"format_phone, format_currency, random_number, math."
			),
			"values": "Input VALUE BINDINGs the operation consumes.",
		},
	},
	"transform.associated_record": {
		"when_to_use": "Read a field from a record this one links to (e.g. the Customer behind a Call Log).",
		"params": {
			"reference_field": "Link fieldname on this record. Required.",
			"fetch_field": "Fieldname to read on the linked record. Required.",
		},
	},
	"transform.child_records": {
		"when_to_use": "Read a field from this record's child table rows.",
		"params": {
			"child_table_field": "Child table fieldname. Required.",
			"fetch_field": "Fieldname to read on each row. Required.",
		},
	},

	# ---------------- AI ----------------
	"action.ai_generate": {
		"when_to_use": (
			"ANY AI text task - summarise, transcribe, classify, extract, draft, "
			"answer. There is no separate transcription node. Branch node: its "
			"outgoing edges use success / low_confidence / failure."
		),
		"params": {
			"prompt_mode": "One of: inline (write the prompt here), profile (use a saved AI profile).",
			"model": (
				"The LLM to run. Required before the workflow can run. Set it only to "
				"a model name the user stated; an unknown name is discarded."
			),
			"system_prompt": "Role instructions. Jinja: {{ doc.FIELDNAME }} is substituted.",
			"user_prompt": (
				"The task. Jinja: {{ doc.FIELDNAME }} is substituted. This is how you "
				"pass record data in - and the fieldname must ALSO be in field_allowlist."
			),
			"field_allowlist": (
				"1-50 fieldnames from readable_fields. THESE ARE THE ONLY RECORD "
				"FIELDS THE AI CAN SEE. Empty list means it sees no record data at all."
			),
			"output_format": "One of: text, json.",
			"mode": "One of: summarize, classify_extract, draft_reply, grounded_answer.",
			"ai_profile": "A saved AI profile, when prompt_mode is profile.",
			"knowledge_base": "Knowledge Base to ground answers against.",
			"include_thread": "1 to include the record's conversation history, else 0.",
			"confidence_threshold": "Below this the low_confidence edge is taken (0-1).",
			"failure_mode": "One of: branch (take the failure edge), fail_workflow.",
		},
	},
	"action.ai_support_agent": {
		"when_to_use": "Grounded support answering on Issue workflows only. Edges: respond / handoff / failure.",
		"params": {
			"ai_profile": "The AI profile to run. Required.",
			"knowledge_base": "Knowledge Base to answer from. Required.",
			"field_allowlist": "Record fields the agent may read. Required.",
		},
	},

	# ---------------- record actions ----------------
	"action.create_todo": {
		"when_to_use": (
			"Create a simple follow-up task against the enrolled record. Both its "
			"values are PLAIN TEXT - it cannot receive an earlier step's output. If "
			"the task text must come from an AI step, use action.create_record on "
			"ToDo with assignments instead."
		),
		"params": {
			"allocated_to": "Email of an enabled User to assign. Required.",
			"description": "Task text. Plain literal - no tokens, no bindings. Required.",
			"priority": "One of: Low, Medium, High.",
		},
	},
	"action.create_record": {
		"when_to_use": (
			"Create a record of any DocType, with field values that may come from "
			"the record or an earlier step. Use this (not create_todo) when a value "
			"must be carried from an AI or transform step."
		),
		"params": {
			"target_doctype": "DocType to create. Required.",
			"assignments": "Rows of {field, value}; value is a VALUE BINDING. Required.",
		},
	},
	"action.update_record": {
		"when_to_use": "Change fields on the enrolled record.",
		"params": {"assignments": "Rows of {field, value, operation}; value is a VALUE BINDING. Required."},
	},
	"action.numeric_adjust": {
		"when_to_use": "Increment or otherwise adjust a numeric field.",
		"params": {
			"field": "Numeric fieldname to change. Required.",
			"operation": "One of: add, subtract, multiply, divide, modulo, power.",
			"amount": "The operand. Required.",
		},
	},
	"action.copy_record": {"when_to_use": "Duplicate the enrolled record."},
	"action.manage_association": {
		"when_to_use": "Link or unlink this record to another record.",
		"params": {
			"target_doctype": "The other record's DocType. Required.",
			"target_name": "The other record's name. Required.",
			"link_field": "Link fieldname joining them. Required.",
			"operation": "One of: link, unlink.",
		},
	},
	"action.merge_contact": {"when_to_use": "Merge duplicate Contacts. Ends the path.", "params": {"match_fields": "Fieldnames identifying the duplicate. Required."}},
	"action.add_comment": {
		"when_to_use": "Leave a note on the record's timeline.",
		"params": {"content": "Comment text. Plain literal - no tokens or bindings. Required."},
	},
	"action.create_note": {
		"when_to_use": "Create a standalone Desk note.",
		"params": {"title": "Note title. Required.", "content": "Note body. Required."},
	},

	# ---------------- people ----------------
	"action.notify_user": {
		"when_to_use": "Send an in-app notification to Desk users.",
		"params": {
			"audience": "Who to notify. One of: specific, assigned, all.",
			"for_user": "The User to notify, when audience is specific.",
			"subject": "Notification title. Plain literal. Required.",
			"message": "Notification body. Plain literal. Required.",
		},
	},
	"action.round_robin": {
		"when_to_use": "Assign records evenly across a pool of users.",
		"params": {
			"assignment_type": "One of: group, users.",
			"group": "User Group, when assignment_type is group.",
			"users": "List of users, when assignment_type is users.",
		},
	},
	"action.unassign_record": {"when_to_use": "Close the record's open assignments."},
	"action.human_approval": {
		"when_to_use": "Pause for a person to approve, edit or reject. Edges: approved / rejected.",
		"params": {
			"reviewer": "User who decides. Required.",
			"draft_text": "What they review, as a VALUE BINDING - use node_output to show an AI result. Required.",
			"title": "Heading shown to the reviewer.",
			"instructions": "Guidance shown to the reviewer.",
		},
	},

	# ---------------- messaging ----------------
	"action.send_email": {
		"when_to_use": "Email the record's contact. Prefer a saved Email Template.",
		"params": {
			"content_mode": "One of: template, inline.",
			"email_template": "Email Template to send, when content_mode is template.",
			"recipient": "Who receives it, as a VALUE BINDING. Required.",
			"subject_override": "Replaces the template subject, as a VALUE BINDING.",
			"sender_name": "From name.",
			"sender_email": "An authorised sending address.",
		},
	},
	"action.send_sms": {
		"when_to_use": "Send an SMS. Consent-gated.",
		"params": {
			"recipient": "Phone number, as a VALUE BINDING. Required.",
			"message": "Message text, as a VALUE BINDING. Required.",
			"purpose": "Consent purpose this send is recorded under. Required.",
			"require_consent": "1 to enforce consent, 0 to skip.",
		},
	},
	"action.instagram_message": {
		"when_to_use": "Send an Instagram DM through the Meta integration.",
		"params": {
			"integration_secret": "Stored credential. Required.",
			"url": "Meta endpoint. Required.",
			"recipient_id": "Instagram recipient, as a VALUE BINDING. Required.",
			"message": "Message, as a VALUE BINDING. Required.",
		},
	},
	"action.webhook": {
		"when_to_use": "POST to an external HTTPS endpoint.",
		"params": {
			"integration_secret": "Stored credential holding the auth. Required.",
			"url": "HTTPS endpoint. Required.",
			"payload": "JSON body; each value may be a VALUE BINDING. Required.",
		},
	},
	"action.asana": {
		"when_to_use": "Create or update Asana work through the installed integration.",
		"params": {
			"operation": "One of: create_task, update_task, create_subtask, create_project. Required.",
			"target_gid": "The Asana object id, as a VALUE BINDING.",
			"payload": "Asana fields; each value is a VALUE BINDING. Required.",
		},
	},

	# ---------------- flow control ----------------
	"action.call_subflow": {
		"when_to_use": "Run another workflow as a step.",
		"params": {
			"subflow_id": "The workflow to run. Required.",
			"wait_for_completion": "1 to wait for it to finish, 0 to fire and continue.",
		},
	},
	"action.go_to": {
		"when_to_use": "Jump to an earlier step instead of continuing.",
		"params": {"target_node_id": "Id of the destination node. Required."},
	},
	"action.complete_goal": {
		"when_to_use": "Record that the goal was met and end this path.",
		"params": {"goal": "Goal name. Required."},
	},
	"action.remove_from_workflow": {
		"when_to_use": "Unenrol the record from this or another workflow.",
		"params": {"target_workflow": "'current', or another workflow's id. Required."},
	},
	"action.mark_communications_read": {"when_to_use": "Clear unread flags on the record's conversations."},
	"action.verify_email": {
		"when_to_use": "Check an address is well-formed before sending.",
		"params": {"email": "Address to check, as a VALUE BINDING. Required."},
	},
}


def _param_type(default: Any) -> str:
	if isinstance(default, bool):
		return "boolean"
	if isinstance(default, int):
		return "integer"
	if isinstance(default, float):
		return "number"
	if isinstance(default, list):
		return "list"
	if isinstance(default, dict):
		return "value_binding" if "kind" in default else "object"
	return "text"


def describe_node(
	definition: dict,
	*,
	required_paths: list[str],
	binding_keys: list[str],
	templated_keys: list[str],
) -> dict:
	"""Merge the hand-written guide with the registry facts for one node."""
	node_type = definition["type"]
	guide = NODE_GUIDE.get(node_type) or {}
	notes = guide.get("params") or {}
	defaults = definition.get("default_config") or {}

	binding_roots = {key.split("[")[0].split(".")[0] for key in binding_keys}
	params: dict[str, dict] = {}
	for key, default in defaults.items():
		spec: dict[str, Any] = {"type": _param_type(default), "default": default}
		if key in required_paths:
			spec["required"] = True
		if key in templated_keys:
			spec["accepts"] = "jinja_template"
		elif key in binding_roots:
			spec["accepts"] = "value_binding"
		else:
			spec["accepts"] = "literal"
		if notes.get(key):
			spec["description"] = notes[key]
		params[key] = spec

	# A required path that is not a top-level default key (e.g. "assignments"
	# on a node whose default_config is empty) still has to be advertised.
	for path in required_paths:
		root = path.split("[")[0].split(".")[0]
		if root not in params:
			params[root] = {
				"type": "object",
				"required": True,
				"accepts": "value_binding" if root in binding_roots else "literal",
				**({"description": notes[root]} if notes.get(root) else {}),
			}

	described = {"params": params}
	if guide.get("when_to_use"):
		described["when_to_use"] = guide["when_to_use"]
	return described
