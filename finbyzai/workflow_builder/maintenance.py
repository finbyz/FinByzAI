from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, get_datetime, now_datetime


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


def log_cleanup_interval_hours() -> int:
	try:
		configured = cint(
			frappe.db.get_single_value("Automation Settings", "log_cleanup_interval_hours", cache=False) or 24
		)
	except Exception:
		configured = 24
	return min(max(configured, 1), 168)


def log_cleanup_batch_size() -> int:
	try:
		configured = cint(
			frappe.db.get_single_value("Automation Settings", "log_cleanup_batch_size", cache=False) or 500
		)
	except Exception:
		configured = 500
	return min(max(configured, 100), 5000)


def _expired_names(
	doctype: str,
	date_field: str,
	cutoff,
	limit: int,
	*,
	statuses: tuple[str, ...] | None = None,
) -> list[str]:
	if not frappe.db.table_exists(doctype):
		return []
	table = frappe.qb.DocType(doctype)
	condition = table[date_field] < cutoff
	if statuses:
		condition &= table.status.isin(statuses)
	return list(
		frappe.qb.from_(table)
		.select(table.name)
		.where(condition)
		.orderby(table[date_field])
		.limit(limit)
		.run(pluck=True)
	)


def purge_expired_execution_history(limit: int | None = None) -> int:
	"""Purge terminal run detail after the configured retention window.

	Enrollment ledgers and aggregate metrics remain, so re-enrollment semantics and
	long-term counts do not change when detailed execution evidence expires.
	"""
	# Retention is operational housekeeping and must continue while execution is
	# disabled; otherwise an emergency kill switch can also exhaust the database.
	if not frappe.db.table_exists("Automation Run"):
		return 0
	cutoff = now_datetime() - timedelta(days=history_retention_days())
	batch_size = log_cleanup_batch_size() if limit is None else min(max(cint(limit), 1), 5000)
	run_names = _expired_names(
		"Automation Run",
		"completed_at",
		cutoff,
		batch_size,
		statuses=("COMPLETED", "FAILED", "CANCELLED"),
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


def purge_expired_automation_logs(limit: int | None = None) -> dict[str, int]:
	"""Remove expired terminal workflow evidence without touching active work.

	Enrollment ledgers and daily metrics remain because they carry deduplication
	and long-term aggregate semantics. Open incidents, open dead letters, pending
	outbox events, and active backfills are intentionally never removed.
	"""
	batch_size = log_cleanup_batch_size() if limit is None else min(max(cint(limit), 1), 5000)
	cutoff = now_datetime() - timedelta(days=history_retention_days())
	counts = {"runs": purge_expired_execution_history(batch_size)}

	cleanup_specs = (
		("Automation Outbox Event", "processed_at", ("PROCESSED",), "outbox_events"),
		("Automation Dead Letter", "resolved_at", ("RESOLVED", "DISCARDED"), "dead_letters"),
		("Automation Audit Event", "occurred_at", None, "audit_events"),
	)
	for doctype, date_field, statuses, result_key in cleanup_specs:
		names = _expired_names(doctype, date_field, cutoff, batch_size, statuses=statuses)
		if names:
			frappe.db.delete(doctype, {"name": ["in", names]})
		counts[result_key] = len(names)

	incident_names = _expired_names(
		"Automation Incident", "resolved_at", cutoff, batch_size, statuses=("RESOLVED",)
	)
	if incident_names and frappe.db.table_exists("Automation Dead Letter"):
		linked = set(
			frappe.get_all(
				"Automation Dead Letter",
				filters={"incident": ["in", incident_names]},
				pluck="incident",
				limit_page_length=0,
			)
		)
		incident_names = [name for name in incident_names if name not in linked]
	if incident_names:
		frappe.db.delete("Automation Incident", {"name": ["in", incident_names]})
	counts["incidents"] = len(incident_names)

	backfill_names = _expired_names(
		"Automation Backfill Job",
		"completed_at",
		cutoff,
		batch_size,
		statuses=("COMPLETED", "FAILED", "CANCELLED"),
	)
	if backfill_names:
		if frappe.db.table_exists("Automation Schedule"):
			frappe.db.set_value(
				"Automation Schedule",
				{"last_backfill_job": ["in", backfill_names]},
				"last_backfill_job",
				None,
				update_modified=False,
			)
		frappe.db.delete("Automation Backfill Job", {"name": ["in", backfill_names]})
	counts["backfill_jobs"] = len(backfill_names)
	return counts


def run_scheduled_log_cleanup(force: bool = False) -> dict:
	"""Run retention cleanup when its administrator-configured interval is due."""
	now = now_datetime()
	last_cleanup = None
	try:
		last_cleanup = frappe.db.get_single_value("Automation Settings", "last_log_cleanup_at", cache=False)
	except Exception:
		# The field may not exist during a rolling deploy before migrate completes.
		pass
	if not force and last_cleanup:
		due_at = get_datetime(last_cleanup) + timedelta(hours=log_cleanup_interval_hours())
		if now < due_at:
			return {"ran": False, "next_run_at": due_at, "counts": {}}
	counts = purge_expired_automation_logs()
	try:
		frappe.db.set_single_value(
			"Automation Settings", "last_log_cleanup_at", now, update_modified=False
		)
	except Exception:
		# Cleanup is still useful during the same rolling-deploy window.
		pass
	return {"ran": True, "completed_at": now, "counts": counts}
