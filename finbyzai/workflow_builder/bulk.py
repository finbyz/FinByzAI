from __future__ import annotations

import json
import hashlib
import hmac
import math
import calendar
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, get_system_timezone, now_datetime
from frappe.utils.verified_command import get_secret

from .authoring import create_audit
from .configuration import automation_enabled, workflow_runtime_allowed
from .engine import enroll, published_trigger_type
from .observability import record_incident
from .errors import AutomationConflictError, AutomationError
from .registry import field_catalog

BACKFILL_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "in", "not in", "like", "not like", "is"}
BACKFILL_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
BACKFILL_ACTIVE_STATUSES = {"QUEUED", "RUNNING", "PAUSED"}
SCHEDULE_FREQUENCIES = {"ONCE", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "ANNUAL", "DATE_FIELD"}
BACKFILL_PREVIEW_TTL_MINUTES = 15


def _receipt_message(payload: dict) -> str:
	return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sign_preview_receipt(payload: dict) -> dict:
	signature = hmac.new(
		get_secret().encode(),
		_receipt_message(payload).encode(),
		digestmod=hashlib.sha256,
	).hexdigest()
	return {**payload, "signature": signature}


def _verify_preview_receipt(value: Any) -> dict:
	receipt = frappe.parse_json(value) if isinstance(value, str) else value
	if not isinstance(receipt, dict):
		raise AutomationConflictError(_("Preview the audience again before starting this backfill."))
	payload = {key: item for key, item in receipt.items() if key != "signature"}
	expected = hmac.new(
		get_secret().encode(),
		_receipt_message(payload).encode(),
		digestmod=hashlib.sha256,
	).hexdigest()
	if not hmac.compare_digest(str(receipt.get("signature") or ""), expected):
		raise AutomationConflictError(_("The backfill preview receipt is invalid. Preview the audience again."))
	if get_datetime(payload.get("expires_at")) < now_datetime():
		raise AutomationConflictError(_("The backfill preview expired. Preview the audience again."))
	return payload


def _parse_filters(value: Any) -> list:
	if value in (None, ""):
		return []
	if isinstance(value, str):
		value = frappe.parse_json(value)
	if isinstance(value, dict):
		return [[key, "=", item] for key, item in value.items()]
	if isinstance(value, list):
		return value
	raise AutomationError(_("Backfill filters must be a JSON object or filter list."))


def _validate_filters(doctype: str, filters: list, execution_user: str) -> None:
	allowed = {row["fieldname"] for row in field_catalog(doctype, user=execution_user)} | {
		"name",
		"owner",
		"creation",
		"modified",
		"docstatus",
	}
	for item in filters:
		if not isinstance(item, (list, tuple)) or len(item) not in {3, 4}:
			raise AutomationError(_("Every backfill filter must use Frappe list-filter syntax."))
		fieldname = item[-3] if len(item) == 4 else item[0]
		operator = str(item[-2]).lower().strip()
		if fieldname not in allowed:
			raise AutomationError(_("Backfill filter field {0} is unavailable.").format(fieldname))
		if operator not in BACKFILL_OPERATORS:
			raise AutomationError(_("Backfill filter operator {0} is unavailable.").format(operator))


def _workflow_version(workflow, version_name: str | None = None):
	selected = version_name or workflow.active_version
	if not selected:
		raise AutomationError(_("Publish the workflow before enrolling existing records."))
	version = frappe.get_doc("Automation Workflow Version", selected)
	if version.workflow != workflow.name:
		raise AutomationError(_("The selected version does not belong to this workflow."))
	return version


def _candidate_filters(filters: list, snapshot_at=None, cursor_name: str | None = None) -> list:
	result = [list(item) if isinstance(item, tuple) else item for item in filters]
	if snapshot_at:
		result.append(["creation", "<=", snapshot_at])
	if cursor_name:
		result.append(["name", ">", cursor_name])
	return result


def _count_candidates(doctype: str, filters: list, execution_user: str) -> int:
	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=[{"COUNT": "name", "as": "count"}],
		limit=1,
		user=execution_user,
	)
	return cint(rows[0].count) if rows else 0


