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

	def _save(self, status, differs):
		saved = {"workflow_id": "AWF-1", "graph_hash": "hash"}
		state = frappe._dict(status=status, active_version="V-1" if status != "DRAFT" else None)
		with patch.object(authoring, "save_workflow_draft", return_value=saved), patch.object(frappe.db, "get_value", return_value=state), \
				patch.object(authoring, "draft_differs_from_version", return_value=differs) as differs_check, \
				patch.object(engine, "open_run_count", return_value=12), patch.object(authoring, "change_workflow_state") as change:
			return api.save_draft(), change, differs_check

	def test_first_real_change_to_an_active_workflow_pauses_it(self):
		result, change, differs_check = self._save("ACTIVE", True)
		differs_check.assert_called_once_with("AWF-1", "V-1")
		change.assert_called_once_with("AWF-1", "PAUSED")
		self.assertEqual(result["paused"], {"status": "PAUSED", "records": 12})

	def test_saving_without_a_change_or_on_a_draft_does_not_pause(self):
		for status, differs in (("ACTIVE", False), ("PAUSED", True), ("DRAFT", True)):
			result, change, _differs_check = self._save(status, differs)
			change.assert_not_called()
			self.assertNotIn("paused", result)

	def test_pause_preview_counts_records_inside_the_workflow(self):
		doc = frappe._dict(name="AWF-1", check_permission=lambda *_: None)
		with patch.object(frappe, "get_doc", return_value=doc), patch.object(engine, "open_run_count", return_value=7):
			self.assertEqual(api.pause_preview("AWF-1"), {"count": 7})


def graph(x):
	return json.dumps({"start_node_id": "t", "nodes": [{"id": "t", "type": "trigger.manual", "position": {"x": x, "y": 0}}], "edges": []})


class DraftDiffersTest(TestCase):
	def _differs(self, draft_graph, draft_settings="{}"):
		version_row = frappe._dict(graph_json=graph(0), settings_json="{}")
		draft = frappe._dict(graph_json=draft_graph, settings_json=draft_settings)
		with patch.object(frappe.db, "get_value", return_value=version_row), patch.object(authoring, "_draft", return_value=draft):
			return authoring.draft_differs_from_version("AWF-1", "V-1")

	def test_moving_a_node_is_not_a_change(self):
		self.assertFalse(self._differs(graph(300)))

	def test_a_settings_change_is_a_change(self):
		self.assertTrue(self._differs(graph(0), json.dumps({"reenrollment": "ALWAYS"})))


class PublishTest(TestCase):
	def setUp(self):
		self.enterContext(patch.object(api.registry, "require_publisher"))
		self.enterContext(patch.object(api, "_envelope", return_value={"workflow_id": "AWF-1", "draft_revision": 2, "payload": {}}))

	def _publish(self, published, versions, status="ACTIVE"):
		active = iter(versions)
		with patch.object(frappe.db, "get_value", side_effect=lambda *a, **k: next(active)), \
				patch.object(authoring, "publish_workflow", return_value=published), \
				patch.object(engine, "move_runs_to_version", return_value={"moved": 3, "failed": 0, "re_enrolled": 0}) as move, \
				patch.object(engine, "resume_held_tokens", return_value=5) as resume, \
				patch.object(authoring, "change_workflow_state", return_value={"status": "ACTIVE"}) as change:
			return api.publish(), move, resume, change

	def test_publishing_a_new_version_moves_paused_records_to_it(self):
		published = {"workflow_id": "AWF-1", "version": "V-2", "status": "ACTIVE", "unchanged": False}
		result, move, resume, change = self._publish(published, ["V-1", "V-2"])
		move.assert_called_once_with("AWF-1", "V-2")
		self.assertEqual(result["runs"]["moved"], 3)
		resume.assert_called_once_with("AWF-1")
		change.assert_not_called()

	def test_publishing_an_unchanged_paused_workflow_restarts_it(self):
		published = {"workflow_id": "AWF-1", "version": "V-1", "status": "PAUSED", "unchanged": True}
		result, move, resume, change = self._publish(published, ["V-1", "V-1"])
		move.assert_not_called()
		change.assert_called_once_with("AWF-1", "ACTIVE")
		self.assertEqual(result["status"], "ACTIVE")
		resume.assert_called_once_with("AWF-1")
