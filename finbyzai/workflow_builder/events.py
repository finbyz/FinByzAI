from __future__ import annotations

import json
import time
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime, time_diff_in_seconds
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from . import engine
from .configuration import automation_enabled, int_setting
from .constants import (
	AUTOMATION_PREFIX,
	MAX_RECURSION_DEPTH,
	OUTBOX_DISPATCH_SECONDS,
	OUTBOX_LEASE_SECONDS,
	OUTBOX_MAX_ATTEMPTS,
	RETRY_DELAYS_SECONDS,
)
from .engine import active_policy_dependency_fields, enroll, reevaluate_active_run_policies
from .errors import AutomationError, AutomationTransientError
from .observability import record_enrollment_decision, record_incident
from .registry import configured_blocked_doctypes
from .schema import (
	ABANDONED_CART_DEFAULT_HOURS,
	abandoned_cart_event_matches,
	condition_fields,
	evaluate_expression,
	event_filter_matches,
	event_trigger_entries,
)


DISPATCH_JOB_ID = "automation-outbox-dispatch"
DISPATCH_METHOD = "finbyzai.workflow_builder.events.dispatch_pending_outbox"
_LOGGER_NAME = "automation_runtime"


def signal_business_event(
	event_topic: str,
	payload: dict | None = None,
	*,
	record_doctype: str | None = None,
	record_name: str | None = None,
	source_doctype: str | None = None,
	source_name: str | None = None,
	idempotency_key: str | None = None,
	check_record_permission: bool = False,
) -> dict:
	"""Release event waits and enroll event-triggered workflows.

	This is the trusted service boundary used by installed app adapters. Public
	API callers authorize first and request a record permission check.
	"""
	topic = str(event_topic or "").strip()
	if not topic:
		raise AutomationError(_("An event topic is required."))
	if payload is not None and not isinstance(payload, dict):
		raise AutomationError(_("Event payload must be a JSON object."))
	payload_value = payload or {}
	# Provider adapters already include the authoritative Email Queue ID. Treat
	# that message as an earlier-action source as well as retaining the enrolled
	# record identity used by event-triggered workflows.
	if not source_name:
		source_name = str(
			payload_value.get("email_queue")
			or payload_value.get("email_id")
			or payload_value.get("message_id")
			or ""
		).strip() or None
	if source_name and not source_doctype and payload_value.get("email_queue"):
		source_doctype = "Email Queue"
	record = None
	if record_doctype and record_name:
		record = frappe.get_doc(record_doctype, record_name)
		if check_record_permission:
			record.check_permission("read")
	if check_record_permission and source_doctype and source_name:
		source_record = frappe.get_doc(source_doctype, source_name)
		source_record.check_permission("read")
	release_waiters = topic != "commerce.order.abandoned" or abandoned_cart_event_matches(
		{"abandoned_after_value": ABANDONED_CART_DEFAULT_HOURS, "abandoned_after_unit": "hours"},
		payload_value,
	)
	released = (
		engine.release_event_waiters(
			topic,
			payload_value,
			record_doctype=record_doctype,
			record_name=record_name,
			source_doctype=source_doctype,
			source_name=source_name,
		)
		if release_waiters
		else 0
	)
	if topic == "communication.responded" and record_doctype and record_name:
		engine.apply_response_policy(record_doctype, record_name, payload_value)
	enrolled = []
	if record_doctype and record_name:
		occurrence = str(idempotency_key or payload_value.get("event_id") or frappe.generate_hash(length=32))[:140]
		subscriptions = frappe.get_list(
			"Automation Trigger Subscription",
			filters={"primary_doctype": record_doctype, "event_type": "EVENT", "active": 1},
			fields=["workflow", "workflow_version", "config_json"],
			ignore_permissions=True,
			limit=0,
		)
		for subscription in subscriptions:
			config = _json_object(subscription.config_json)
			entries = event_trigger_entries(config, 2 if isinstance(config.get("events"), list) else 1)
			matched_entry = next(
				(
					entry
					for entry in entries
					if str(entry.get("event_topic") or "").strip() == topic
					and (topic != "commerce.order.abandoned" or abandoned_cart_event_matches(entry, payload_value))
					and event_filter_matches(entry.get("event_filter"), payload_value)
				),
				None,
			)
			if not matched_entry or not evaluate_expression(config.get("condition"), record):
				continue
			workflow_state = frappe.db.get_value(
				"Automation Workflow",
				subscription.workflow,
				["status", "active_version"],
				as_dict=True,
			)
			if (
				not workflow_state
				or workflow_state.status != "ACTIVE"
				or workflow_state.active_version != subscription.workflow_version
			):
				continue
			run_id = engine.enroll(
				subscription.workflow,
				record_doctype,
				record_name,
				source=f"EVENT:{topic}"[:140],
				occurrence_key=f"{topic}:{occurrence}"[:140],
			)
			if run_id:
				enrolled.append({"workflow": subscription.workflow, "run_id": run_id})
	return {"event_topic": topic, "released": released, "enrolled": enrolled}