def preview_backfill(workflow_name: str, filters: Any = None, sample_size: int = 10, max_records: int = 0) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("read")
	version = _workflow_version(workflow)
	parsed_filters = _parse_filters(filters)
	_validate_filters(workflow.primary_doctype, parsed_filters, version.execution_user)
	snapshot_at = now_datetime()
	candidates = _candidate_filters(parsed_filters, snapshot_at)
	total = _count_candidates(workflow.primary_doctype, candidates, version.execution_user)
	bounded_total = min(total, cint(max_records)) if cint(max_records) > 0 else total
	receipt = _sign_preview_receipt(
		{
			"workflow_id": workflow.name,
			"workflow_version": version.name,
			"snapshot_at": str(snapshot_at),
			"filters": parsed_filters,
			"max_records": max(cint(max_records), 0),
			"expires_at": str(add_to_date(snapshot_at, minutes=BACKFILL_PREVIEW_TTL_MINUTES)),
		}
	)
	samples = frappe.get_list(
		workflow.primary_doctype,
		filters=candidates,
		fields=["name"],
		order_by="name asc",
		limit=min(max(cint(sample_size), 1), 25),
		user=version.execution_user,
	)
	return {
		"workflow_id": workflow.name,
		"workflow_version": version.name,
		"version_no": cint(version.version_no),
		"primary_doctype": workflow.primary_doctype,
		"execution_user": version.execution_user,
		"snapshot_at": snapshot_at,
		"estimated_count": bounded_total,
		"unbounded_count": total,
		"sample_records": [row.name for row in samples],
		"filters": parsed_filters,
		"receipt": receipt,
	}


def create_backfill(
	workflow_name: str,
	filters: Any = None,
	batch_size: int = 100,
	*,
	source: str = "BACKFILL",
	workflow_version: str | None = None,
	schedule: str | None = None,
	dry_run: bool = False,
	max_records: int = 0,
	records_per_minute: int = 500,
	preview_receipt: Any = None,
) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("read")
	if workflow.status != "ACTIVE":
		raise AutomationConflictError(_("Activate the workflow before starting a backfill."))
	if not workflow_runtime_allowed(workflow.name):
		raise AutomationConflictError(_("Automation runtime is disabled in Automation Settings."))
	parsed_filters = _parse_filters(filters)
	snapshot_at = now_datetime()
	if preview_receipt:
		receipt = _verify_preview_receipt(preview_receipt)
		if receipt.get("workflow_id") != workflow.name:
			raise AutomationConflictError(_("This preview belongs to a different workflow."))
		if _receipt_message(receipt.get("filters") or []) != _receipt_message(parsed_filters):
			raise AutomationConflictError(_("The audience filters changed. Preview the audience again."))
		if cint(receipt.get("max_records")) != max(cint(max_records), 0):
			raise AutomationConflictError(_("The record limit changed. Preview the audience again."))
		workflow_version = receipt.get("workflow_version")
		snapshot_at = get_datetime(receipt.get("snapshot_at"))
	version = _workflow_version(workflow, workflow_version)
	_validate_filters(workflow.primary_doctype, parsed_filters, version.execution_user)
	source = str(source or "BACKFILL").upper()
	if source not in {"BACKFILL", "SCHEDULE"}:
		raise AutomationError(_("Unsupported backfill source."))
	estimated_count = _count_candidates(
		workflow.primary_doctype,
		_candidate_filters(parsed_filters, snapshot_at),
		version.execution_user,
	)
	if cint(max_records) > 0:
		estimated_count = min(estimated_count, cint(max_records))
	job = frappe.get_doc(
		{
			"doctype": "Automation Backfill Job",
			"workflow": workflow.name,
			"workflow_version": version.name,
			"source": source,
			"schedule": schedule,
			"status": "QUEUED",
			"snapshot_at": snapshot_at,
			"filters_json": json.dumps(parsed_filters, default=str),
			"batch_size": min(max(cint(batch_size), 1), 500),
			"records_per_minute": min(max(cint(records_per_minute), 1), 10000),
			"max_records": max(cint(max_records), 0),
			"estimated_count": estimated_count,
			"processed_count": 0,
			"enrolled_count": 0,
			"failed_count": 0,
			"dry_run": bool(dry_run),
			"next_batch_at": snapshot_at,
		}
	).insert(ignore_permissions=True)
	create_audit(
		workflow.name,
		"BACKFILL_CREATED",
		{
			"backfill": job.name,
			"source": source,
			"workflow_version": version.name,
			"dry_run": bool(dry_run),
			"estimated_count": estimated_count,
		},
	)
	_queue_backfill(job.name, 0)
	_publish_backfill(job, "CREATED")
	return {
		"backfill_id": job.name,
		"status": job.status,
		"workflow_version": version.name,
		"estimated_count": estimated_count,
		"dry_run": bool(job.dry_run),
	}


