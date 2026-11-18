"""WF-04: start a workflow manually for selected records or for a segment."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from finbyzai.workflow_builder import api, engine
from finbyzai.workflow_builder.errors import AutomationError


def workflow(doctype="Lead"):
	doc = frappe._dict(name="AWF-1", title="Follow up", primary_doctype=doctype, active_version="V-1")
	doc.check_permission = lambda *_: None
	return doc


class ManualStartTest(TestCase):
	def setUp(self):
		self.enterContext(patch.object(api.registry, "require_operator"))

	def test_only_active_workflows_with_a_manual_trigger_are_offered(self):
		rows = [frappe._dict(name="A", title="Manual one", active_version="V-A"), frappe._dict(name="B", title="On form", active_version="V-B")]
		with patch.object(frappe, "get_list", return_value=rows), \
				patch.object(engine, "published_trigger_type", side_effect=lambda version: "trigger.manual" if version == "V-A" else "trigger.event"):
			self.assertEqual(api.manual_start_workflows("Lead"), [{"name": "A", "title": "Manual one"}])

	def test_selected_records_are_enrolled_and_skips_are_reported(self):
		runs = {"LEAD-1": "RUN-1", "LEAD-2": None}
		with patch.object(frappe, "get_doc", side_effect=lambda doctype, name=None: workflow() if doctype == "Automation Workflow" else MagicMock()), \
				patch.object(engine, "published_trigger_type", return_value="trigger.manual"), \
				patch.object(engine, "enroll", side_effect=lambda wf, doctype, name, **kw: runs[name]) as enroll:
			result = api.enroll_manual_records("AWF-1", '["LEAD-1", "LEAD-2", "LEAD-1"]', "key-1")
		self.assertEqual(result["enrolled"], 1)
		self.assertEqual(result["skipped_count"], 1)
		self.assertEqual(result["skipped"][0]["record"], "LEAD-2")
		self.assertEqual(enroll.call_count, 2)  # duplicate name in the selection is enrolled once
		self.assertEqual(enroll.call_args_list[0].kwargs["occurrence_key"], "key-1:LEAD-1")

	def test_selection_size_and_key_are_checked(self):
		with self.assertRaises(AutomationError):
			api.enroll_manual_records("AWF-1", "[]", "key")
		with self.assertRaises(AutomationError):
			api.enroll_manual_records("AWF-1", [f"L{i}" for i in range(501)], "key")
		with self.assertRaises(AutomationError):
			api.enroll_manual_records("AWF-1", '["L1"]', None)

	def test_workflow_without_manual_trigger_refuses(self):
		with patch.object(frappe, "get_doc", return_value=workflow()), patch.object(engine, "published_trigger_type", return_value="trigger.event"):
			with self.assertRaises(AutomationError):
				api.enroll_manual_records("AWF-1", '["L1"]', "key")

	def test_segment_of_another_record_type_is_refused(self):
		segment = frappe._dict(name="S", audience_doctype="Contact", archived=0, check_permission=lambda *_: None)
		def get_doc(doctype, name=None):
			return workflow("Lead") if doctype == "Automation Workflow" else segment
		with patch.object(frappe, "get_doc", side_effect=get_doc), patch.object(engine, "published_trigger_type", return_value="trigger.manual"):
			with self.assertRaises(AutomationError):
				api.enroll_manual_segment("AWF-1", "S", "key")

	def test_large_segments_run_in_the_background_small_ones_inline(self):
		segment = frappe._dict(name="S", audience_doctype="Lead", archived=0, check_permission=lambda *_: None)
		def get_doc(doctype, name=None):
			return workflow("Lead") if doctype == "Automation Workflow" else segment
		with patch.object(frappe, "get_doc", side_effect=get_doc), patch.object(engine, "published_trigger_type", return_value="trigger.manual"), \
				patch("finbyzreach.segment_service.count_members", return_value=5000), patch.object(frappe, "enqueue") as enqueue:
			result = api.enroll_manual_segment("AWF-1", "S", "key")
		self.assertEqual(result, {"queued": True, "size": 5000})
		self.assertEqual(enqueue.call_args.kwargs["segment_name"], "S")
		with patch.object(frappe, "get_doc", side_effect=get_doc), patch.object(engine, "published_trigger_type", return_value="trigger.manual"), \
				patch("finbyzreach.segment_service.count_members", return_value=3), \
				patch.object(api, "_enroll_segment_members", return_value={"enrolled": 3, "skipped_count": 0}) as inline:
			result = api.enroll_manual_segment("AWF-1", "S", "key")
		self.assertEqual(result["queued"], False)
		self.assertEqual(result["enrolled"], 3)
		inline.assert_called_once()