def _changed_fields(doc, event_type: str) -> list[str]:
	fields = []
	for df in doc.meta.fields:
		if not df.fieldname or df.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML", "Button"}:
			continue
		if event_type == "AFTER_INSERT" or doc.has_value_changed(df.fieldname):
			fields.append(df.fieldname)
	return sorted(set(fields))


def _safe_changed_values(
	doc,
	event_type: str,
	subscriptions: list[dict],
	changed_fields: list[str],
	policy_dependencies: set[str] | None = None,
) -> dict:
	"""Store before/after evidence only for depended-on, non-sensitive scalar fields."""
	dependencies = set()
	for subscription in subscriptions:
		dependencies.update(_json_list(subscription.get("dependency_fields_json")))
	dependencies.update(policy_dependencies or set())
	previous = doc.get_doc_before_save() if event_type == "ON_UPDATE" else None
	safe_types = {"Check", "Int", "Float", "Currency", "Percent", "Date", "Datetime", "Time", "Select"}
	values = {}
	for fieldname in sorted(dependencies.intersection(changed_fields)):
		df = doc.meta.get_field(fieldname)
		if not df or df.fieldtype not in safe_types:
			continue
		values[fieldname] = {"before": previous.get(fieldname) if previous else None, "after": doc.get(fieldname)}
	return values


def capture_after_insert(doc, method=None) -> None:
	_capture(doc, "AFTER_INSERT")
	from .integrations import capture_web_form_submission

	capture_web_form_submission(doc, method)


def capture_on_update(doc, method=None) -> None:
	if frappe.flags.in_insert:
		return
	_capture(doc, "ON_UPDATE")


def _native_wait_sources(topic: str, doctype: str, name: str) -> tuple[bool, bool]:
	"""Return whether an exact enrolled-record or action-output wait exists."""
	base = {
		"status": "ACTIVE",
		"timer_type": ["in", ["TIMEOUT", "EVENT_WAIT"]],
		"event_topic": topic,
	}
	enrolled = bool(
		frappe.db.exists(
			"Automation Timer",
			{**base, "source_type": "ENROLLED_RECORD", "record_doctype": doctype, "record_name": name},
			cache=False,
		)
	)
	action_output = bool(
		frappe.db.exists(
			"Automation Timer",
			{
				**base,
				"source_type": "ACTION_RECORD",
				"source_doctype": doctype,
				"source_name": name,
			},
			cache=False,
		)
	)
	return enrolled, action_output


def _native_wait_occurrences(doc, event_type: str, changed_fields: list[str]) -> list[dict]:
	"""Build durable exact-source occurrences needed by currently active waits."""
	if event_type != "ON_UPDATE" or not changed_fields or not frappe.db.table_exists("Automation Timer"):
		return []
	occurrences = []
	record_sources = _native_wait_sources("record.updated", doc.doctype, doc.name)
	if any(record_sources):
		occurrences.append(
			{
				"topic": "record.updated",
				"enrolled": record_sources[0],
				"action_output": record_sources[1],
				"payload": {
					"event_id": f"record:{doc.doctype}:{doc.name}:updated:{doc.get('modified')}",
					"source_doctype": doc.doctype,
					"source_name": doc.name,
					"occurred_at": str(doc.get("modified") or now_datetime()),
					"changed_fields": changed_fields,
					"status": doc.get("status"),
					"docstatus": cint(doc.get("docstatus")),
				},
			},
		)

	if doc.doctype == "ToDo" and doc.get("status") == "Closed":
		previous = doc.get_doc_before_save()
		if not previous or previous.get("status") != "Closed":
			todo_sources = _native_wait_sources("workflow.todo.completed", doc.doctype, doc.name)
			if todo_sources[1]:
				occurrences.append(
					{
						"topic": "workflow.todo.completed",
						"enrolled": False,
						"action_output": True,
						"payload": {
							"event_id": f"todo:{doc.name}:closed:{doc.get('modified')}",
							"todo": doc.name,
							"occurred_at": str(doc.get("modified") or now_datetime()),
							"allocated_to": doc.get("allocated_to"),
							"status": "Closed",
						},
					},
				)
	return occurrences


def _merge_native_wait_events(existing: list, current: list) -> list[dict]:
	merged = {}
	for occurrence in [*existing, *current]:
		if not isinstance(occurrence, dict):
			continue
		payload = occurrence.get("payload") if isinstance(occurrence.get("payload"), dict) else {}
		key = f"{occurrence.get('topic')}:{payload.get('event_id')}"
		merged[key] = occurrence
	return list(merged.values())[-50:]