def _queue_backfill(job_name: str, page: int) -> None:
	frappe.enqueue(
		"finbyzai.workflow_builder.bulk.process_backfill",
		backfill_name=job_name,
		queue="long",
		enqueue_after_commit=True,
		job_id=f"automation-backfill-{job_name}-{page}",
		deduplicate=True,
	)


def _publish_backfill(job, event_type: str) -> None:
	frappe.publish_realtime(
		"automation_backfill_updated",
		{
			"workflow_id": job.workflow,
			"backfill_id": job.name,
			"status": job.status,
			"event_type": event_type,
			"processed_count": cint(job.processed_count),
			"enrolled_count": cint(job.enrolled_count),
		},
		doctype="Automation Workflow",
		docname=job.workflow,
		after_commit=True,
	)


def dispatch_ready_backfills() -> int:
	if not automation_enabled() or not frappe.db.table_exists("Automation Backfill Job"):
		return 0
	rows = frappe.db.get_values(
		"Automation Backfill Job",
		filters={"status": "QUEUED", "next_batch_at": ["<=", now_datetime()]},
		fieldname=["name", "workflow", "processed_count"],
		as_dict=True,
		order_by="next_batch_at asc",
		limit=20,
		for_update=True,
		skip_locked=True,
	)
	queued = 0
	for row in rows:
		if not workflow_runtime_allowed(row.workflow):
			continue
		_queue_backfill(row.name, cint(row.processed_count))
		queued += 1
	return queued


def _fail_backfill(job_name: str, error: Exception, save_point: str) -> int:
	message = str(error)[:2000]
	frappe.db.rollback(save_point=save_point)
	frappe.db.release_savepoint(save_point)
	job = frappe.get_doc("Automation Backfill Job", job_name, for_update=True)
	if job.status not in {"CANCELLED", "PAUSED"}:
		job.status = "FAILED"
		job.failed_count = cint(job.failed_count) + 1
		job.error_message = message
		job.last_heartbeat_at = now_datetime()
		job.save(ignore_permissions=True)
		create_audit(job.workflow, "BACKFILL_FAILED", {"backfill": job.name, "error": message})
		_publish_backfill(job, "FAILED")
		record_incident(
			source_type="BACKFILL", source_name=job.name, workflow=job.workflow,
			error_code=getattr(error, "code", type(error).__name__), message=message, attempts=cint(job.failed_count),
		)
	frappe.log_error(title=f"Automation backfill {job_name} failed", message=frappe.get_traceback(with_context=True))
	return 0


