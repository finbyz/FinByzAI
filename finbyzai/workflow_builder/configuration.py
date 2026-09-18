from __future__ import annotations

import frappe
from frappe.utils import cint


def setting(fieldname: str, default=None):
	if not frappe.db.exists("DocType", "Automation Settings"):
		return default
	value = frappe.db.get_single_value("Automation Settings", fieldname, cache=True)
	return default if value is None else value


def int_setting(fieldname: str, default: int) -> int:
	return cint(setting(fieldname, default)) or default


def automation_enabled() -> bool:
	return bool(cint(setting("enabled", 0)))


def workflow_runtime_allowed(_workflow_name: str | None = None) -> bool:
	"""Return whether workflow execution is globally enabled.

	The workflow argument remains for compatibility with runtime call sites. Runtime
	access is no longer restricted by a per-workflow rollout allowlist.
	"""
	return automation_enabled()


def external_actions_enabled() -> bool:
	return automation_enabled() and bool(cint(setting("external_actions_enabled", 0)))


def ai_actions_enabled() -> bool:
	"""Independent fail-closed switch for billable AI provider calls."""
	return automation_enabled() and bool(cint(setting("ai_actions_enabled", 0)))


def ai_authoring_enabled() -> bool:
	"""Independent switch for billable prompt-to-draft authoring calls.

	Authoring does not require the workflow runtime switch because a generated
	draft cannot execute until a publisher explicitly publishes and activates it.
	"""
	return bool(cint(setting("ai_authoring_enabled", 0)))


def ai_authoring_generator_agent() -> str:
	"""AI Agent that builds a full workflow draft from scratch.

	Resolution order: dedicated generator agent, then the deprecated single
	authoring agent. The prompt and structured-output schema live on the AI
	Agent record, never in code.
	"""
	return str(
		setting("ai_authoring_generator_agent", "")
		or setting("ai_authoring_agent", "")
		or ""
	).strip()


def ai_authoring_editor_agent() -> str:
	"""AI Agent for incremental edits to an existing draft.

	Falls back to the generator agent (then the deprecated single agent) when
	no dedicated editor agent is configured.
	"""
	return str(
		setting("ai_authoring_editor_agent", "")
		or ai_authoring_generator_agent()
		or ""
	).strip()