def _capture(doc, event_type: str) -> None:
	if (
		frappe.flags.in_install
		or frappe.flags.in_migrate
		or not automation_enabled()
		or doc.doctype.startswith(AUTOMATION_PREFIX)
		or doc.doctype in configured_blocked_doctypes()
		or getattr(doc.flags, "skip_automation", False)
	):
		return
	if doc.meta.istable or doc.meta.issingle or getattr(doc.meta, "is_virtual", False):
		return
	changed_fields = _changed_fields(doc, event_type)
	if event_type == "ON_UPDATE" and not changed_fields:
		return
	native_wait_events = _native_wait_occurrences(doc, event_type, changed_fields)
	subscriptions = _matching_subscriptions(doc.doctype, event_type)
	policy_dependencies = (
		active_policy_dependency_fields(doc.doctype, doc.name) if event_type == "ON_UPDATE" else set()
	)
	if not subscriptions and not policy_dependencies and not native_wait_events:
		return
	context = getattr(frappe.flags, "automation_context", {}) or {}
	recursion_depth = cint(context.get("recursion_depth"))
	if recursion_depth >= int_setting("max_recursion_depth", MAX_RECURSION_DEPTH):
		return
	if event_type == "ON_UPDATE":
		changed = set(changed_fields)
		subscription_relevant = bool(subscriptions) and _subscriptions_need_update(subscriptions, changed)
		policy_relevant = bool(policy_dependencies.intersection(changed))
		if not subscription_relevant and not policy_relevant and not native_wait_events:
			return
	changed_values = _safe_changed_values(
		doc, event_type, subscriptions, changed_fields, policy_dependencies=policy_dependencies
	)

	if event_type == "ON_UPDATE":
		existing = frappe.db.get_value(
			"Automation Outbox Event",
			{
				"object_doctype": doc.doctype,
				"object_name": doc.name,
				"event_type": "ON_UPDATE",
				"status": "PENDING",
			},
			["name", "changed_fields_json", "changed_values_json", "decision_json"],
			as_dict=True,
			for_update=True,
		)
		if existing:
			existing_fields = set(_json_list(existing.changed_fields_json))
			existing_fields.update(changed_fields)

			existing_values = _json_object(existing.changed_values_json)
			for field, val_dict in changed_values.items():
				if field in existing_values:
					existing_values[field]["after"] = val_dict["after"]
				else:
					existing_values[field] = val_dict

			existing_native = _json_object(existing.decision_json).get("native_wait_events") or []
			combined_native = _merge_native_wait_events(existing_native, native_wait_events)
			frappe.db.set_value(
				"Automation Outbox Event",
				existing.name,
				{
					"changed_fields_json": json.dumps(sorted(existing_fields)),
					"changed_values_json": json.dumps(existing_values, default=str),
					"decision_json": json.dumps({"native_wait_events": combined_native}, default=str),
				},
				update_modified=False
			)
			_register_dispatch_wake()
			return

	event_id = frappe.generate_hash(length=32)
	event = frappe.get_doc(
		{
			"doctype": "Automation Outbox Event",
			"event_id": event_id,
			"event_type": event_type,
			"object_doctype": doc.doctype,
			"object_name": doc.name,
			"changed_fields_json": json.dumps(changed_fields),
			"changed_values_json": json.dumps(changed_values, default=str),
			"decision_json": json.dumps({"native_wait_events": native_wait_events}, default=str),
			"status": "PENDING",
			"attempts": 0,
			"available_at": now_datetime(),
			"trace_id": context.get("trace_id") or frappe.generate_hash(length=20),
			"causation_id": context.get("causation_id"),
			"recursion_depth": recursion_depth,
		}
	).insert(ignore_permissions=True)
	_register_dispatch_wake()


def _matching_subscriptions(doctype: str, event_type: str) -> list[dict]:
	event_types = [event_type, "ON_UPDATE"] if event_type == "AFTER_INSERT" else [event_type]
	rows = frappe.get_list(
		"Automation Trigger Subscription",
		filters={"primary_doctype": doctype, "event_type": ["in", event_types], "active": 1},
		fields=["name", "workflow", "workflow_version", "event_type", "config_json", "dependency_fields_json"],
		ignore_permissions=True,
		limit=0,
	)
	if event_type != "AFTER_INSERT":
		return rows
	return [
		row
		for row in rows
		if row.event_type == "AFTER_INSERT"
		or _json_object(row.config_json).get("_trigger_type") == "trigger.filter_criteria"
	]


def _json_list(value: str | None) -> list:
	try:
		parsed = json.loads(value or "[]")
	except (TypeError, ValueError):
		return []
	return parsed if isinstance(parsed, list) else []


def _json_object(value: str | None) -> dict:
	try:
		parsed = json.loads(value or "{}")
	except (TypeError, ValueError):
		return {}
	return parsed if isinstance(parsed, dict) else {}


def _subscriptions_need_update(subscriptions: list[dict], changed_fields: set[str]) -> bool:
	for subscription in subscriptions:
		dependencies = set(_json_list(subscription.get("dependency_fields_json")))
		if not dependencies or dependencies.intersection(changed_fields):
			return True
	return False


def _register_dispatch_wake() -> None:
	"""Wake once after commit without allowing Redis pressure to break the source transaction."""
	if getattr(frappe.flags, "automation_dispatch_wake_registered", False):
		return
	frappe.flags.automation_dispatch_wake_registered = True
	frappe.db.after_commit.add(_enqueue_dispatcher_safely)