def process_backfill(backfill_name: str) -> int:
	if not frappe.db.exists("Automation Backfill Job", backfill_name):
		return 0
	save_point = "automation_backfill_batch"
	frappe.db.savepoint(save_point)
	try:
		job = frappe.get_doc("Automation Backfill Job", backfill_name, for_update=True)
		if job.status in BACKFILL_TERMINAL_STATUSES or job.status == "PAUSED":
			frappe.db.release_savepoint(save_point)
			return 0
		if not workflow_runtime_allowed(job.workflow):
			frappe.db.release_savepoint(save_point)
			return 0
		workflow = frappe.get_doc("Automation Workflow", job.workflow)
		version = _workflow_version(workflow, job.workflow_version)
		filters = _candidate_filters(_parse_filters(job.filters_json), job.snapshot_at, job.cursor_name)
		remaining = cint(job.max_records) - cint(job.processed_count) if cint(job.max_records) > 0 else cint(job.batch_size)
		page_size = min(cint(job.batch_size), remaining) if cint(job.max_records) > 0 else cint(job.batch_size)
		if page_size <= 0:
			rows = []
		else:
			rows = frappe.get_list(
				workflow.primary_doctype,
				filters=filters,
				fields=["name"],
				order_by="name asc",
				limit=page_size,
				user=version.execution_user,
			)
		if not job.started_at:
			job.started_at = now_datetime()
		job.status = "RUNNING"
		job.last_heartbeat_at = now_datetime()
		enrolled = 0
		if not job.dry_run:
			for row in rows:
				enrolled += bool(
					enroll(
						workflow.name,
						workflow.primary_doctype,
						row.name,
						source=job.source,
						occurrence_key=f"{job.name}:{row.name}",
						workflow_version=version.name,
						causation_id=job.name,
					)
				)
		job.processed_count = cint(job.processed_count) + len(rows)
		job.enrolled_count = cint(job.enrolled_count) + enrolled
		if rows:
			job.cursor_name = rows[-1].name
		limit_reached = cint(job.max_records) > 0 and cint(job.processed_count) >= cint(job.max_records)
		if len(rows) < page_size or limit_reached:
			job.status = "COMPLETED"
			job.completed_at = now_datetime()
			job.next_batch_at = None
			job.error_message = None
			event_type = "COMPLETED"
		else:
			job.status = "QUEUED"
			seconds = max(1, math.ceil(len(rows) / max(cint(job.records_per_minute), 1) * 60))
			job.next_batch_at = add_to_date(now_datetime(), seconds=seconds)
			event_type = "BATCH_COMPLETED"
		job.last_heartbeat_at = now_datetime()
		job.save(ignore_permissions=True)
		_publish_backfill(job, event_type)
		if job.status == "QUEUED" and get_datetime(job.next_batch_at) <= now_datetime():
			_queue_backfill(job.name, cint(job.processed_count))
		frappe.db.release_savepoint(save_point)
		return len(rows)
	except Exception as error:
		return _fail_backfill(backfill_name, error, save_point)


def change_backfill_state(job_name: str, action: str) -> dict:
	job = frappe.get_doc("Automation Backfill Job", job_name, for_update=True)
	job.check_permission("read")
	action = str(action or "").upper()
	if action == "PAUSE":
		if job.status not in {"QUEUED", "RUNNING"}:
			raise AutomationConflictError(_("Only queued or running backfills can be paused."))
		job.status = "PAUSED"
	elif action == "RESUME":
		if job.status != "PAUSED":
			raise AutomationConflictError(_("Only paused backfills can be resumed."))
		job.status = "QUEUED"
		job.next_batch_at = now_datetime()
	elif action == "CANCEL":
		if job.status in BACKFILL_TERMINAL_STATUSES:
			raise AutomationConflictError(_("This backfill is already finished."))
		job.status = "CANCELLED"
		job.completed_at = now_datetime()
		job.next_batch_at = None
	elif action == "RETRY":
		if job.status != "FAILED":
			raise AutomationConflictError(_("Only failed backfills can be retried."))
		job.status = "QUEUED"
		job.next_batch_at = now_datetime()
		job.error_message = None
	else:
		raise AutomationError(_("Unsupported backfill control action."))
	job.save(ignore_permissions=True)
	create_audit(job.workflow, f"BACKFILL_{action}", {"backfill": job.name})
	_publish_backfill(job, action)
	if job.status == "QUEUED":
		_queue_backfill(job.name, cint(job.processed_count))
	return {"backfill_id": job.name, "status": job.status}


def _zone(timezone: str) -> ZoneInfo:
	try:
		return ZoneInfo(str(timezone or ""))
	except (ZoneInfoNotFoundError, ValueError):
		raise AutomationError(_("Choose a valid IANA timezone."))


