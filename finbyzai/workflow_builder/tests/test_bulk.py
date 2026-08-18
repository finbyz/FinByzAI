from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from finbyzai.workflow_builder import bulk, engine
from finbyzai.workflow_builder.api import list_schedules
from finbyzai.workflow_builder.authoring import (
	create_workflow_record,
	get_workflow_draft,
	publish_workflow,
	save_workflow_draft,
)
from finbyzai.workflow_builder.errors import AutomationConflictError


class TestAutomationBulkOperations(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.engine_gate = patch.object(engine, "workflow_runtime_allowed", return_value=True)
		self.engine_gate.start()
		self.addCleanup(self.engine_gate.stop)
		self.bulk_gate = patch.object(bulk, "workflow_runtime_allowed", return_value=True)
		self.bulk_gate.start()
		self.addCleanup(self.bulk_gate.stop)
		self.bulk_enabled = patch.object(bulk, "automation_enabled", return_value=True)
		self.bulk_enabled.start()
		self.addCleanup(self.bulk_enabled.stop)

	def test_backfill_uses_durable_cursor_and_idempotent_occurrences(self):
		leads = [
			frappe.get_doc({"doctype": "Lead", "first_name": f"Backfill {index}"}).insert().name
			for index in range(2)
		]
		created = create_workflow_record("Backfill workflow", "Lead")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		with patch.object(bulk, "_queue_backfill"):
			result = bulk.create_backfill(created["workflow"], [["name", "in", leads]], batch_size=100)
			processed = bulk.process_backfill(result["backfill_id"])
		self.assertEqual(processed, 2)
		job = frappe.get_doc("Automation Backfill Job", result["backfill_id"])
		self.assertEqual(job.status, "COMPLETED")
		self.assertEqual(job.processed_count, 2)
		self.assertEqual(job.enrolled_count, 2)
		self.assertTrue(job.workflow_version)
		self.assertEqual(frappe.db.count("Automation Run", {"workflow": created["workflow"], "source": "BACKFILL"}), 2)

	def test_backfill_stays_pinned_when_a_new_version_is_published(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Pinned Backfill"}).insert().name
		created = create_workflow_record("Pinned backfill workflow", "Lead")
		first = publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		with patch.object(bulk, "_queue_backfill"):
			job = bulk.create_backfill(created["workflow"], [["name", "=", lead]], batch_size=100)
		graph = get_workflow_draft(created["workflow"])["draft"]["graph"]
		graph["nodes"].append(
			{"id": "end-1", "type": "end.complete", "type_version": 1, "position": {"x": 360, "y": 160}, "config": {}}
		)
		graph["edges"] = [
			{"id": "edge-1", "source": graph["start_node_id"], "source_handle": "default", "target": "end-1"}
		]
		saved = save_workflow_draft(created["workflow"], 0, graph)
		second = publish_workflow(created["workflow"], saved["draft_revision"], reenrollment="ALWAYS")
		self.assertNotEqual(first["version"], second["version"])
		with patch.object(bulk, "_queue_backfill"):
			bulk.process_backfill(job["backfill_id"])
		run_name = frappe.db.get_value(
			"Automation Run", {"workflow": created["workflow"], "record_name": lead}, "name", order_by="creation desc"
		)
		run = frappe.get_doc("Automation Run", run_name)
		self.assertEqual(run.workflow_version, first["version"])

	def test_preview_and_dry_run_use_permission_checked_filters_without_enrollment(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Dry Run Lead"}).insert().name
		created = create_workflow_record("Dry run workflow", "Lead")
		published = publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		preview = bulk.preview_backfill(created["workflow"], [["name", "=", lead]])
		self.assertEqual(preview["workflow_version"], published["version"])
		self.assertEqual(preview["estimated_count"], 1)
		self.assertEqual(preview["sample_records"], [lead])
		with patch.object(bulk, "_queue_backfill"):
			result = bulk.create_backfill(created["workflow"], [["name", "=", lead]], dry_run=True)
			bulk.process_backfill(result["backfill_id"])
		job = frappe.get_doc("Automation Backfill Job", result["backfill_id"])
		self.assertEqual(job.status, "COMPLETED")
		self.assertEqual(job.processed_count, 1)
		self.assertEqual(job.enrolled_count, 0)
		self.assertFalse(frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead}))

	def test_preview_receipt_pins_version_snapshot_filters_and_limit(self):
		marker = f"Receipt {frappe.generate_hash(length=8)}"
		frappe.get_doc({"doctype": "Lead", "first_name": marker}).insert()
		created = create_workflow_record("Receipt-pinned backfill", "Lead")
		first = publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		filters = [["first_name", "=", marker]]
		preview = bulk.preview_backfill(created["workflow"], filters, max_records=10)

		graph = get_workflow_draft(created["workflow"])["draft"]["graph"]
		graph["nodes"].append(
			{"id": "receipt-end", "type": "end.complete", "type_version": 1, "position": {"x": 240, "y": 0}, "config": {}}
		)
		graph["edges"] = [
			{"id": "receipt-edge", "source": graph["start_node_id"], "source_handle": "default", "target": "receipt-end"}
		]
		saved = save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], saved["draft_revision"], reenrollment="ALWAYS")
		frappe.get_doc({"doctype": "Lead", "first_name": marker}).insert()

		with patch.object(bulk, "_queue_backfill"):
			result = bulk.create_backfill(
				created["workflow"], filters, max_records=10, preview_receipt=preview["receipt"]
			)
		job = frappe.get_doc("Automation Backfill Job", result["backfill_id"])
		self.assertEqual(job.workflow_version, first["version"])
		self.assertEqual(job.snapshot_at, frappe.utils.get_datetime(preview["snapshot_at"]))
		self.assertEqual(job.estimated_count, preview["estimated_count"])

		with self.assertRaisesRegex(AutomationConflictError, "record limit changed"):
			bulk.create_backfill(
				created["workflow"], filters, max_records=11, preview_receipt=preview["receipt"]
			)

	def test_backfill_pause_resume_and_cancel_are_durable(self):
		created = create_workflow_record("Controlled backfill workflow", "Lead")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		with patch.object(bulk, "_queue_backfill") as queue:
			result = bulk.create_backfill(created["workflow"], [])
			self.assertEqual(bulk.change_backfill_state(result["backfill_id"], "PAUSE")["status"], "PAUSED")
			self.assertEqual(bulk.process_backfill(result["backfill_id"]), 0)
			self.assertEqual(bulk.change_backfill_state(result["backfill_id"], "RESUME")["status"], "QUEUED")
			self.assertEqual(bulk.change_backfill_state(result["backfill_id"], "CANCEL")["status"], "CANCELLED")
		self.assertGreaterEqual(queue.call_count, 2)

	def test_failed_batch_rolls_back_progress_and_can_be_retried(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Failed Backfill"}).insert().name
		created = create_workflow_record("Recoverable backfill workflow", "Lead")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		with patch.object(bulk, "_queue_backfill"):
			result = bulk.create_backfill(created["workflow"], [["name", "=", lead]])
			with patch.object(bulk, "enroll", side_effect=RuntimeError("temporary failure")), patch.object(
				frappe, "log_error"
			):
				self.assertEqual(bulk.process_backfill(result["backfill_id"]), 0)
			job = frappe.get_doc("Automation Backfill Job", result["backfill_id"])
			self.assertEqual(job.status, "FAILED")
			self.assertEqual(job.processed_count, 0)
			self.assertEqual(job.failed_count, 1)
			self.assertEqual(bulk.change_backfill_state(job.name, "RETRY")["status"], "QUEUED")

	def test_scheduler_recovers_a_due_backfill_without_duplicate_inline_work(self):
		created = create_workflow_record("Recovered backfill workflow", "Lead")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		with patch.object(bulk, "_queue_backfill"):
			result = bulk.create_backfill(created["workflow"], [])
		with patch.object(bulk, "_queue_backfill") as queue:
			self.assertGreaterEqual(bulk.dispatch_ready_backfills(), 1)
			queue.assert_any_call(result["backfill_id"], 0)

	def test_schedule_is_disabled_by_default_and_advances_when_due(self):
		created = create_workflow_record("Scheduled workflow", "Lead", trigger_type="trigger.schedule")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		result = bulk.create_schedule(
			created["workflow"],
			"HOURLY",
			str(add_to_date(now_datetime(), minutes=-1)),
			filters=[],
			batch_size=25,
		)
		schedule = frappe.get_doc("Automation Schedule", result["schedule_id"])
		self.assertFalse(schedule.enabled)
		schedule.enabled = 1
		schedule.save()
		dummy = frappe.get_doc({
			"doctype": "Automation Backfill Job",
			"workflow": created["workflow"],
			"workflow_version": frappe.db.get_value("Automation Workflow", created["workflow"], "active_version"),
			"source": "SCHEDULE",
			"schedule": schedule.name,
			"status": "COMPLETED",
			"snapshot_at": now_datetime(),
			"next_batch_at": now_datetime(),
			"batch_size": 25,
			"records_per_minute": 500,
		}).insert(ignore_permissions=True)
		with patch.object(bulk, "create_backfill", return_value={"backfill_id": dummy.name, "status": "QUEUED"}):
			self.assertEqual(bulk.dispatch_due_schedules(), 1)
		schedule.reload()
		self.assertGreater(schedule.next_run_at, now_datetime())
		self.assertEqual(schedule.last_backfill_job, dummy.name)

	def test_schedule_delete_contract_preserves_execution_history(self):
		created = create_workflow_record("Schedule deletion workflow", "Lead", trigger_type="trigger.schedule")
		published = publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		pristine = bulk.create_schedule(
			created["workflow"], "DAILY", str(add_to_date(now_datetime(), days=1))
		)
		self.assertTrue(bulk.delete_schedule(pristine["schedule_id"])["deleted"])

		historical = bulk.create_schedule(
			created["workflow"], "DAILY", str(add_to_date(now_datetime(), days=1))
		)
		frappe.get_doc(
			{
				"doctype": "Automation Backfill Job",
				"workflow": created["workflow"],
				"workflow_version": published["version"],
				"source": "SCHEDULE",
				"schedule": historical["schedule_id"],
				"status": "COMPLETED",
				"snapshot_at": now_datetime(),
				"next_batch_at": now_datetime(),
				"batch_size": 100,
				"records_per_minute": 500,
			}
		).insert(ignore_permissions=True)
		rows = {row.name: row for row in list_schedules(created["workflow"])["rows"]}
		self.assertTrue(rows[historical["schedule_id"]].has_history)
		with self.assertRaisesRegex(AutomationConflictError, "execution history"):
			bulk.delete_schedule(historical["schedule_id"])

	def test_schedule_catch_up_and_overlap_policies_are_deterministic(self):
		created = create_workflow_record("Schedule policy workflow", "Lead", trigger_type="trigger.schedule")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		result = bulk.create_schedule(
			created["workflow"],
			"HOURLY",
			str(add_to_date(now_datetime(), hours=-3)),
			catch_up_policy="SKIP",
		)
		schedule = frappe.get_doc("Automation Schedule", result["schedule_id"])
		schedule.enabled = 1
		schedule.save()
		with patch.object(bulk, "create_backfill") as create:
			self.assertEqual(bulk.dispatch_due_schedules(), 0)
			create.assert_not_called()
		schedule.reload()
		self.assertGreater(schedule.next_run_at, now_datetime())

		schedule.next_run_at = add_to_date(now_datetime(), minutes=-1)
		schedule.catch_up_policy = "RUN_ONCE"
		schedule.overlap_policy = "SKIP"
		schedule.save()
		active = frappe.get_doc({
			"doctype": "Automation Backfill Job",
			"workflow": created["workflow"],
			"workflow_version": frappe.db.get_value("Automation Workflow", created["workflow"], "active_version"),
			"source": "SCHEDULE",
			"schedule": schedule.name,
			"status": "QUEUED",
			"snapshot_at": now_datetime(),
			"next_batch_at": now_datetime(),
			"batch_size": 100,
			"records_per_minute": 500,
		}).insert(ignore_permissions=True)
		with patch.object(bulk, "create_backfill") as create:
			self.assertEqual(bulk.dispatch_due_schedules(), 0)
			create.assert_not_called()

		schedule.reload()
		schedule.next_run_at = add_to_date(now_datetime(), minutes=-1)
		schedule.overlap_policy = "QUEUE"
		schedule.save()
		with patch.object(
			bulk,
			"create_backfill",
			return_value={"backfill_id": active.name, "status": "QUEUED"},
		) as create:
			self.assertEqual(bulk.dispatch_due_schedules(), 1)
			create.assert_called_once()