def _enqueue_dispatcher_safely() -> bool:
	try:
		site = str(getattr(frappe.local, "site", "site")).replace(".", "-")
		frappe.enqueue(
			DISPATCH_METHOD,
			queue="default",
			job_id=f"{site}-{DISPATCH_JOB_ID}",
			deduplicate=True,
		)
		return True
	except (frappe.QueueOverloaded, RedisConnectionError, RedisTimeoutError, ConnectionError, TimeoutError) as exc:
		# The database outbox is authoritative; the minute scheduler will recover it.
		frappe.logger(_LOGGER_NAME, allow_site=True).warning("Outbox wake deferred: %s", exc)
		return False


def process_outbox_event(event_name: str) -> int:
	"""Compatibility entry point for jobs queued by the pre-batch implementation."""
	if not automation_enabled() or not frappe.db.table_exists("Automation Outbox Event"):
		return 0
	lease_owner = _lease_owner()
	row = _claim_event(event_name=event_name, lease_owner=lease_owner)
	if not row:
		return 0
	return _run_claimed_event(row, lease_owner)


def process_claimed_outbox_event(event_name: str, lease_owner: str) -> int:
	"""Process a lease persisted by the scheduler's Frappe-managed transaction."""
	if not automation_enabled() or not frappe.db.table_exists("Automation Outbox Event"):
		return 0
	row = frappe.db.get_value(
		"Automation Outbox Event",
		{"name": event_name, "status": "PROCESSING", "lease_owner": lease_owner},
		["name", "attempts"],
		as_dict=True,
		for_update=True,
	)
	if not row:
		return 0
	return _run_claimed_event(row, lease_owner)