def _localized(value, timezone: str, *, shift_nonexistent_forward: bool = False):
	"""Attach a timezone while rejecting spring-forward wall times.

	Ambiguous fall-back values consistently use fold=0 (the earlier instant).
	"""
	zone = _zone(timezone)
	naive = get_datetime(value).replace(tzinfo=None)
	local = naive.replace(tzinfo=zone, fold=0)
	round_trip = local.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None)
	if round_trip != naive:
		if shift_nonexistent_forward and round_trip > naive:
			return round_trip.replace(tzinfo=zone, fold=0)
		raise AutomationError(_("The selected local time does not exist because of daylight-saving time. Choose another time."))
	return local


def _local_to_system(value, timezone: str):
	return _localized(value, timezone).astimezone(_zone(get_system_timezone())).replace(tzinfo=None)


def _recurrence(value: Any) -> dict:
	parsed = frappe.parse_json(value) if isinstance(value, str) else value
	return parsed if isinstance(parsed, dict) else {}


def _month_target(year: int, month: int, rule: dict, fallback_day: int) -> int:
	last_day = calendar.monthrange(year, month)[1]
	mode = str(rule.get("monthly_mode") or "DAY").upper()
	if mode == "FIRST_WEEKDAY":
		weekday = min(max(cint(rule.get("weekday")), 0), 6)
		return 1 + (weekday - datetime(year, month, 1).weekday()) % 7
	if mode == "LAST_WEEKDAY":
		weekday = min(max(cint(rule.get("weekday")), 0), 6)
		return last_day - (datetime(year, month, last_day).weekday() - weekday) % 7
	return min(max(cint(rule.get("day")) or fallback_day, 1), last_day)


def _next_occurrence(value, frequency: str, timezone: str, recurrence: Any = None):
	current = get_datetime(value).replace(tzinfo=_zone(get_system_timezone())).astimezone(_zone(timezone))
	frequency = str(frequency or "").upper()
	if frequency == "ONCE":
		return None
	rule = _recurrence(recurrence)
	if frequency == "HOURLY":
		return (current + timedelta(hours=1)).astimezone(_zone(get_system_timezone())).replace(tzinfo=None)
	local = current.replace(tzinfo=None)
	if frequency in {"DAILY", "DATE_FIELD"}:
		next_local = local + timedelta(days=1)
	elif frequency == "WEEKLY":
		next_local = local + timedelta(weeks=1)
	elif frequency == "MONTHLY":
		year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
		next_local = local.replace(year=year, month=month, day=_month_target(year, month, rule, local.day))
	elif frequency == "ANNUAL":
		year = local.year + 1
		month = min(max(cint(rule.get("month")) or local.month, 1), 12)
		day = min(max(cint(rule.get("day")) or local.day, 1), calendar.monthrange(year, month)[1])
		next_local = local.replace(year=year, month=month, day=day)
	else:
		raise AutomationError(_("Unsupported schedule frequency."))
	# A recurrence configured on an ordinary day can later land inside a DST
	# spring-forward gap. Run it at the corresponding first valid wall time;
	# direct user-entered timestamps remain strictly rejected by _local_to_system.
	return _localized(next_local, timezone, shift_nonexistent_forward=True).astimezone(_zone(get_system_timezone())).replace(tzinfo=None)


def _advance_after_now(value, frequency: str, timezone: str, now, recurrence: Any = None) -> Any:
	next_value = get_datetime(value)
	for _iteration in range(10000):
		if next_value > now:
			return next_value
		next_value = _next_occurrence(next_value, frequency, timezone, recurrence)
		if next_value is None:
			return None
	raise AutomationError(_("The schedule is too far behind to recover automatically."))


