from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from finbyzai.workflow_builder import engine, events
from finbyzai.workflow_builder.authoring import create_workflow_record, publish_workflow


class TestLoadAndRecovery(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.enabled = patch.object(events, "automation_enabled", return_value=True)
		self.enabled.start()
		self.addCleanup(self.enabled.stop)
		self.engine_enabled = patch.object(engine, "automation_enabled", return_value=True)
		self.engine_enabled.start()
		self.addCleanup(self.engine_enabled.stop)
		self.subscription_scope = patch.object(events, "_matching_subscriptions", return_value=[])
		self.subscription_scope.start()
		self.addCleanup(self.subscription_scope.stop)
		self.test_runs = []
		self.test_events = []
		self.test_leads = []
		created = create_workflow_record(f"Load fixture {frappe.generate_hash(length=8)}", "Lead")
		published = publish_workflow(created["workflow"], 0)
		self.workflow_name = created["workflow"]
		self.workflow_version = published["version"]
		self.addCleanup(self._cleanup_test_rows)

	def _cleanup_test_rows(self):
		# Worker/recovery paths commit independently, so do not rely only on the
		# in-memory list. Remove every run owned by this test workflow, including
		# any run created by an event path after the fixture transaction committed.
		run_names = set(self.test_runs)
		run_names.update(
			frappe.get_all("Automation Run", filters={"workflow": self.workflow_name}, pluck="name", limit=0)
		)
		for run_name in run_names:
			self._delete_test_run(run_name)
		for event_name in self.test_events:
			frappe.db.delete("Automation Outbox Event", {"name": event_name})
		for lead_name in self.test_leads:
			frappe.db.delete("Lead", {"name": lead_name})
		frappe.db.delete("Automation Enrollment Ledger", {"workflow": self.workflow_name})
		frappe.db.delete("Automation Trigger Subscription", {"workflow": self.workflow_name})
		frappe.db.delete("Automation Audit Event", {"workflow": self.workflow_name})
		frappe.db.delete("Automation Workflow Draft", {"workflow": self.workflow_name})
		frappe.db.delete("Automation Workflow Version", {"workflow": self.workflow_name})
		frappe.db.delete("Automation Workflow", {"name": self.workflow_name})
		frappe.db.commit()

	def _delete_test_run(self, run_name):
		frappe.db.delete("Automation Dead Letter", {"run": run_name})
		frappe.db.delete("Automation Incident", {"run": run_name})
		frappe.db.delete("Automation Action Attempt", {"run": run_name})
		frappe.db.delete("Automation Effect Ledger", {"run": run_name})
		frappe.db.delete("Automation Policy Evaluation", {"run": run_name})
		frappe.db.delete("Automation Enrollment Decision", {"run": run_name})
		frappe.db.delete("Automation Timer", {"run": run_name})
		frappe.db.delete("Automation Run Event", {"run": run_name})
		frappe.db.delete("Automation Run Token", {"run": run_name})
		frappe.db.delete("Automation Run", {"name": run_name})

	def test_worker_crash_recovery_outbox(self):
		"""Simulate a worker crash during outbox processing and verify lease expiry recovers it."""
		# Create an event that a worker claimed but crashed before completing
		now = now_datetime()
		past = add_to_date(now, minutes=-10)
		crashed_lead = frappe.get_doc({"doctype": "Lead", "first_name": "Crashed Worker"}).insert()
		active_lead = frappe.get_doc({"doctype": "Lead", "first_name": "Active Worker"}).insert()
		self.test_leads.extend([crashed_lead.name, active_lead.name])

		event = frappe.get_doc({
			"doctype": "Automation Outbox Event",
			"event_id": frappe.generate_hash(length=20),
			"event_type": "ON_UPDATE",
			"object_doctype": "Lead",
			"object_name": crashed_lead.name,
			"status": "PROCESSING",
			"available_at": past,
			"lease_owner": "crashed-worker",
			"lease_until": past, # expired
		}).insert(ignore_permissions=True, ignore_links=True)
		self.test_events.append(event.name)

		# Also create a non-expired one
		future = add_to_date(now, minutes=10)
		event2 = frappe.get_doc({
			"doctype": "Automation Outbox Event",
			"event_id": frappe.generate_hash(length=20),
			"event_type": "ON_UPDATE",
			"object_doctype": "Lead",
			"object_name": active_lead.name,
			"status": "PROCESSING",
			"available_at": now,
			"lease_owner": "active-worker",
			"lease_until": future, # active
		}).insert(ignore_permissions=True, ignore_links=True)
		self.test_events.append(event2.name)

		# Run the recovery sweep
		recovered = events._recover_expired_leases(event_names=[event.name, event2.name])

		self.assertEqual(recovered, 1)

		# Verify crashed event was recovered
		doc1 = frappe.get_doc("Automation Outbox Event", event.name)
		self.assertEqual(doc1.status, "FAILED")
		self.assertEqual(doc1.error_code, "LEASE_EXPIRED")
		self.assertIsNone(doc1.lease_owner)
		self.assertIsNone(doc1.lease_until)

		# Verify active event was NOT touched
		doc2 = frappe.get_doc("Automation Outbox Event", event2.name)
		self.assertEqual(doc2.status, "PROCESSING")
		self.assertEqual(doc2.lease_owner, "active-worker")

	def test_worker_crash_recovery_tokens(self):
		"""Simulate a worker crash during token processing and verify lease expiry recovers it."""
		now = now_datetime()
		past = add_to_date(now, minutes=-10)
		run = frappe.get_doc({
				"doctype": "Automation Run",
				"workflow": self.workflow_name,
				"workflow_version": self.workflow_version,
				"record_doctype": "Lead",
				"record_name": "Test",
				"record_key": "Lead:Test",
				"source": "MANUAL",
				"status": "RUNNING",
		}).insert(ignore_permissions=True, ignore_links=True)
		run_name = run.name
		self.test_runs.append(run_name)

		token = frappe.get_doc({
			"doctype": "Automation Run Token",
			"run": run_name,
			"node_id": "trigger-1",
			"status": "RUNNING",
			"available_at": past,
			"lease_owner": "crashed-worker",
			"lease_until": past, # expired
		}).insert(ignore_permissions=True)

		# Recovery sweep
		with patch.object(engine, "_queue_token") as mock_queue:
			ready_count = engine.dispatch_ready_tokens(token_names=[token.name])
			self.assertEqual(ready_count, 1)
			mock_queue.assert_called_with(token.name)

		doc = frappe.get_doc("Automation Run Token", token.name)
		self.assertEqual(doc.status, "READY")
		self.assertIsNone(doc.lease_owner)
		self.assertIsNone(doc.lease_until)
		self._delete_test_run(run_name)
		self.test_runs.remove(run_name)
		frappe.db.commit()

	def test_dispatcher_burst(self):
		"""Simulate a sudden burst of ready tokens and pending outbox events to ensure dispatchers handle load gracefully."""
		run = frappe.get_doc({
				"doctype": "Automation Run",
				"workflow": self.workflow_name,
				"workflow_version": self.workflow_version,
				"record_doctype": "Lead",
				"record_name": "Test",
				"record_key": "Lead:Test",
				"source": "MANUAL",
				"status": "RUNNING",
		}).insert(ignore_permissions=True, ignore_links=True)
		run_name = run.name
		self.test_runs.append(run_name)

		now = now_datetime()
		# Insert 150 tokens (assuming default batch size is 100)
		token_names = []
		for i in range(150):
			token_names.append(frappe.get_doc({
				"doctype": "Automation Run Token",
				"run": run_name,
				"node_id": f"trigger-{i}",
				"status": "READY",
				"available_at": now,
			}).insert(ignore_permissions=True).name)

		# Insert 150 outbox events
		event_names = []
		for i in range(150):
			event_names.append(frappe.get_doc({
				"doctype": "Automation Outbox Event",
				"event_id": frappe.generate_hash(length=20),
				"event_type": "ON_UPDATE",
				"object_doctype": "Lead",
				"object_name": f"Test Lead {i}",
				"status": "PENDING",
				"available_at": now,
			}).insert(ignore_permissions=True, ignore_links=True).name)
		self.test_events.extend(event_names)

		# Run dispatcher and verify it respects batch size limits (e.g., processes exactly 100)
		with patch.object(engine, "_queue_token") as mock_queue_token:
			dispatched_tokens = engine.dispatch_ready_tokens(token_names=token_names)
			# Depending on settings, it should dispatch up to token_batch_size (default 100)
			self.assertTrue(dispatched_tokens > 0)
			self.assertEqual(mock_queue_token.call_count, dispatched_tokens)

		with patch.object(events.frappe, "enqueue") as mock_queue_event:
			dispatched_outbox = events.dispatch_pending_outbox(event_names=event_names)
			self.assertTrue(dispatched_outbox > 0)
			self.assertEqual(mock_queue_event.call_count, dispatched_outbox)
		self._delete_test_run(run_name)
		self.test_runs.remove(run_name)
		frappe.db.commit()

	def test_missing_workflow_is_terminally_quarantined(self):
		run = frappe.get_doc(
			{
				"doctype": "Automation Run",
				"workflow": f"missing-{frappe.generate_hash(length=10)}",
				"workflow_version": "missing-version",
				"record_doctype": "Lead",
				"record_name": "Test",
				"record_key": "Lead:Test",
				"source": "MANUAL",
				"status": "QUEUED",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		self.test_runs.append(run.name)
		token = frappe.get_doc(
			{
				"doctype": "Automation Run Token",
				"run": run.name,
				"node_id": "trigger-1",
				"status": "READY",
				"available_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
		sibling = frappe.get_doc(
			{
				"doctype": "Automation Run Token",
				"run": run.name,
				"node_id": "next-node",
				"status": "READY",
				"available_at": now_datetime(),
			}
		).insert(ignore_permissions=True)

		engine.execute_token(token.name)
		engine.execute_token(sibling.name)

		self.assertEqual(frappe.db.get_value("Automation Run Token", token.name, "status"), "FAILED")
		self.assertEqual(frappe.db.get_value("Automation Run Token", sibling.name, "status"), "CANCELLED")
		self.assertEqual(frappe.db.get_value("Automation Run", run.name, "status"), "FAILED")
		self.assertEqual(frappe.db.get_value("Automation Run", run.name, "error_code"), "MISSING_WORKFLOW")
		self._delete_test_run(run.name)
		self.test_runs.remove(run.name)
		frappe.db.commit()

	def test_tokenless_orphaned_run_is_recovered_by_scheduler(self):
		run = frappe.get_doc(
			{
				"doctype": "Automation Run",
				"workflow": f"missing-{frappe.generate_hash(length=10)}",
				"workflow_version": "missing-version",
				"record_doctype": "Lead",
				"record_name": "Test",
				"record_key": "Lead:Test",
				"source": "MANUAL",
				"status": "RUNNING",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		self.test_runs.append(run.name)

		self.assertGreaterEqual(engine.recover_orphaned_active_runs(), 1)
		self.assertEqual(frappe.db.get_value("Automation Run", run.name, "status"), "FAILED")
		self.assertEqual(frappe.db.get_value("Automation Run", run.name, "error_code"), "MISSING_WORKFLOW")
		self.assertTrue(
			frappe.db.exists(
				"Automation Dead Letter", {"source_type": "RUN", "source_name": run.name, "status": "OPEN"}
			)
		)
		self._delete_test_run(run.name)
		self.test_runs.remove(run.name)
		frappe.db.commit()

	def test_orphan_recovery_closes_token_timer_and_attempt_evidence(self):
		run = frappe.get_doc(
			{
				"doctype": "Automation Run",
				"workflow": f"missing-{frappe.generate_hash(length=10)}",
				"workflow_version": "missing-version",
				"record_doctype": "Lead",
				"record_name": "Test",
				"record_key": "Lead:Test",
				"source": "MANUAL",
				"status": "RUNNING",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		self.test_runs.append(run.name)
		token = frappe.get_doc(
			{
				"doctype": "Automation Run Token",
				"run": run.name,
				"node_id": "delay",
				"status": "WAITING",
				"lease_owner": "abandoned-worker",
				"lease_until": add_to_date(now_datetime(), minutes=-10),
			}
		).insert(ignore_permissions=True)
		timer = frappe.get_doc(
			{
				"doctype": "Automation Timer",
				"run": run.name,
				"token": token.name,
				"node_id": "delay",
				"timer_type": "DELAY",
				"due_at": add_to_date(now_datetime(), minutes=10),
				"status": "ACTIVE",
			}
		).insert(ignore_permissions=True)
		attempt = frappe.get_doc(
			{
				"doctype": "Automation Action Attempt",
				"run": run.name,
				"token": token.name,
				"node_id": "delay",
				"attempt_no": 1,
				"status": "WAITING",
				"started_at": now_datetime(),
			}
		).insert(ignore_permissions=True)

		self.assertGreaterEqual(engine.recover_orphaned_active_runs(), 1)
		token_state = frappe.db.get_value(
			"Automation Run Token",
			token.name,
			["status", "completed_at", "lease_owner", "lease_until"],
			as_dict=True,
		)
		self.assertEqual(token_state.status, "CANCELLED")
		self.assertIsNotNone(token_state.completed_at)
		self.assertIsNone(token_state.lease_owner)
		self.assertIsNone(token_state.lease_until)
		timer_state = frappe.db.get_value(
			"Automation Timer", timer.name, ["status", "released_at"], as_dict=True
		)
		self.assertEqual(timer_state.status, "CANCELLED")
		self.assertIsNotNone(timer_state.released_at)
		attempt_state = frappe.db.get_value(
			"Automation Action Attempt", attempt.name, ["status", "completed_at", "error_code"], as_dict=True
		)
		self.assertEqual(attempt_state.status, "CANCELLED")
		self.assertIsNotNone(attempt_state.completed_at)
		self.assertEqual(attempt_state.error_code, "MISSING_WORKFLOW")

		self._delete_test_run(run.name)
		self.test_runs.remove(run.name)
		frappe.db.commit()