def _process_event(event) -> int:
	try:
		record = frappe.get_doc(event.object_doctype, event.object_name)
	except frappe.DoesNotExistError:
		_complete_event(event.name, error_message="Source document no longer exists")
		return 0
	if event.event_type == "WEBHOOK":
		target = _json_object(event.decision_json).get("webhook") or {}
		definition = frappe.db.get_value(
			"Automation Inbound Webhook",
			target.get("definition"),
			["enabled", "workflow", "workflow_version"],
			as_dict=True,
		)
		workflow_state = frappe.db.get_value(
			"Automation Workflow",
			target.get("workflow"),
			["status", "active_version"],
			as_dict=True,
		)
		if (
			not definition
			or not cint(definition.enabled)
			or definition.workflow != target.get("workflow")
			or definition.workflow_version != target.get("workflow_version")
			or not workflow_state
			or workflow_state.status != "ACTIVE"
			or workflow_state.active_version != target.get("workflow_version")
		):
			_complete_event(event.name, decisions=[{"decision": "SKIPPED", "reason": "INACTIVE_WEBHOOK_VERSION"}])
			return 0
		run_name = enroll(
			target["workflow"],
			record.doctype,
			record.name,
			source="WEBHOOK",
			occurrence_key=event.event_id,
			workflow_version=target["workflow_version"],
			require_active_version=True,
			causation_id=event.trace_id,
			recursion_depth=cint(event.recursion_depth),
		)
		_complete_event(event.name, decisions=[{"workflow": target["workflow"], "decision": "ENROLLED" if run_name else "SKIPPED", "run": run_name}])
		return int(bool(run_name))
	changed = set(_json_list(event.changed_fields_json))
	subscriptions = _matching_subscriptions(event.object_doctype, event.event_type)
	enrolled = 0
	native_occurrences = _json_object(event.decision_json).get("native_wait_events") or []
	native_decisions = []
	for occurrence in native_occurrences:
		if not isinstance(occurrence, dict):
			continue
		topic = str(occurrence.get("topic") or "").strip()
		payload = occurrence.get("payload") if isinstance(occurrence.get("payload"), dict) else {}
		if not topic:
			continue
		released = engine.release_event_waiters(
			topic,
			payload,
			record_doctype=event.object_doctype if occurrence.get("enrolled") else None,
			record_name=event.object_name if occurrence.get("enrolled") else None,
			source_doctype=event.object_doctype if occurrence.get("action_output") else None,
			source_name=event.object_name if occurrence.get("action_output") else None,
		)
		native_decisions.append(
			{"kind": "WAIT_EVENT", "event_topic": topic, "event_id": payload.get("event_id"), "released": released}
		)
	policy_results = reevaluate_active_run_policies(
		outbox_event=event.name,
		event_id=event.event_id,
		record_doctype=event.object_doctype,
		record_name=event.object_name,
		changed_fields=changed,
	)
	decisions = [*native_decisions, *({"kind": "RUN_POLICY", **row} for row in policy_results)]
	for subscription in subscriptions:
		workflow_state = frappe.db.get_value(
			"Automation Workflow",
			subscription.workflow,
			["status", "active_version"],
			as_dict=True,
		)
		if (
			not workflow_state
			or workflow_state.status != "ACTIVE"
			or workflow_state.active_version != subscription.workflow_version
		):
			decisions.append(
				{"workflow": subscription.workflow, "decision": "SKIPPED", "reason": "STALE_SUBSCRIPTION"}
			)
			record_enrollment_decision(
				workflow=subscription.workflow,
				workflow_version=subscription.workflow_version,
				record_doctype=event.object_doctype,
				record_name=event.object_name,
				source=event.event_type,
				occurrence_key=event.event_id,
				decision="REJECTED",
				reason_code="STALE_SUBSCRIPTION",
				evidence={"active_version": workflow_state.active_version if workflow_state else None},
				trace_id=event.trace_id,
			)
			continue
		dependencies = set(_json_list(subscription.dependency_fields_json))
		if event.event_type == "ON_UPDATE" and dependencies and not dependencies.intersection(changed):
			decisions.append({"workflow": subscription.workflow, "decision": "SKIPPED", "reason": "IRRELEVANT_FIELDS"})
			record_enrollment_decision(
				workflow=subscription.workflow, workflow_version=subscription.workflow_version,
				record_doctype=event.object_doctype, record_name=event.object_name, source=event.event_type,
				occurrence_key=event.event_id, decision="REJECTED", reason_code="IRRELEVANT_FIELDS",
				evidence={"changed_fields": sorted(changed), "dependency_fields": sorted(dependencies)}, trace_id=event.trace_id,
			)
			continue
		config = _json_object(subscription.config_json)
		watch_fields = {str(field) for field in config.get("watch_fields") or [] if field}
		if event.event_type == "ON_UPDATE" and watch_fields and not watch_fields.intersection(changed):
			decisions.append({"workflow": subscription.workflow, "decision": "SKIPPED", "reason": "WATCHED_FIELDS_UNCHANGED"})
			record_enrollment_decision(
				workflow=subscription.workflow, workflow_version=subscription.workflow_version,
				record_doctype=event.object_doctype, record_name=event.object_name, source=event.event_type,
				occurrence_key=event.event_id, decision="REJECTED", reason_code="WATCHED_FIELDS_UNCHANGED",
				evidence={"changed_fields": sorted(changed), "watch_fields": sorted(watch_fields)}, trace_id=event.trace_id,
			)
			continue
		if not evaluate_expression(config.get("condition"), record):
			decisions.append({"workflow": subscription.workflow, "decision": "SKIPPED", "reason": "CONDITION_FALSE"})
			record_enrollment_decision(
				workflow=subscription.workflow, workflow_version=subscription.workflow_version,
				record_doctype=event.object_doctype, record_name=event.object_name, source=event.event_type,
				occurrence_key=event.event_id, decision="REJECTED", reason_code="TRIGGER_CONDITION_FALSE",
				evidence={"condition_fields": sorted(condition_fields(config.get("condition"))), "changed_fields": sorted(changed)}, trace_id=event.trace_id,
			)
			continue
		run_name = enroll(
			subscription.workflow,
			event.object_doctype,
			event.object_name,
			source=event.event_type,
			occurrence_key=event.event_id,
			workflow_version=subscription.workflow_version,
			require_active_version=True,
			causation_id=event.causation_id or event.trace_id,
			recursion_depth=cint(event.recursion_depth),
		)
		enrolled += bool(run_name)
		decisions.append({"workflow": subscription.workflow, "decision": "ENROLLED" if run_name else "SKIPPED", "reason": None if run_name else "DEDUPLICATION_OR_POLICY", "run": run_name})
	_complete_event(event.name, decisions=decisions)
	return enrolled


def dispatch_pending_outbox(event_names: list[str] | None = None) -> int:
	if not automation_enabled() or not frappe.db.table_exists("Automation Outbox Event"):
		return 0
	if event_names is not None and not event_names:
		return 0
	_recover_expired_leases(event_names=event_names)
	batch_size = min(max(int_setting("outbox_batch_size", 100), 1), 500)
	lease_owner = f"dispatch-{frappe.generate_hash(length=20)}"
	started = time.monotonic()
	queued = 0
	while queued < batch_size and time.monotonic() - started < OUTBOX_DISPATCH_SECONDS:
		row = _claim_event(lease_owner=lease_owner, event_names=event_names)
		if not row:
			break
		frappe.enqueue(
			"finbyzai.workflow_builder.events.process_claimed_outbox_event",
			event_name=row.name,
			lease_owner=lease_owner,
			queue="default",
			enqueue_after_commit=True,
			job_id=f"automation-outbox-{row.name}",
			deduplicate=True,
		)
		queued += 1
	return queued


def _lease_owner() -> str:
	try:
		from rq import get_current_job

		if job := get_current_job():
			return str(job.id)[-140:]
	except Exception:
		pass
	return f"sync-{frappe.generate_hash(length=20)}"


