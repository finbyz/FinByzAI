from __future__ import annotations

import frappe
from frappe.utils import cint


def setting(fieldname: str, default=None):
	if not frappe.db.exists("DocType", "Automation Settings"):
		return default
	value = frappe.db.get_single_value("Automation Settings", fieldname, cache=False)
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
