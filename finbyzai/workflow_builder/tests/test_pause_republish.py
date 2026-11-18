"""WF-06 / WF-07: editing pauses an active workflow; records continue on the new version."""
import json
from unittest import TestCase
from unittest.mock import patch

import frappe

from finbyzai.workflow_builder import api, authoring, engine


def version(*nodes):
	return frappe._dict(graph_json=json.dumps({"start_node_id": "t", "nodes": [{"id": i, "type": t} for i, t in nodes]}))


OLD = version(("t", "trigger.manual"), ("mail", "action.send_email"), ("wait", "delay.fixed"))
NEW_COMPATIBLE = version(("t", "trigger.manual"), ("mail", "action.send_email"), ("wait", "delay.fixed"), ("extra", "action.add_note"))
NEW_WITHOUT_WAIT = version(("t", "trigger.manual"), ("mail", "action.send_email"))


class MoveRunsTest(TestCase):
	def _move(self, new_version_doc, token_nodes):
		docs = {"V-OLD": OLD, "V-NEW": new_version_doc}
		runs = [frappe._dict(name="RUN-1", workflow_version="V-OLD", record_doctype="Lead", record_name="LEAD-1")]
		def get_all(doctype, **kwargs):
			return runs if doctype == "Automation Run" else token_nodes
		with patch.object(frappe, "get_doc", side_effect=lambda doctype, name: docs[name]), patch.object(frappe, "get_all", side_effect=get_all), \
				patch.object(frappe.db, "set_value") as set_value, patch.object(engine, "_append_event") as event, \
				patch.object(engine, "_cancel_open_run_artifacts") as cancel, patch.object(engine, "enroll", return_value="RUN-2") as enroll, patch.object(engine, "now_datetime", return_value="2026-11-18 10:00:00"):
			result = engine.move_runs_to_version("AWF-1", "V-NEW")
		return result, set_value, event, cancel, enroll

	def test_run_whose_open_step_still_exists_moves_to_the_new_version(self):
		result, set_value, event, cancel, enroll = self._move(NEW_COMPATIBLE, ["wait"])
		self.assertEqual(result, {"moved": 1, "failed": 0, "re_enrolled": 0})
		self.assertEqual(set_value.call_args.args[:3], ("Automation Run", "RUN-1", "workflow_version"))
		self.assertEqual(event.call_args.args[1], "RUN_MOVED_TO_VERSION")
		cancel.assert_not_called(); enroll.assert_not_called()

	def test_run_whose_step_was_removed_fails_and_the_record_is_enrolled_again(self):
		result, set_value, event, cancel, enroll = self._move(NEW_WITHOUT_WAIT, ["wait"])
		self.assertEqual(result, {"moved": 0, "failed": 1, "re_enrolled": 1})
		self.assertEqual(set_value.call_args.args[2]["status"], "FAILED")
		self.assertEqual(event.call_args.args[1], "RUN_VERSION_MISMATCH")
		self.assertEqual(enroll.call_args.args[:3], ("AWF-1", "Lead", "LEAD-1"))
		self.assertEqual(enroll.call_args.kwargs["workflow_version"], "V-NEW")


class PauseOnEditTest(TestCase):
	def setUp(self):
		self.enterContext(patch.object(api.registry, "require_builder"))
		self.enterContext(patch.object(api.registry, "require_viewer"))
		self.enterContext(patch.object(api, "_envelope", return_value={"workflow_id": "AWF-1", "draft_revision": 1, "payload": {}}))

	def _save(self, status, published_hash, draft_hash):
		saved = {"workflow_id": "AWF-1", "graph_hash": draft_hash}
		def get_value(doctype, name, field, **kw):
			if doctype == "Automation Workflow":
				return frappe._dict(status=status, active_version="V-1")
			return published_hash
		with patch.object(authoring, "save_workflow_draft", return_value=saved), patch.object(frappe.db, "get_value", side_effect=get_value), \
				patch.object(engine, "open_run_count", return_value=12), patch.object(authoring, "change_workflow_state") as change:
			return api.save_draft(), change

	def test_first_real_change_to_an_active_workflow_pauses_it(self):
		result, change = self._save("ACTIVE", "hash-a", "hash-b")
		change.assert_called_once_with("AWF-1", "PAUSED")
		self.assertEqual(result["paused"], {"status": "PAUSED", "records": 12})

	def test_saving_without_a_change_or_on_a_draft_does_not_pause(self):
		for status, published, draft in (("ACTIVE", "same", "same"), ("PAUSED", "a", "b"), ("DRAFT", None, "b")):
			result, change = self._save(status, published, draft)
			change.assert_not_called()
			self.assertNotIn("paused", result)

	def test_pause_preview_counts_records_inside_the_workflow(self):
		doc = frappe._dict(name="AWF-1", check_permission=lambda *_: None)
		with patch.object(frappe, "get_doc", return_value=doc), patch.object(engine, "open_run_count", return_value=7):
			self.assertEqual(api.pause_preview("AWF-1"), {"count": 7})