def _recover_expired_leases(*, event_names: list[str] | None = None) -> int:
	now = now_datetime()
	filters = {"status": "PROCESSING", "lease_until": ["<=", now]}
	if event_names is not None:
		if not event_names:
			return 0
		filters["name"] = ["in", event_names]
	names = frappe.db.get_values(
		"Automation Outbox Event",
		filters=filters,
		fieldname="name",
		pluck=True,
		limit=500,
	)
	recovered = 0
	for name in names:
		doc = frappe.get_doc("Automation Outbox Event", name, for_update=True)
		if doc.status != "PROCESSING" or not doc.lease_until or doc.lease_until > now_datetime():
			continue
		doc.status = "FAILED"
		doc.available_at = now_datetime()
		doc.lease_owner = None
		doc.lease_until = None
		doc.error_code = "LEASE_EXPIRED"
		doc.error_message = "Worker lease expired; event recovered for retry."
		doc.save(ignore_permissions=True)
		recovered += 1
	return recovered


def _claim_event(*, lease_owner: str, event_name: str | None = None, event_names: list[str] | None = None):
	filters = {
		"status": ["in", ["PENDING", "FAILED"]],
		"available_at": ["<=", now_datetime()],
	}
	if event_name:
		filters["name"] = event_name
	elif event_names is not None:
		if not event_names:
			return None
		filters["name"] = ["in", event_names]
	rows = frappe.db.get_values(
		"Automation Outbox Event",
		filters=filters,
		fieldname=["name", "attempts"],
		as_dict=True,
		order_by="creation asc",
		limit=1,
		for_update=True,
		skip_locked=True,
	)
	if not rows:
		return None
	row = rows[0]
	attempts = cint(row.attempts) + 1
	now = now_datetime()
	frappe.db.set_value(
		"Automation Outbox Event",
		row.name,
		{
			"status": "PROCESSING",
			"attempts": attempts,
			"last_attempt_at": now,
			"lease_owner": lease_owner,
			"lease_until": now + timedelta(seconds=OUTBOX_LEASE_SECONDS),
			"error_code": None,
			"error_message": None,
		},
		update_modified=False,
	)
	row.attempts = attempts
	return row


def _run_claimed_event(row, lease_owner: str) -> int:
	try:
		event = frappe.get_doc("Automation Outbox Event", row.name)
		if event.status != "PROCESSING" or event.lease_owner != lease_owner:
			return 0
		return _process_event(event)
	except Exception as exc:
		# Clear all business writes and enqueue-after-commit callbacks from this event.
		# Frappe's job wrapper commits the failure state when this function returns.
		frappe.db.rollback()
		_fail_event(row.name, cint(row.attempts), exc)
		return 0


def _complete_event(event_name: str, *, error_message: str | None = None, decisions: list[dict] | None = None) -> None:
	frappe.db.set_value(
		"Automation Outbox Event",
		event_name,
		{
			"status": "PROCESSED",
			"processed_at": now_datetime(),
			"lease_owner": None,
			"lease_until": None,
			"error_code": None,
			"error_message": error_message,
			"decision_json": json.dumps(decisions or [], default=str),
		},
		update_modified=False,
	)


def _is_transient(exc: Exception) -> bool:
	if isinstance(exc, AutomationTransientError):
		return True
	if isinstance(
		exc,
		(
			frappe.QueryDeadlockError,
			frappe.QueryTimeoutError,
			RedisConnectionError,
			RedisTimeoutError,
			ConnectionError,
			TimeoutError,
		),
	):
		return True
	try:
		return bool(frappe.db.is_deadlocked(exc) or frappe.db.is_timedout(exc))
	except Exception:
		return False


def _fail_event(event_name: str, attempts: int, exc: Exception) -> None:
	transient = _is_transient(exc)
	can_retry = transient and attempts < OUTBOX_MAX_ATTEMPTS
	status = "FAILED" if can_retry else "DEAD"
	delay = RETRY_DELAYS_SECONDS[min(max(attempts - 1, 0), len(RETRY_DELAYS_SECONDS) - 1)] if can_retry else 0
	error_code = getattr(exc, "code", None) or type(exc).__name__
	message = str(exc).replace("\n", " ")[:500] or type(exc).__name__
	frappe.db.set_value(
		"Automation Outbox Event",
		event_name,
		{
			"status": status,
			"available_at": now_datetime() + timedelta(seconds=delay),
			"processed_at": now_datetime() if status == "DEAD" else None,
			"lease_owner": None,
			"lease_until": None,
			"error_code": str(error_code)[:140],
			"error_message": message,
		},
		update_modified=False,
	)
	frappe.logger(_LOGGER_NAME, allow_site=True).error(
		"Outbox event %s failed (%s, attempt %s): %s", event_name, status, attempts, message
	)
	if status == "DEAD":
		record_incident(
			source_type="OUTBOX", source_name=event_name, error_code=str(error_code),
			message=message, attempts=attempts,
		)


