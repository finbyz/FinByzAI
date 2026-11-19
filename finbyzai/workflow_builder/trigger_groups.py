"""AND / OR linking of several enrollment triggers.

Each trigger of a ``trigger.any`` node becomes one subscription. With match mode
``any`` (the default) one trigger is enough. With ``all`` every trigger must have
fired for the same record, optionally within a time window, before the record
enrolls. Partial matches are kept in ``Automation Trigger Match``.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, now_datetime

MATCH_ANY = "any"
MATCH_ALL = "all"
MAX_WINDOW_HOURS = 24 * 365


def match_settings(trigger_config: dict | None) -> dict:
	"""Normalised match settings stored on the trigger node."""
	config = trigger_config if isinstance(trigger_config, dict) else {}
	mode = MATCH_ALL if str(config.get("match_mode") or MATCH_ANY).lower() == MATCH_ALL else MATCH_ANY
	window = cint(config.get("match_window_hours") or 0)
	return {"mode": mode, "window_hours": min(max(window, 0), MAX_WINDOW_HOURS)}


def all_triggers_satisfied(subscription_config: dict, workflow: str, record_doctype: str, record_name: str) -> bool:
	"""Record that this trigger fired; True when the record may enroll now."""
	if subscription_config.get("_match_mode") != MATCH_ALL:
		return True
	group_id = str(subscription_config.get("_trigger_group_id") or "")
	group_ids = [str(value) for value in subscription_config.get("_trigger_group_ids") or [] if value]
	if not group_id or len(group_ids) < 2 or group_id not in group_ids:
		return True
	window_hours = cint(subscription_config.get("_match_window_hours") or 0)
	now = now_datetime()
	filters = {"workflow": workflow, "record_doctype": record_doctype, "record_name": record_name}
	if window_hours:
		frappe.db.delete("Automation Trigger Match", {**filters, "matched_at": ["<", now - timedelta(hours=window_hours)]})
	existing = frappe.db.get_value("Automation Trigger Match", {**filters, "trigger_group_id": group_id}, "name", for_update=True)
	if existing:
		frappe.db.set_value("Automation Trigger Match", existing, "matched_at", now, update_modified=False)
	else:
		frappe.get_doc(
			{"doctype": "Automation Trigger Match", **filters, "trigger_group_id": group_id, "matched_at": now}
		).insert(ignore_permissions=True)
	present = set(
		frappe.get_all("Automation Trigger Match", filters=filters, pluck="trigger_group_id", limit_page_length=0)
	)
	if not set(group_ids) <= present:
		return False
	# Consume the matches so the record has to satisfy every trigger again.
	frappe.db.delete("Automation Trigger Match", {**filters, "trigger_group_id": ["in", group_ids]})
	return True


def clear_matches(workflow: str) -> None:
	frappe.db.delete("Automation Trigger Match", {"workflow": workflow})
