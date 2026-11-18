"""WF-08: a test run really sends to the tester only, simulates record changes and skips waits."""
from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import patch

import frappe

from finbyzai.workflow_builder import engine, external, test_mode


@contextmanager
def _principal(*args, **kwargs):
	yield


def _entry(node_id, node_type, status="EVALUATED", output=None):
	return {"node_id": node_id, "type": node_type, "status": status, "output": output or {}}


GRAPH = {"nodes": [
	{"id": "mail", "type": "action.send_email", "config": {"recipient": "customer@example.com"}},
	{"id": "sms", "type": "action.send_sms", "config": {"recipient": "+41790000000", "message": "Hello"}},
	{"id": "wait", "type": "delay.fixed", "config": {}},
	{"id": "set", "type": "action.update_record", "config": {}},
	{"id": "hook", "type": "action.webhook", "config": {}},
	{"id": "asana", "type": "action.asana", "config": {"payload": {"name": "Call back"}}},
]}
PATH = [
	_entry("mail", "action.send_email"), _entry("sms", "action.send_sms"),
	_entry("wait", "delay.fixed", "PREDICTED", {"due_at": "2026-11-20 09:00:00"}),
	_entry("set", "action.update_record", "PREDICTED"), _entry("hook", "action.webhook"), _entry("asana", "action.asana"),
]


class TestModeTest(TestCase):
	def _run(self, tester, **patches):
		record = frappe._dict(doctype="Lead", name="LEAD-1")
		with patch.object(engine, "simulate_graph", return_value={"path": [dict(e, output=dict(e["output"])) for e in PATH], "completed": True}), \
				patch.object(test_mode, "preview_principal", _principal), \
				patch.object(external, "queue_email", return_value={"email_queue": "Q-1", "suppressed": False}) as mail, \
				patch.object(external, "send_frappe_sms", return_value={"status": "SENT"}) as sms, \
				patch.object(external, "execute_asana", return_value={"gid": "1"}) as asana:
			result = test_mode.run_test(GRAPH, record, execution_user="Administrator", tester=tester)
		return {e["node_id"]: e for e in result["path"]}, result, mail, sms, asana

	def test_messages_go_only_to_the_tester_and_each_step_says_what_happened(self):
		steps, result, mail, sms, asana = self._run({"email": "me@example.com", "phone": "+41791111111", "user": "me@example.com"})
		self.assertEqual(mail.call_args.kwargs["test_recipient"], "me@example.com")
		self.assertEqual(sms.call_args.args[1]["recipient"], "+41791111111")
		self.assertTrue(sms.call_args.args[1]["message"].startswith("[TEST]"))
		self.assertEqual(asana.call_args.args[0]["payload"]["name"], "[TEST] Call back")
		self.assertEqual([steps[i]["test_outcome"] for i in ("mail", "sms", "wait", "set", "hook", "asana")],
			[test_mode.REAL, test_mode.REAL, test_mode.SKIPPED, test_mode.SIMULATED, test_mode.SKIPPED, test_mode.REAL])
		self.assertIn("2026-11-20 09:00:00", steps["wait"]["test_note"])
		self.assertEqual(result["summary"], {test_mode.REAL: 3, test_mode.SIMULATED: 1, test_mode.SKIPPED: 2, test_mode.FAILED: 0})

	def test_without_a_test_address_or_number_nothing_is_sent(self):
		steps, _result, mail, sms, _asana = self._run({"user": "me@example.com"})
		mail.assert_not_called(); sms.assert_not_called()
		self.assertEqual(steps["mail"]["test_outcome"], test_mode.SKIPPED)
		self.assertEqual(steps["sms"]["test_outcome"], test_mode.SKIPPED)

	def test_a_suppressed_test_address_is_reported_not_mailed(self):
		record = frappe._dict(doctype="Lead", name="LEAD-1")
		with patch.object(engine, "simulate_graph", return_value={"path": [_entry("mail", "action.send_email")], "completed": True}), \
				patch.object(test_mode, "preview_principal", _principal), \
				patch.object(external, "queue_email", return_value={"suppressed": True, "suppression_reason": "Hard bounce"}):
			result = test_mode.run_test(GRAPH, record, execution_user="Administrator", tester={"email": "me@example.com"})
		self.assertEqual(result["path"][0]["test_outcome"], test_mode.SKIPPED)
		self.assertIn("Hard bounce", result["path"][0]["test_note"])

	def test_a_failing_step_is_logged_and_the_run_goes_on(self):
		record = frappe._dict(doctype="Lead", name="LEAD-1")
		with patch.object(engine, "simulate_graph", return_value={"path": PATH[:2], "completed": True}), \
				patch.object(test_mode, "preview_principal", _principal), \
				patch.object(external, "queue_email", side_effect=Exception("No outgoing account")), \
				patch.object(external, "send_frappe_sms", return_value={"status": "SENT"}):
			result = test_mode.run_test(GRAPH, record, execution_user="Administrator", tester={"email": "me@example.com", "phone": "+41791111111"})
		self.assertEqual([e["test_outcome"] for e in result["path"]], [test_mode.FAILED, test_mode.REAL])
