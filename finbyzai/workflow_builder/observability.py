from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint, getdate, now_datetime, sha256_hash

from .errors import AutomationError
from .notifications import enqueue_notification_for_user
from .schema import canonical_json

METRIC_FIELDS = {
	"enrollments",
	"suppressed",
	"duplicates",
	"completed_runs",
	"failed_runs",
	"cancelled_runs",
	"node_failures",
	"retries",
	"total_duration_seconds",
}


def record_enrollment_decision(
	*,
	workflow: str,
	workflow_version: str | None,
	record_doctype: str,
	record_name: str,
	source: str,
	occurrence_key: str | None,
	decision: str,
	reason_code: str,
	evidence: dict | None = None,
	trace_id: str | None = None,
	run: str | None = None,
) -> str | None:
	"""Write safe, append-only enrollment evidence in the caller's transaction."""
	if not frappe.db.table_exists("Automation Enrollment Decision"):
		return None
	row = frappe.get_doc(
		{
			"doctype": "Automation Enrollment Decision",
			"workflow": workflow,
			"workflow_version": workflow_version,
			"record_doctype": record_doctype,
			"record_name": record_name,
			"record_key": f"{record_doctype}:{record_name}",
			"source": str(source or "UNKNOWN")[:140],
			"occurrence_key": str(occurrence_key or "")[:140],
			"decision": decision,
			"reason_code": str(reason_code or "UNKNOWN")[:140],
			"evidence_json": json.dumps(evidence or {}, default=str),
			"trace_id": trace_id,
			"run": run,
			"decided_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	metric = {"ENROLLED": "enrollments", "SUPPRESSED": "suppressed", "DUPLICATE": "duplicates"}.get(decision)
	if metric and workflow_version:
		increment_metric(workflow, workflow_version, metric)
	return row.name


def increment_metric(workflow: str, workflow_version: str, fieldname: str, amount: float = 1) -> None:
	if fieldname not in METRIC_FIELDS:
		raise ValueError(f"Unsupported automation metric: {fieldname}")
	if not frappe.db.table_exists("Automation Metric Daily"):
		return
	filters = {"metric_date": getdate(), "workflow": workflow, "workflow_version": workflow_version}
	name = frappe.db.get_value("Automation Metric Daily", filters, "name", for_update=True)
	if not name:
		save_point = f"automation_metric_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(save_point)
		try:
			name = frappe.get_doc({"doctype": "Automation Metric Daily", **filters}).insert(ignore_permissions=True).name
		except frappe.DuplicateEntryError:
			frappe.db.rollback(save_point=save_point)
			name = frappe.db.get_value("Automation Metric Daily", filters, "name", for_update=True)
		else:
			frappe.db.release_savepoint(save_point)
	if name:
		current = frappe.db.get_value("Automation Metric Daily", name, fieldname, for_update=True) or 0
		frappe.db.set_value("Automation Metric Daily", name, fieldname, float(current) + amount, update_modified=False)


def _incident_fingerprint(source_type: str, workflow: str | None, node_id: str | None, error_code: str) -> str:
	return sha256_hash(canonical_json({
		"source_type": source_type,
		"workflow": workflow or "",
		"node_id": node_id or "",
		"error_code": error_code,
	}))


def record_incident(
	*,
	source_type: str,
	source_name: str,
	error_code: str,
	message: str,
	workflow: str | None = None,
	run: str | None = None,
	node_id: str | None = None,
	attempts: int = 0,
	severity: str = "ERROR",
) -> dict | None:
	"""Group a terminal failure and attach one recoverable dead-letter row."""
	if not frappe.db.table_exists("Automation Incident") or not frappe.db.table_exists("Automation Dead Letter"):
		return None
	now = now_datetime()
	error_code = str(error_code or "UNKNOWN_ERROR")[:140]
	fingerprint = _incident_fingerprint(source_type, workflow, node_id, error_code)
	incident_name = frappe.db.get_value("Automation Incident", {"fingerprint": fingerprint}, "name", for_update=True)
	if incident_name:
		incident = frappe.get_doc("Automation Incident", incident_name)
		incident.status = "OPEN"
		incident.severity = severity
		incident.run = run or incident.run
		incident.occurrence_count = cint(incident.occurrence_count) + 1
		incident.last_seen_at = now
		incident.last_message = str(message or "")[:2000]
		incident.resolution = None
		incident.resolved_by = None
		incident.resolved_at = None
		incident.save(ignore_permissions=True)
	else:
		save_point = f"automation_incident_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(save_point)
		try:
			incident = frappe.get_doc(
				{
					"doctype": "Automation Incident",
					"fingerprint": fingerprint,
					"status": "OPEN",
					"severity": severity,
					"workflow": workflow,
					"run": run,
					"node_id": node_id,
					"error_code": error_code,
					"occurrence_count": 1,
					"first_seen_at": now,
					"last_seen_at": now,
					"last_message": str(message or "")[:2000],
				}
			).insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			frappe.db.rollback(save_point=save_point)
			incident_name = frappe.db.get_value("Automation Incident", {"fingerprint": fingerprint}, "name", for_update=True)
			incident = frappe.get_doc("Automation Incident", incident_name)
			incident.status = "OPEN"
			incident.run = run or incident.run
			incident.occurrence_count = cint(incident.occurrence_count) + 1
			incident.last_seen_at = now
			incident.last_message = str(message or "")[:2000]
			incident.save(ignore_permissions=True)
		else:
			frappe.db.release_savepoint(save_point)
	dead_name = frappe.db.get_value(
		"Automation Dead Letter", {"source_type": source_type, "source_name": source_name}, "name", for_update=True
	)
	values = {
		"workflow": workflow,
		"run": run,
		"node_id": node_id,
		"status": "OPEN",
		"error_code": error_code,
		"message": str(message or "")[:2000],
		"attempts": cint(attempts),
		"incident": incident.name,
		"resolved_at": None,
		"resolution": None,
	}
	if dead_name:
		frappe.db.set_value("Automation Dead Letter", dead_name, values, update_modified=False)
	else:
		save_point = f"automation_dead_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(save_point)
		try:
			dead_name = frappe.get_doc(
				{
					"doctype": "Automation Dead Letter",
					"source_type": source_type,
					"source_name": source_name,
					"created_at": now,
					**values,
				}
			).insert(ignore_permissions=True).name
		except frappe.DuplicateEntryError:
			frappe.db.rollback(save_point=save_point)
			dead_name = frappe.db.get_value(
				"Automation Dead Letter", {"source_type": source_type, "source_name": source_name}, "name", for_update=True
			)
			frappe.db.set_value("Automation Dead Letter", dead_name, values, update_modified=False)
		else:
			frappe.db.release_savepoint(save_point)
	_maybe_alert(incident)
	frappe.publish_realtime(
		"automation_incident_updated",
		{"incident_id": incident.name, "dead_letter_id": dead_name, "status": "OPEN"},
		after_commit=True,
	)
	return {"incident": incident.name, "dead_letter": dead_name}


def _maybe_alert(incident) -> None:
	threshold = max(cint(frappe.db.get_single_value("Automation Settings", "incident_alert_threshold") or 3), 1)
	user = frappe.db.get_single_value("Automation Settings", "incident_alert_user")
	if cint(incident.occurrence_count) != threshold or not user or not frappe.db.get_value("User", user, "enabled"):
		return
	enqueue_notification_for_user(
		user,
		{
			"type": "Alert",
			"document_type": "Automation Incident",
			"document_name": incident.name,
			"subject": f"Automation incident {incident.error_code} reached {threshold} occurrences",
			"from_user": frappe.session.user,
		},
	)


def list_enrollment_decisions(*, workflow: str | None = None, record_doctype: str | None = None, record_name: str | None = None, decision: str | None = None, reason_code: str | None = None, source: str | None = None, date_from: str | None = None, date_to: str | None = None, start: int = 0, page_length: int = 50) -> dict:
	page_length = min(max(cint(page_length), 1), 100)
	filters = {}
	if workflow:
		filters["workflow"] = workflow
	if record_doctype:
		filters["record_doctype"] = record_doctype
	if record_name:
		filters["record_name"] = ["like", f"%{record_name}%"]
	if decision:
		filters["decision"] = decision
	if reason_code:
		filters["reason_code"] = reason_code
	if source:
		filters["source"] = source

	if date_from and date_to:
		filters["decided_at"] = ["between", [date_from, date_to]]
	elif date_from:
		filters["decided_at"] = [">=", date_from]
	elif date_to:
		filters["decided_at"] = ["<=", date_to]

	rows = frappe.get_list(
		"Automation Enrollment Decision",
		filters=filters,
		fields=["name", "workflow", "workflow_version", "record_doctype", "record_name", "source", "decision", "reason_code", "evidence_json", "trace_id", "run", "decided_at"],
		order_by="creation desc",
		start=max(cint(start), 0),
		limit=page_length + 1,
	)
	return {"rows": rows[:page_length], "has_more": len(rows) > page_length}


def export_enrollment_decisions(*, workflow: str | None = None, record_doctype: str | None = None, record_name: str | None = None, decision: str | None = None, reason_code: str | None = None, source: str | None = None, date_from: str | None = None, date_to: str | None = None) -> None:
	filters = {}
	if workflow:
		filters["workflow"] = workflow
	if record_doctype:
		filters["record_doctype"] = record_doctype
	if record_name:
		filters["record_name"] = ["like", f"%{record_name}%"]
	if decision:
		filters["decision"] = decision
	if reason_code:
		filters["reason_code"] = reason_code
	if source:
		filters["source"] = source

	if date_from and date_to:
		filters["decided_at"] = ["between", [date_from, date_to]]
	elif date_from:
		filters["decided_at"] = [">=", date_from]
	elif date_to:
		filters["decided_at"] = ["<=", date_to]

	rows = frappe.get_list(
		"Automation Enrollment Decision",
		filters=filters,
		fields=["name", "workflow", "workflow_version", "record_doctype", "record_name", "source", "decision", "reason_code", "run", "decided_at"],
		order_by="creation desc",
		limit=50000,
	)

	import csv
	from io import StringIO

	f = StringIO()
	writer = csv.writer(f)
	writer.writerow(["ID", "Workflow", "Version", "Record DocType", "Record Name", "Source", "Decision", "Reason", "Run", "Decided At"])

	def csv_safe(value):
		text = "" if value is None else str(value)
		if text.startswith(("=", "+", "-", "@")):
			return "'" + text
		return text

	for r in rows:
		writer.writerow([csv_safe(value) for value in [r.name, r.workflow, r.workflow_version, r.record_doctype, r.record_name, r.source, r.decision, r.reason_code, r.run, r.decided_at]])

	frappe.response['result'] = f.getvalue()
	frappe.response['type'] = 'csv'
	frappe.response['doctype'] = 'Automation_Enrollment_Decisions'


def list_incidents(*, status: str | None = "OPEN", start: int = 0, page_length: int = 50) -> dict:
	filters = {} if not status or status == "ALL" else {"status": str(status).upper()}
	page_length = min(max(cint(page_length), 1), 100)
	rows = frappe.get_list(
		"Automation Incident", filters=filters,
		fields=["name", "fingerprint", "status", "severity", "workflow", "run", "node_id", "error_code", "occurrence_count", "first_seen_at", "last_seen_at", "last_message", "resolution", "resolved_by", "resolved_at"],
		order_by="last_seen_at desc", start=max(cint(start), 0), limit=page_length + 1,
	)
	return {"rows": rows[:page_length], "has_more": len(rows) > page_length}


def list_dead_letters(*, status: str | None = "OPEN", start: int = 0, page_length: int = 50) -> dict:
	filters = {} if not status or status == "ALL" else {"status": str(status).upper()}
	page_length = min(max(cint(page_length), 1), 100)
	rows = frappe.get_list(
		"Automation Dead Letter", filters=filters,
		fields=["name", "source_type", "source_name", "workflow", "run", "node_id", "status", "error_code", "message", "attempts", "incident", "created_at", "resolved_at", "resolution"],
		order_by="creation desc", start=max(cint(start), 0), limit=page_length + 1,
	)
	return {"rows": rows[:page_length], "has_more": len(rows) > page_length}


def resolve_incident(incident_name: str, resolution: str) -> dict:
	incident = frappe.get_doc("Automation Incident", incident_name, for_update=True)
	if incident.status == "RESOLVED":
		return {"incident_id": incident.name, "status": incident.status}
	incident.status = "RESOLVED"
	incident.resolution = str(resolution or "Resolved by operator.")[:2000]
	incident.resolved_by = frappe.session.user
	incident.resolved_at = now_datetime()
	incident.save(ignore_permissions=True)
	frappe.db.set_value(
		"Automation Dead Letter", {"incident": incident.name, "status": ["in", ["OPEN", "RETRYING"]]},
		{"status": "RESOLVED", "resolved_at": incident.resolved_at, "resolution": incident.resolution},
		update_modified=False,
	)
	frappe.publish_realtime("automation_incident_updated", {"incident_id": incident.name, "status": "RESOLVED"}, after_commit=True)
	return {"incident_id": incident.name, "status": incident.status}


def retry_dead_letter(dead_letter_name: str) -> dict:
	letter = frappe.get_doc("Automation Dead Letter", dead_letter_name, for_update=True)
	if letter.status not in {"OPEN", "RETRYING"}:
		raise AutomationError("Only open dead letters can be retried.")
	letter.status = "RETRYING"
	letter.save(ignore_permissions=True)
	if letter.source_type == "OUTBOX":
		from .events import retry_outbox_event
		result = retry_outbox_event(letter.source_name)
	elif letter.source_type == "RUN":
		from .engine import retry_run_record
		result = retry_run_record(letter.run or letter.source_name)
	elif letter.source_type == "EXTERNAL":
		raise AutomationError("Ambiguous external effects must be reconciled as delivered or not delivered; they are never blindly retried.")
	elif letter.source_type == "BACKFILL":
		from .bulk import change_backfill_state
		result = change_backfill_state(letter.source_name, "RETRY")
	else:
		raise AutomationError("This dead-letter source cannot be retried.")
	letter.status = "RESOLVED"
	letter.resolved_at = now_datetime()
	letter.resolution = "Recovery accepted by operator."
	letter.save(ignore_permissions=True)
	return {"dead_letter_id": letter.name, "status": letter.status, "result": result}


def reconcile_dead_letter(dead_letter_name: str, resolution: str) -> dict:
	letter = frappe.get_doc("Automation Dead Letter", dead_letter_name, for_update=True)
	if letter.source_type != "EXTERNAL" or letter.status != "OPEN":
		raise AutomationError("Only open external dead letters can be reconciled.")
	from .engine import reconcile_external_effect

	result = reconcile_external_effect(letter.source_name, resolution)
	letter.status = "RESOLVED"
	letter.resolved_at = now_datetime()
	letter.resolution = f"External effect reconciled as {str(resolution).upper()}."
	letter.save(ignore_permissions=True)
	return {"dead_letter_id": letter.name, "status": letter.status, "result": result}


def bulk_retry_dead_letters(dead_letter_names: list[str]) -> dict:
	results = []
	for name in list(dict.fromkeys(str(value) for value in dead_letter_names if value))[:100]:
		letter = frappe.db.get_value("Automation Dead Letter", name, ["name", "source_type", "status"], as_dict=True)
		if not letter or letter.status != "OPEN" or letter.source_type == "EXTERNAL":
			continue
		results.append(retry_dead_letter(name))
	return {"count": len(results), "rows": results}


def discard_dead_letter(dead_letter_name: str) -> dict:
	letter = frappe.get_doc("Automation Dead Letter", dead_letter_name, for_update=True)
	if letter.status != "OPEN":
		raise AutomationError("Only open dead letters can be discarded.")

	if letter.source_type == "OUTBOX":
		from .events import discard_outbox_event
		discard_outbox_event(letter.source_name)

	letter.status = "RESOLVED"
	letter.resolved_at = now_datetime()
	letter.resolution = "Discarded by operator."
	letter.save(ignore_permissions=True)
	return {"dead_letter_id": letter.name, "status": letter.status, "result": "DISCARDED"}


def bulk_discard_dead_letters(dead_letter_names: list[str]) -> dict:
	results = []
	for name in list(dict.fromkeys(str(value) for value in dead_letter_names if value))[:100]:
		letter = frappe.db.get_value("Automation Dead Letter", name, ["name", "status"], as_dict=True)
		if not letter or letter.status != "OPEN":
			continue
		results.append(discard_dead_letter(name))
	return {"count": len(results), "rows": results}


def analytics(workflow: str | None = None, *, days: int = 30) -> dict:
	days = min(max(cint(days), 1), 365)
	filters: dict[str, Any] = {"metric_date": [">=", frappe.utils.add_days(getdate(), -days + 1)]}
	if workflow:
		filters["workflow"] = workflow
	rows = frappe.get_list(
		"Automation Metric Daily", filters=filters,
		fields=["metric_date", "workflow", "workflow_version", *sorted(METRIC_FIELDS)],
		order_by="metric_date asc", limit=10000,
	)
	totals = {field: 0 for field in METRIC_FIELDS}
	for row in rows:
		for field in METRIC_FIELDS:
			totals[field] += row.get(field) or 0
	return {"rows": rows, "totals": totals, "days": days}


def check_queue_health() -> None:
	if not frappe.db.table_exists("Automation Settings"):
		return

	settings = frappe.get_single("Automation Settings")
	if not cint(settings.enabled):
		return

	timer_lag_mins = cint(settings.alert_timer_lag_minutes)
	queue_age_mins = cint(settings.alert_queue_age_minutes)

	now = now_datetime()

	if timer_lag_mins > 0:
		threshold_time = frappe.utils.add_to_date(now, minutes=-timer_lag_mins)
		stuck_timer = frappe.db.get_value("Automation Timer", {"status": "ACTIVE", "due_at": ["<", threshold_time]}, "name")
		if stuck_timer:
			record_incident(
				source_type="SYSTEM",
				source_name="timer_lag_monitor",
				error_code="TIMER_LAG_EXCEEDED",
				message=f"Active timer {stuck_timer} is delayed beyond threshold of {timer_lag_mins} minutes.",
				severity="WARNING",
			)

	if queue_age_mins > 0:
		threshold_time = frappe.utils.add_to_date(now, minutes=-queue_age_mins)
		stuck_token = frappe.db.get_value("Automation Run Token", {"status": "READY", "available_at": ["<", threshold_time]}, "name")
		if stuck_token:
			record_incident(
				source_type="SYSTEM",
				source_name="queue_age_monitor",
				error_code="TOKEN_QUEUE_AGE_EXCEEDED",
				message=f"Ready token {stuck_token} is delayed beyond threshold of {queue_age_mins} minutes.",
				severity="WARNING",
			)

		stuck_outbox = frappe.db.get_value("Automation Outbox Event", {"status": "PENDING", "available_at": ["<", threshold_time]}, "name")
		if stuck_outbox:
			record_incident(
				source_type="SYSTEM",
				source_name="queue_age_monitor",
				error_code="OUTBOX_QUEUE_AGE_EXCEEDED",
				message=f"Pending outbox event {stuck_outbox} is delayed beyond threshold of {queue_age_mins} minutes.",
				severity="WARNING",
			)
