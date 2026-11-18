"""WF-08: test run of a workflow on one record.

Messages really go out, but only to the tester: email, SMS, Instagram DM and the in-app notification
use the tester's own address, number or user, and carry a [TEST] marker. Asana tasks are really
created. Changes to records are simulated, and waits are skipped (the log says until when they would
last). Every step of the log says whether it was really sent, simulated or skipped.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from . import engine
from .principal import preview_principal

REAL = "REALLY_SENT"
SIMULATED = "SIMULATED"
SKIPPED = "SKIPPED"
FAILED = "FAILED"

WAIT_NODES = {"delay.fixed", "delay.drip", "delay.until_date", "delay.until_event"}
DELIVERY_NODES = {"action.send_email", "action.send_sms", "action.instagram_message", "action.notify_user", "action.asana"}
NOT_SENT_NODES = {"action.webhook"}


def _outcome(entry: dict, mode: str, note: str | None = None, **extra) -> dict:
	entry["test_outcome"] = mode
	if note:
		entry["test_note"] = note
	entry.update(extra)
	return entry


def _wait_note(entry: dict) -> str:
	output = entry.get("output") or {}
	if output.get("due_at"):
		return _("Wait skipped. It would last until {0}.").format(output["due_at"])
	if output.get("interval_seconds"):
		return _("Wait skipped. It would release a batch every {0} seconds.").format(output["interval_seconds"])
	return _("Wait skipped. In a live run the record waits here for the event.")


def _really_send(entry: dict, node: dict, run, record, outputs: dict, tester: dict, settings: dict) -> dict:
	from . import external

	config = dict(node.get("config") or {})
	node_type = node["type"]
	try:
		if node_type == "action.send_email":
			address = tester.get("email")
			if not address:
				return _outcome(entry, SKIPPED, _("No test email address was given, so the email was not sent."))
			result = external.queue_email(run, config, record=record, outputs=outputs, workflow_settings=settings, test_recipient=address)
			entry["output"] = result
			if result.get("suppressed"):
				return _outcome(entry, SKIPPED, _("Not sent: {0}").format(result.get("suppression_reason")))
			return _outcome(entry, REAL, _("Email queued to {0} with [TEST] in the subject.").format(address))
		if node_type == "action.send_sms":
			phone = tester.get("phone")
			if not phone:
				return _outcome(entry, SKIPPED, _("No test phone number was given, so the SMS was not sent."))
			config.update(recipient=phone, require_consent=0, message="[TEST] " + str(config.get("message") or ""))
			entry["output"] = external.send_frappe_sms(run, config, record=record, outputs=outputs, workflow_settings=settings)
			return _outcome(entry, REAL, _("SMS sent to {0}.").format(phone))
		if node_type == "action.instagram_message":
			target = tester.get("instagram_id")
			if not target:
				return _outcome(entry, SKIPPED, _("No test Instagram ID was given, so the message was not sent."))
			config.update(recipient_id=target, message="[TEST] " + str(config.get("message") or ""))
			entry["output"] = external.send_instagram_message(run, config, record=record, outputs=outputs, effect_key=f"test:{run.name}:{node['id']}")
			return _outcome(entry, REAL, _("Direct message sent to the test account."))
		if node_type == "action.notify_user":
			user = tester.get("user")
			sent = engine.enqueue_notification_for_user(user, {
				"type": "Alert",
				"subject": "[TEST] " + str(config.get("subject") or _("Workflow notification")),
				"email_content": str(config.get("message") or ""),
				"document_type": record.doctype,
				"document_name": record.name,
				"from_user": user,
			})
			entry["output"] = {"recipients": [user] if sent else [], "recipient_count": 1 if sent else 0}
			if not sent:
				return _outcome(entry, SKIPPED, _("The tester has no enabled account or email address, so no notification was sent."))
			return _outcome(entry, REAL, _("Notification sent to you only."))
		if node_type == "action.asana":
			payload = config.get("payload")
			if isinstance(payload, dict) and payload.get("name"):
				config["payload"] = {**payload, "name": "[TEST] " + str(payload["name"])}
			entry["output"] = external.execute_asana(config, record=record, outputs=outputs)
			return _outcome(entry, REAL, _("Asana task or project really created. Delete it in Asana when done."))
	except Exception as exc:
		entry["status"] = "FAILED"
		return _outcome(entry, FAILED, str(exc)[:500])
	return _outcome(entry, SKIPPED)


def run_test(graph: dict, record, *, execution_user: str, tester: dict, settings: dict | None = None) -> dict:
	"""Walk the graph for one record, sending real messages to the tester only."""
	result = engine.simulate_graph(graph, record, execution_user=execution_user)
	nodes = engine._node_map(graph)
	run = frappe._dict(
		name="TEST", record_doctype=record.doctype, record_name=record.name,
		record_key=f"{record.doctype}:{record.name}", trace_id="test", causation_id="test", recursion_depth=0,
	)
	outputs: dict = {}
	with preview_principal(execution_user, {"trace_id": "test", "causation_id": "test", "recursion_depth": 1}):
		for entry in result["path"]:
			node = nodes[entry["node_id"]]
			node_type = node["type"]
			if node_type in DELIVERY_NODES:
				_really_send(entry, node, run, record, outputs, tester, settings or {})
			elif node_type in WAIT_NODES:
				_outcome(entry, SKIPPED, _wait_note(entry))
			elif node_type in NOT_SENT_NODES:
				_outcome(entry, SKIPPED, _("Webhooks are not called in a test."))
			elif node_type.startswith("condition."):
				_outcome(entry, SIMULATED, _("Evaluated on this record."))
			else:
				_outcome(entry, SIMULATED, _("Record changes are only simulated in a test."))
			outputs[entry["node_id"]] = entry.get("output") or {}
	counts = {mode: sum(1 for entry in result["path"] if entry.get("test_outcome") == mode) for mode in (REAL, SIMULATED, SKIPPED, FAILED)}
	return {**result, "test_mode": True, "summary": counts}