def create_schedule(
	workflow_name: str,
	frequency: str,
	next_run_at: str,
	filters: Any = None,
	batch_size: int = 100,
	*,
	timezone: str | None = None,
	version_policy: str = "ACTIVE_AT_RUN",
	workflow_version: str | None = None,
	catch_up_policy: str = "RUN_ONCE",
	overlap_policy: str = "SKIP",
	max_records: int = 0,
	records_per_minute: int = 500,
	recurrence: Any = None,
) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("read")
	version = _workflow_version(workflow, workflow_version if str(version_policy).upper() == "PINNED" else None)
	if published_trigger_type(version.name) != "trigger.schedule":
		raise AutomationConflictError(_("Only workflows published with a scheduled trigger can create schedules."))
	frequency = str(frequency or "").upper()
	if frequency not in SCHEDULE_FREQUENCIES:
		raise AutomationError(_("Choose once, hourly, daily, weekly, monthly, annual, or Date-field scheduling."))
	timezone = str(timezone or get_system_timezone())
	_zone(timezone)
	parsed_filters = _parse_filters(filters)
	parsed_recurrence = _recurrence(recurrence)
	_validate_filters(workflow.primary_doctype, parsed_filters, version.execution_user)
	if frequency == "DATE_FIELD":
		date_field = str(parsed_recurrence.get("date_field") or "").strip()
		field = next((row for row in field_catalog(workflow.primary_doctype, user=version.execution_user) if row.get("fieldname") == date_field), None)
		if not field or field.get("fieldtype") not in {"Date", "Datetime"}:
			raise AutomationError(_("Choose a readable Date or Datetime field for this schedule."))
		parsed_recurrence["date_field_type"] = field.get("fieldtype")
	schedule = frappe.get_doc(
		{
			"doctype": "Automation Schedule",
			"workflow": workflow.name,
			"version_policy": str(version_policy or "ACTIVE_AT_RUN").upper(),
			"workflow_version": version.name if str(version_policy).upper() == "PINNED" else None,
			"enabled": 0,
			"frequency": frequency,
			"recurrence_json": json.dumps(parsed_recurrence),
			"timezone": timezone,
			"catch_up_policy": str(catch_up_policy or "RUN_ONCE").upper(),
			"overlap_policy": str(overlap_policy or "SKIP").upper(),
			"filters_json": json.dumps(parsed_filters),
			"batch_size": min(max(cint(batch_size), 1), 500),
			"records_per_minute": min(max(cint(records_per_minute), 1), 10000),
			"max_records": max(cint(max_records), 0),
			"next_run_at": _local_to_system(next_run_at, timezone),
		}
	).insert()
	create_audit(
		workflow.name,
		"SCHEDULE_CREATED",
		{"schedule": schedule.name, "frequency": frequency, "version_policy": schedule.version_policy},
	)
	return {"schedule_id": schedule.name, "enabled": False, "next_run_at": schedule.next_run_at}


def set_schedule_enabled(schedule_name: str, enabled: bool) -> dict:
	schedule = frappe.get_doc("Automation Schedule", schedule_name, for_update=True)
	schedule.check_permission("write")
	if enabled:
		workflow = frappe.get_doc("Automation Workflow", schedule.workflow)
		if workflow.status != "ACTIVE":
			raise AutomationConflictError(_("Activate the workflow before enabling its schedule."))
		if not workflow_runtime_allowed(workflow.name):
			raise AutomationConflictError(_("Automation runtime is disabled in Automation Settings."))
		version = _workflow_version(workflow, schedule.workflow_version if schedule.version_policy == "PINNED" else None)
		if published_trigger_type(version.name) != "trigger.schedule":
			raise AutomationConflictError(_("This workflow's published trigger no longer permits scheduled enrollment."))
		schedule.next_run_at = _advance_after_now(
			schedule.next_run_at,
			schedule.frequency,
			schedule.timezone,
			now_datetime(),
			schedule.recurrence_json,
		) if get_datetime(schedule.next_run_at) < now_datetime() and schedule.catch_up_policy == "SKIP" else schedule.next_run_at
		if schedule.next_run_at is None:
			raise AutomationConflictError(_("This one-time occurrence has already passed. Choose a new date and time."))
	schedule.enabled = bool(enabled)
	schedule.save()
	create_audit(schedule.workflow, "SCHEDULE_ENABLED" if enabled else "SCHEDULE_DISABLED", {"schedule": schedule.name})
	return {"schedule_id": schedule.name, "enabled": bool(schedule.enabled), "next_run_at": schedule.next_run_at}


