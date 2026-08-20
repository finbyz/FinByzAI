from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, now_datetime

from .configuration import automation_enabled


RUN_CHILD_DOCTYPES = (
	"Automation Action Attempt",
	"Automation Timer",
	"Automation Run Token",
	"Automation Run Event",
	"Automation Effect Ledger",
	"Automation Policy Evaluation",
	"Automation Enrollment Decision",
)


def history_retention_days() -> int:
	"""The client baseline is six months; administrators may retain longer."""
	try:
		configured = cint(frappe.db.get_single_value("Automation Settings", "history_retention_days", cache=False) or 180)
	except Exception:
		configured = 180
	return max(configured, 180)


def purge_expired_execution_history(limit: int = 500) -> int:
	"""Purge terminal run detail after the configured retention window.

	Enrollment ledgers and aggregate metrics remain, so re-enrollment semantics and
	long-term counts do not change when detailed execution evidence expires.
	"""
	if not automation_enabled() or not frappe.db.table_exists("Automation Run"):
		return 0
	cutoff = now_datetime() - timedelta(days=history_retention_days())
	run = frappe.qb.DocType("Automation Run")
	run_names = list(
		frappe.qb.from_(run)
		.select(run.name)
		.where((run.status.isin(["COMPLETED", "FAILED", "CANCELLED"])) & (run.completed_at < cutoff))
		.orderby(run.completed_at)
		.limit(min(max(cint(limit), 1), 2000))
		.run(pluck=True)
	)
	if not run_names:
		return 0
	for doctype in RUN_CHILD_DOCTYPES:
		if frappe.db.table_exists(doctype):
			frappe.db.delete(doctype, {"run": ["in", run_names]})
	for doctype in ("Automation Incident", "Automation Dead Letter"):
		if frappe.db.table_exists(doctype):
			frappe.db.set_value(doctype, {"run": ["in", run_names]}, "run", None, update_modified=False)
	frappe.db.set_value(
		"Automation Enrollment Ledger",
		{"run": ["in", run_names]},
		"run",
		None,
		update_modified=False,
	)
	frappe.db.delete("Automation Run", {"name": ["in", run_names]})
	return len(run_names)
