from __future__ import annotations

import json

import frappe

from finbyzai.workflow_builder.external import repair_email_queue_communication


def execute() -> None:
	"""Restore timeline Communications for emails queued by legacy workflow actions."""
	if not (
		frappe.db.table_exists("Automation Action Attempt")
		and frappe.db.table_exists("Email Queue")
		and frappe.db.table_exists("Communication")
	):
		return

	queue_names = set()
	for attempt in frappe.get_all(
		"Automation Action Attempt",
		filters=[["output_json", "like", "%email_queue%"]],
		fields=["output_json"],
		limit_page_length=0,
	):
		try:
			output = json.loads(attempt.output_json or "{}")
		except (TypeError, ValueError):
			continue
		queue_name = str(output.get("email_queue") or "").strip()
		if queue_name:
			queue_names.add(queue_name)

	for index, queue_name in enumerate(sorted(queue_names)):
		savepoint = f"workflow_email_link_{index}"
		frappe.db.savepoint(savepoint)
		try:
			repair_email_queue_communication(queue_name)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(
				title="Workflow email Communication repair failed",
				message=frappe.get_traceback(with_context=False),
			)
		else:
			frappe.db.release_savepoint(savepoint)
