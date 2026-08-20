from __future__ import annotations

import frappe

from finbyzai.workflow_builder.setup import ensure_automation_indexes


def execute() -> None:
	"""Index existing event waits by their enrolled record and earlier-action source."""
	if not frappe.db.table_exists("Automation Timer"):
		return
	for timer in frappe.get_all(
		"Automation Timer",
		filters={"status": "ACTIVE", "timer_type": ["in", ["TIMEOUT", "EVENT_WAIT"]]},
		fields=["name", "run", "token"],
		limit_page_length=0,
	):
		run = frappe.db.get_value(
			"Automation Run",
			timer.run,
			["record_doctype", "record_name"],
			as_dict=True,
		)
		if not run:
			continue
		state = frappe.parse_json(
			frappe.db.get_value("Automation Run Token", timer.token, "output_json") or "{}"
		)
		source_name = str(state.get("event_source_id") or "").strip()
		values = {
			"record_doctype": run.record_doctype,
			"record_name": run.record_name,
			"source_type": "ACTION_EMAIL" if source_name else "ENROLLED_RECORD",
			"source_doctype": str(state.get("event_source_doctype") or ("Email Queue" if source_name else run.record_doctype)),
			"source_name": source_name or run.record_name,
		}
		frappe.db.set_value("Automation Timer", timer.name, values, update_modified=False)
	ensure_automation_indexes()