def retry_outbox_event(event_name: str) -> dict:
	row = frappe.db.get_value(
		"Automation Outbox Event", event_name, ["name", "status"], as_dict=True, for_update=True
	)
	if not row:
		raise frappe.DoesNotExistError
	if row.status not in {"FAILED", "DEAD"}:
		raise AutomationError("Only failed or dead-lettered events can be retried.")
	frappe.db.set_value(
		"Automation Outbox Event",
		event_name,
		{
			"status": "PENDING",
			"attempts": 0,
			"available_at": now_datetime(),
			"processed_at": None,
			"lease_owner": None,
			"lease_until": None,
			"error_code": None,
			"error_message": None,
		},
		update_modified=False,
	)
	if frappe.db.table_exists("Automation Dead Letter"):
		frappe.db.set_value(
			"Automation Dead Letter", {"source_type": "OUTBOX", "source_name": event_name},
			{"status": "RESOLVED", "resolved_at": now_datetime(), "resolution": "Outbox event requeued by operator."},
			update_modified=False,
		)
	_register_dispatch_wake()
	return {"event_id": event_name, "status": "PENDING"}


def discard_outbox_event(event_name: str) -> dict:
	row = frappe.db.get_value(
		"Automation Outbox Event", event_name, ["name", "status"], as_dict=True, for_update=True
	)
	if not row:
		raise frappe.DoesNotExistError
	if row.status in {"PROCESSING", "PROCESSED"}:
		raise AutomationError("Processing or processed events cannot be discarded.")
	frappe.db.set_value(
		"Automation Outbox Event",
		event_name,
		{
			"status": "DEAD",
			"processed_at": now_datetime(),
			"lease_owner": None,
			"lease_until": None,
			"error_code": "MANUAL_DISCARD",
			"error_message": "Discarded by an Automation Operator.",
		},
		update_modified=False,
	)
	return {"event_id": event_name, "status": "DEAD"}


def list_outbox_events(
	*, status: str | None = None, search: str | None = None, start: int = 0, page_length: int = 50
) -> dict:
	filters = {}
	if status and str(status).upper() != "ALL":
		filters["status"] = str(status).upper()
	or_filters = None
	if search:
		like = f"%{str(search).strip()}%"
		or_filters = {
			"name": ["like", like],
			"event_id": ["like", like],
			"object_name": ["like", like],
			"error_code": ["like", like],
		}
	page_length = min(max(cint(page_length), 1), 100)
	rows = frappe.get_list(
		"Automation Outbox Event",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name", "event_id", "event_type", "object_doctype", "object_name", "changed_fields_json", "changed_values_json", "decision_json", "status", "attempts",
			"available_at", "last_attempt_at", "processed_at", "error_code", "error_message", "trace_id", "creation",
		],
		order_by="creation desc",
		start=max(cint(start), 0),
		limit=page_length + 1,
	)
	return {"rows": rows[:page_length], "has_more": len(rows) > page_length}


def bulk_retry_outbox_events(event_names: list[str]) -> dict:
	names = list(dict.fromkeys(str(name) for name in event_names if name))[:100]
	retried = []
	for name in names:
		status = frappe.db.get_value("Automation Outbox Event", name, "status")
		if status in {"FAILED", "DEAD"}:
			retry_outbox_event(name)
			retried.append(name)
	return {"retried": retried, "count": len(retried)}


def operation_snapshot() -> dict:
	return {
		"health": runtime_health(),
		"failed_attempts": frappe.get_list(
			"Automation Action Attempt",
			filters={"status": ["in", ["FAILED", "UNKNOWN_COMMIT"]]},
			fields=["name", "run", "token", "node_id", "attempt_no", "status", "error_code", "error_message", "started_at", "completed_at"],
			order_by="creation desc",
			limit=25,
		),
		"due_timers": frappe.db.count("Automation Timer", {"status": "ACTIVE", "due_at": ["<=", now_datetime()]}),
		"ready_tokens": frappe.db.count("Automation Run Token", {"status": "READY", "available_at": ["<=", now_datetime()]}),
		"policy_evaluations": _policy_evaluation_snapshot(),
	}


def _policy_evaluation_snapshot() -> dict:
	counts = {"NO_CHANGE": 0, "GOAL_MET": 0, "ELIGIBILITY_LOST": 0}
	if not frappe.db.table_exists("Automation Policy Evaluation"):
		return {"counts": counts, "recent": []}
	for row in frappe.get_list(
		"Automation Policy Evaluation",
		fields=["outcome", {"COUNT": "name", "as": "count"}],
		group_by="outcome",
		ignore_permissions=True,
		limit=0,
	):
		if row.outcome in counts:
			counts[row.outcome] = cint(row.count)
	return {
		"counts": counts,
		"recent": frappe.get_list(
			"Automation Policy Evaluation",
			fields=[
				"name",
				"workflow",
				"run",
				"record_doctype",
				"record_name",
				"outcome",
				"reason_code",
				"changed_fields_json",
				"evaluated_at",
			],
			order_by="creation desc",
			limit=10,
		),
	}