def delete_schedule(schedule_name: str) -> dict:
	schedule = frappe.get_doc("Automation Schedule", schedule_name, for_update=True)
	schedule.check_permission("delete")
	if schedule.enabled:
		raise AutomationConflictError(_("Disable the schedule before deleting it."))
	if frappe.db.exists("Automation Backfill Job", {"schedule": schedule.name, "status": ["in", list(BACKFILL_ACTIVE_STATUSES)]}):
		raise AutomationConflictError(_("Wait for or cancel active scheduled backfills before deleting this schedule."))
	if frappe.db.exists("Automation Backfill Job", {"schedule": schedule.name}):
		raise AutomationConflictError(_("Schedules with execution history cannot be deleted. Keep this schedule disabled for audit history."))
	workflow_name = schedule.workflow
	frappe.delete_doc("Automation Schedule", schedule.name)
	create_audit(workflow_name, "SCHEDULE_DELETED", {"schedule": schedule_name})
	return {"schedule_id": schedule_name, "deleted": True}


def dispatch_due_schedules() -> int:
	if not automation_enabled() or not frappe.db.table_exists("Automation Schedule"):
		return 0
	now = now_datetime()
	rows = frappe.db.get_values(
		"Automation Schedule",
		filters={"enabled": 1, "next_run_at": ["<=", now]},
		fieldname=["name"],
		as_dict=True,
		order_by="next_run_at asc",
		limit=20,
		for_update=True,
		skip_locked=True,
	)
	created = 0
	for row in rows:
		schedule = frappe.get_doc("Automation Schedule", row.name, for_update=True)
		if not workflow_runtime_allowed(schedule.workflow):
			continue
		next_cycle = _next_occurrence(schedule.next_run_at, schedule.frequency, schedule.timezone, schedule.recurrence_json)
		missed_cycle = bool(next_cycle and next_cycle <= now)
		active_job = frappe.db.exists(
			"Automation Backfill Job",
			{"schedule": schedule.name, "status": ["in", list(BACKFILL_ACTIVE_STATUSES)]},
		)
		should_run = not (schedule.catch_up_policy == "SKIP" and missed_cycle)
		if schedule.overlap_policy == "SKIP" and active_job:
			should_run = False
		if should_run:
			workflow = frappe.get_doc("Automation Workflow", schedule.workflow)
			version_name = schedule.workflow_version if schedule.version_policy == "PINNED" else workflow.active_version
			filters = _parse_filters(schedule.filters_json)
			if schedule.frequency == "DATE_FIELD":
				rule = _recurrence(schedule.recurrence_json)
				fieldname = str(rule.get("date_field") or "")
				local_due = get_datetime(schedule.next_run_at).replace(tzinfo=_zone(get_system_timezone())).astimezone(_zone(schedule.timezone))
				if rule.get("date_field_type") == "Datetime":
					start = _localized(datetime.combine(local_due.date(), datetime.min.time()), schedule.timezone, shift_nonexistent_forward=True).astimezone(_zone(get_system_timezone())).replace(tzinfo=None)
					end = _localized(datetime.combine(local_due.date() + timedelta(days=1), datetime.min.time()), schedule.timezone, shift_nonexistent_forward=True).astimezone(_zone(get_system_timezone())).replace(tzinfo=None)
					filters.extend([[fieldname, ">=", start], [fieldname, "<", end]])
				else:
					filters.append([fieldname, "=", local_due.date().isoformat()])
			result = create_backfill(
				schedule.workflow,
				filters,
				schedule.batch_size,
				source="SCHEDULE",
				workflow_version=version_name,
				schedule=schedule.name,
				max_records=schedule.max_records,
				records_per_minute=schedule.records_per_minute,
			)
			schedule.last_backfill_job = result["backfill_id"]
			schedule.last_run_at = now
			created += 1
		if schedule.frequency == "ONCE":
			schedule.enabled = 0
		else:
			schedule.next_run_at = _advance_after_now(
				next_cycle,
				schedule.frequency,
				schedule.timezone,
				now,
				schedule.recurrence_json,
			)
		schedule.save(ignore_permissions=True)
	return created