def runtime_health(workflow_id: str | None = None) -> dict:
	"""Return actionable runtime health, optionally scoped to one workflow.

	The outbox and Redis queue are shared infrastructure and therefore remain
	global. Runs, incidents, and dead letters are scoped when a workflow is passed.
	Historical terminal rows stay observable without keeping health red forever:
	only failures inside the configured window are health-significant.
	"""
	counts = {"PENDING": 0, "PROCESSING": 0, "PROCESSED": 0, "FAILED": 0, "DEAD": 0}
	for row in frappe.get_list(
		"Automation Outbox Event",
		fields=["status", {"COUNT": "name", "as": "count"}],
		group_by="status",
		ignore_permissions=True,
		limit=0,
	):
		counts[row.status] = cint(row.count)
	quarantined = frappe.db.count(
		"Automation Outbox Event", {"status": "DEAD", "error_code": "UNSAFE_INTERNAL_SOURCE"}
	)
	counts["DEAD"] = max(counts["DEAD"] - quarantined, 0)
	oldest = frappe.db.get_value(
		"Automation Outbox Event",
		{"status": ["in", ["PENDING", "FAILED"]]},
		"creation",
		order_by="creation asc",
	)
	queue_count = None
	dispatcher_status = None
	queue_available = True
	try:
		from frappe.utils.background_jobs import get_job_status, get_queue

		queue_count = get_queue("default").count
		status = get_job_status(DISPATCH_JOB_ID)
		dispatcher_status = status.value if status else None
	except (RedisConnectionError, RedisTimeoutError, ConnectionError, TimeoutError):
		queue_available = False
	active_subscriptions = frappe.db.count("Automation Trigger Subscription", {"active": 1})
	oldest_age = max(cint(time_diff_in_seconds(now_datetime(), oldest)), 0) if oldest else 0

	workflow_filter = {"workflow": workflow_id} if workflow_id else {}
	failure_window_hours = min(max(int_setting("health_failure_window_hours", 24), 1), 24 * 30)
	stale_after_minutes = min(max(int_setting("alert_queue_age_minutes", 15), 1), 24 * 60)
	recent_failed = frappe.db.count(
		"Automation Run",
		{
			**workflow_filter,
			"status": "FAILED",
			"modified": [">=", add_to_date(now_datetime(), hours=-failure_window_hours)],
		},
	)
	active_rows = frappe.get_all(
		"Automation Run",
		filters={**workflow_filter, "status": ["in", ["QUEUED", "RUNNING"]]},
		fields=["name", "workflow", "workflow_version", "modified"],
		limit_page_length=0,
	)
	stale_cutoff = add_to_date(now_datetime(), minutes=-stale_after_minutes)
	stale_active = sum(1 for row in active_rows if row.modified and row.modified <= stale_cutoff)
	stale_external_effects = frappe.db.count(
		"Automation Effect Ledger",
		{"status": ["in", ["PROCESSING", "STARTED"]], "modified": ["<=", stale_cutoff]},
	)
	orphaned_active = sum(
		1
		for row in active_rows
		if not frappe.db.exists("Automation Workflow", row.workflow)
		or not frappe.db.exists("Automation Workflow Version", row.workflow_version)
	)
	incident_filters = {**workflow_filter, "status": "OPEN"}
	dead_letter_filters = {**workflow_filter, "status": ["in", ["OPEN", "RETRYING"]]}
	open_incidents = frappe.db.count("Automation Incident", incident_filters)
	open_dead_letters = frappe.db.count("Automation Dead Letter", dead_letter_filters)
	queue_age_limit = stale_after_minutes * 60
	reasons = []
	if not queue_available:
		reasons.append("QUEUE_UNAVAILABLE")
	if counts["FAILED"]:
		reasons.append("OUTBOX_RETRYING")
	if counts["DEAD"]:
		reasons.append("OUTBOX_DEAD")
	if oldest_age >= queue_age_limit:
		reasons.append("OUTBOX_STALE")
	if recent_failed:
		reasons.append("RECENT_FAILED_RUNS")
	if stale_active:
		reasons.append("STALE_ACTIVE_RUNS")
	if stale_external_effects:
		reasons.append("STALE_EXTERNAL_EFFECTS")
	if orphaned_active:
		reasons.append("ORPHANED_ACTIVE_RUNS")
	if open_incidents:
		reasons.append("OPEN_INCIDENTS")
	if open_dead_letters:
		reasons.append("OPEN_DEAD_LETTERS")
	return {
		"enabled": automation_enabled(),
		"workflow": workflow_id,
		"active_subscriptions": active_subscriptions,
		"outbox": counts,
		"quarantined": quarantined,
		"oldest_ready_age_seconds": oldest_age,
		"default_queue_count": queue_count,
		"dispatcher_status": dispatcher_status,
		"queue_available": queue_available,
		"runs": {
			"active": len(active_rows),
			"recent_failed": recent_failed,
			"stale_active": stale_active,
			"orphaned_active": orphaned_active,
			"failure_window_hours": failure_window_hours,
		},
		"stale_external_effects": stale_external_effects,
		"open_incidents": open_incidents,
		"open_dead_letters": open_dead_letters,
		"reasons": reasons,
		"healthy": not reasons,
	}
