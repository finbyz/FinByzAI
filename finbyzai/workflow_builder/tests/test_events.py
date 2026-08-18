from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from finbyzai.workflow_builder import engine, events
from finbyzai.workflow_builder.authoring import (
	create_workflow_record,
	get_workflow_draft,
	publish_workflow,
	save_workflow_draft,
)
from finbyzai.workflow_builder.errors import AutomationTransientError


class TestAutomationEvents(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.flags.in_install = False
		frappe.flags.in_migrate = False
		frappe.flags.automation_dispatch_wake_registered = False
		self.enabled = patch.object(events, "automation_enabled", return_value=True)
		self.enabled.start()
		self.addCleanup(self.enabled.stop)
		self.runtime_allowed = patch.object(engine, "workflow_runtime_allowed", return_value=True)
		self.runtime_allowed.start()
		self.addCleanup(self.runtime_allowed.stop)

	def _publish(self, title: str, trigger_type: str = "trigger.document_insert") -> dict:
		created = create_workflow_record(title, "Lead", trigger_type=trigger_type)
		publish_workflow(created["workflow"], 0)
		return created

	def _waiting_policy_run(self, title: str, first_name: str, settings: dict) -> tuple[object, dict, str, str]:
		lead = frappe.get_doc({"doctype": "Lead", "first_name": first_name}).insert()
		created = create_workflow_record(title, "Lead")
		graph = created["graph"]
		graph["nodes"].extend(
			[
				{"id": "delay", "type": "delay.fixed", "type_version": 1, "config": {"seconds": 3600}},
				{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
			]
		)
		graph["edges"] = [
			{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "delay"},
			{"id": "edge-2", "source": "delay", "source_handle": "default", "target": "end"},
		]
		saved = save_workflow_draft(created["workflow"], 0, graph, settings)
		self.assertTrue(saved["valid"])
		published = publish_workflow(created["workflow"], 1, reenrollment="ALWAYS")
		run_name = engine.enroll(
			created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key=frappe.generate_hash(length=12)
		)
		engine.execute_token(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "trigger-1"}, "name")
		)
		engine.execute_token(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "name")
		)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "WAITING")
		timer_name = frappe.db.get_value("Automation Timer", {"run": run_name, "status": "ACTIVE"}, "name")
		self.assertTrue(timer_name)
		return lead, published, run_name, timer_name

	def _policy_event(self, lead_name: str) -> str:
		return frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead_name, "event_type": "ON_UPDATE", "status": "PENDING"},
			"name",
			order_by="creation desc",
		)

	def _save_policy_only(self, doc) -> None:
		"""Keep assertions independent from unrelated active subscriptions on the test site."""
		with patch.object(events, "_matching_subscriptions", return_value=[]):
			doc.save()

	def test_no_subscription_creates_no_outbox_or_queue_wake(self):
		with patch.object(events, "_matching_subscriptions", return_value=[]), patch.object(
			events, "_register_dispatch_wake"
		) as wake:
			lead = frappe.get_doc({"doctype": "Lead", "first_name": "No Subscription"}).insert()
		self.assertFalse(
			frappe.db.exists("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		)
		wake.assert_not_called()

	def test_internal_framework_doctypes_are_rejected_before_subscription_lookup(self):
		doc = frappe.get_doc({"doctype": "Error Log", "method": "automation test", "error": "test"})
		with patch.object(events, "_matching_subscriptions") as subscriptions:
			events._capture(doc, "AFTER_INSERT")
		subscriptions.assert_not_called()

	def test_source_rollback_removes_outbox_event(self):
		self._publish("Rollback event")
		frappe.db.savepoint("before_source")
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Rolled Back Source"}).insert()
		self.assertTrue(
			frappe.db.exists("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		)
		frappe.db.rollback(save_point="before_source")
		self.assertFalse(frappe.db.exists("Lead", lead.name))
		self.assertFalse(
			frappe.db.exists("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		)

	def test_many_source_documents_register_one_dispatch_wake(self):
		self._publish("Singleton wake")
		frappe.flags.automation_dispatch_wake_registered = False
		before = frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "event_type": "AFTER_INSERT"})
		callbacks_before = list(frappe.db.after_commit._functions).count(events._enqueue_dispatcher_safely)
		for index in range(20):
			frappe.get_doc({"doctype": "Lead", "first_name": f"Batch {index}"}).insert()
		callbacks_after = list(frappe.db.after_commit._functions).count(events._enqueue_dispatcher_safely)
		self.assertEqual(callbacks_after, callbacks_before + 1)
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "event_type": "AFTER_INSERT"}),
			before + 20,
		)

	def test_queue_overload_defers_to_scheduler_without_raising(self):
		with patch.object(frappe, "enqueue", side_effect=frappe.QueueOverloaded("full")):
			self.assertFalse(events._enqueue_dispatcher_safely())

	def test_runtime_health_includes_failed_stale_orphaned_and_operator_work(self):
		def count(doctype, filters=None):
			if doctype == "Automation Trigger Subscription":
				return 1
			if doctype == "Automation Run" and (filters or {}).get("status") == "FAILED":
				return 2
			if doctype == "Automation Incident":
				return 3
			if doctype == "Automation Dead Letter":
				return 4
			return 0

		active_run = SimpleNamespace(
			name="RUN-ORPHAN",
			workflow="MISSING-WORKFLOW",
			workflow_version="MISSING-VERSION",
			modified=frappe.utils.add_to_date(now_datetime(), minutes=-30),
		)
		with (
			patch.object(frappe, "get_list", return_value=[]),
			patch.object(frappe, "get_all", return_value=[active_run]),
			patch.object(frappe.db, "count", side_effect=count),
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe.db, "exists", return_value=False),
			patch.object(events, "int_setting", side_effect=lambda name, default: 24 if name == "health_failure_window_hours" else 15),
			patch("frappe.utils.background_jobs.get_queue", return_value=SimpleNamespace(count=0)),
			patch("frappe.utils.background_jobs.get_job_status", return_value=None),
		):
			health = events.runtime_health("WF-1")

		self.assertFalse(health["healthy"])
		self.assertEqual(health["runs"]["recent_failed"], 2)
		self.assertEqual(health["runs"]["stale_active"], 1)
		self.assertEqual(health["runs"]["orphaned_active"], 1)
		self.assertEqual(health["open_incidents"], 3)
		self.assertEqual(health["open_dead_letters"], 4)
		self.assertTrue(
			{
				"RECENT_FAILED_RUNS",
				"STALE_ACTIVE_RUNS",
				"ORPHANED_ACTIVE_RUNS",
				"OPEN_INCIDENTS",
				"OPEN_DEAD_LETTERS",
			}.issubset(health["reasons"])
		)

	def test_after_insert_subscription_enrolls_without_runtime_versions(self):
		created = self._publish("Insert event")
		test_subscriptions = [
			row for row in events._matching_subscriptions("Lead", "AFTER_INSERT")
			if row.workflow == created["workflow"]
		]
		self.assertEqual(len(test_subscriptions), 1)
		with patch.object(events, "_matching_subscriptions", return_value=test_subscriptions):
			lead = frappe.get_doc({"doctype": "Lead", "first_name": "Inserted Event"}).insert()
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "AFTER_INSERT"},
			"name",
		)
		before_versions = frappe.db.count("Version", {"ref_doctype": "Automation Outbox Event"})
		with patch.object(events, "_matching_subscriptions", return_value=test_subscriptions), patch.object(frappe.db, "commit") as commit:
			self.assertEqual(events.process_outbox_event(event_name), 1)
		commit.assert_not_called()
		self.assertEqual(
			frappe.db.count("Version", {"ref_doctype": "Automation Outbox Event"}), before_versions
		)
		self.assertTrue(
			frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name})
		)

	def test_stale_subscription_is_rejected_during_dispatch_not_source_save(self):
		created = self._publish("Stale subscription boundary")
		subscriptions = [
			row for row in events._matching_subscriptions("Lead", "AFTER_INSERT")
			if row.workflow == created["workflow"]
		]
		with patch.object(events, "_matching_subscriptions", return_value=subscriptions):
			lead = frappe.get_doc({"doctype": "Lead", "first_name": "Stale subscription"}).insert()
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "AFTER_INSERT"},
			"name",
		)
		frappe.db.set_value("Automation Workflow", created["workflow"], "status", "PAUSED")
		with patch.object(events, "_matching_subscriptions", return_value=subscriptions):
			self.assertEqual(events.process_outbox_event(event_name), 0)
		self.assertFalse(frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name}))
		self.assertTrue(
			frappe.db.exists(
				"Automation Enrollment Decision",
				{"workflow": created["workflow"], "record_name": lead.name, "reason_code": "STALE_SUBSCRIPTION"},
			)
		)

	def test_irrelevant_update_is_filtered_before_outbox_insert(self):
		created = create_workflow_record("Relevant update", "Lead", trigger_type="trigger.document_change")
		graph = created["graph"]
		graph["nodes"][0]["config"] = {
			"condition": {
				"kind": "predicate",
				"field": "company_name",
				"operator": "eq",
				"value": "Qualified Company",
			}
		}
		save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], 1)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Updated Event"}).insert()
		before = frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		lead.first_name = "Unrelated Name"
		lead.save()
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name}),
			before,
		)
		lead.company_name = "Qualified Company"
		lead.save()
		# A relevant update coalesces into an already-pending ON_UPDATE event instead
		# of creating duplicate queue work for the same record.
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name}),
			before,
		)
		pending_fields = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "ON_UPDATE", "status": "PENDING"},
			"changed_fields_json",
		)
		self.assertIn("company_name", frappe.parse_json(pending_fields))
		self.assertEqual(frappe.get_meta("Workflow").module, "Workflow")

	def test_relevant_change_completes_live_run_and_cancels_durable_wait(self):
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Goal reached"}
		lead, _published, run_name, timer_name = self._waiting_policy_run(
			"Event-driven goal", "Working", {"goal_condition": goal}
		)
		lead.first_name = "Goal reached"
		self._save_policy_only(lead)
		event_name = self._policy_event(lead.name)
		self.assertTrue(event_name)
		before_goal_checks = events.operation_snapshot()["policy_evaluations"]["counts"]["GOAL_MET"]

		self.assertEqual(events.process_outbox_event(event_name), 0)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")
		self.assertEqual(frappe.db.get_value("Automation Timer", timer_name, "status"), "CANCELLED")
		self.assertIsNone(frappe.db.get_value("Automation Timer", timer_name, "released_at"))
		self.assertEqual(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "status"),
			"CANCELLED",
		)
		evaluation = frappe.get_doc("Automation Policy Evaluation", {"run": run_name, "outbox_event": event_name})
		self.assertEqual(evaluation.outcome, "GOAL_MET")
		self.assertEqual(evaluation.reason_code, "GOAL_CONDITION_TRUE")
		self.assertEqual(
			frappe.db.count("Automation Run Event", {"run": run_name, "event_type": "RUN_GOAL_MET"}), 1
		)
		policy_snapshot = events.operation_snapshot()["policy_evaluations"]
		self.assertEqual(policy_snapshot["counts"]["GOAL_MET"], before_goal_checks + 1)
		self.assertTrue(any(row.run == run_name for row in policy_snapshot["recent"]))

	def test_relevant_change_cancels_run_that_loses_eligibility(self):
		eligibility = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Eligible"}
		lead, _published, run_name, timer_name = self._waiting_policy_run(
			"Event-driven eligibility",
			"Eligible",
			{"unenroll_when_ineligible": True, "eligibility_condition": eligibility},
		)
		lead.first_name = "No longer eligible"
		self._save_policy_only(lead)
		event_name = self._policy_event(lead.name)
		self.assertTrue(event_name)

		events.process_outbox_event(event_name)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "CANCELLED")
		self.assertEqual(frappe.db.get_value("Automation Timer", timer_name, "status"), "CANCELLED")
		self.assertEqual(
			frappe.db.get_value("Automation Policy Evaluation", {"run": run_name}, "outcome"),
			"ELIGIBILITY_LOST",
		)
		self.assertEqual(
			frappe.db.count("Automation Run Event", {"run": run_name, "event_type": "RUN_ELIGIBILITY_LOST"}), 1
		)

	def test_policy_capture_filters_unrelated_changes_and_snapshot_runs(self):
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Done"}
		lead, _published, run_name, _timer_name = self._waiting_policy_run(
			"Relevant policy fields", "Working", {"goal_condition": goal}
		)
		before = frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		lead.company_name = "Unrelated"
		self._save_policy_only(lead)
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name}), before
		)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "WAITING")

		snapshot_lead, _published, snapshot_run, _timer_name = self._waiting_policy_run(
			"Snapshot policy", "Snapshot working", {"read_mode": "ENROLLMENT_SNAPSHOT", "goal_condition": goal}
		)
		snapshot_before = frappe.db.count(
			"Automation Outbox Event", {"object_doctype": "Lead", "object_name": snapshot_lead.name}
		)
		snapshot_lead.first_name = "Done"
		self._save_policy_only(snapshot_lead)
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": snapshot_lead.name}),
			snapshot_before,
		)
		self.assertEqual(frappe.db.get_value("Automation Run", snapshot_run, "status"), "WAITING")

	def test_policy_evaluation_is_deduplicated_per_run_and_event(self):
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Done"}
		lead, _published, run_name, _timer_name = self._waiting_policy_run(
			"Policy evaluation dedupe", "Working", {"goal_condition": goal}
		)
		lead.first_name = "Still working"
		self._save_policy_only(lead)
		event_name = self._policy_event(lead.name)
		event = frappe.get_doc("Automation Outbox Event", event_name)
		arguments = {
			"outbox_event": event.name,
			"event_id": event.event_id,
			"record_doctype": event.object_doctype,
			"record_name": event.object_name,
			"changed_fields": set(frappe.parse_json(event.changed_fields_json)),
		}
		first = engine.reevaluate_active_run_policies(**arguments)
		second = engine.reevaluate_active_run_policies(**arguments)
		self.assertEqual(first[0]["outcome"], "NO_CHANGE")
		self.assertTrue(second[0]["deduplicated"])
		self.assertEqual(
			frappe.db.count("Automation Policy Evaluation", {"run": run_name, "event_id": event.event_id}), 1
		)

	def test_active_run_uses_its_pinned_policy_after_new_publish(self):
		old_goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Old goal"}
		lead, first_version, run_name, _timer_name = self._waiting_policy_run(
			"Pinned lifecycle policy", "Working", {"goal_condition": old_goal}
		)
		draft = get_workflow_draft(first_version["workflow_id"])["draft"]
		new_goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "New goal"}
		saved = save_workflow_draft(
			first_version["workflow_id"], draft["draft_revision"], draft["graph"], {"goal_condition": new_goal}
		)
		second_version = publish_workflow(first_version["workflow_id"], saved["draft_revision"], reenrollment="ALWAYS")
		self.assertNotEqual(first_version["version"], second_version["version"])
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "workflow_version"), first_version["version"])

		lead.first_name = "Old goal"
		self._save_policy_only(lead)
		events.process_outbox_event(self._policy_event(lead.name))
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")
		self.assertEqual(
			frappe.db.get_value("Automation Policy Evaluation", {"run": run_name}, "workflow_version"),
			first_version["version"],
		)

	def test_transient_failure_retries_and_expired_lease_recovers(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Retry Source"}).insert()
		event = frappe.get_doc(
			{
				"doctype": "Automation Outbox Event",
				"event_id": frappe.generate_hash(length=32),
				"event_type": "AFTER_INSERT",
				"object_doctype": "Lead",
				"object_name": lead.name,
				"status": "PROCESSING",
				"attempts": 1,
				"available_at": now_datetime(),
				"lease_owner": "dead-worker",
				"lease_until": now_datetime() - timedelta(minutes=1),
			}
		).insert(ignore_permissions=True)
		events._fail_event(event.name, 1, AutomationTransientError("temporary"))
		row = frappe.db.get_value(
			"Automation Outbox Event", event.name, ["status", "error_code", "available_at"], as_dict=True
		)
		self.assertEqual(row.status, "FAILED")
		self.assertEqual(row.error_code, "WF_TRANSIENT")
		frappe.db.set_value(
			"Automation Outbox Event",
			event.name,
			{"status": "PROCESSING", "lease_until": now_datetime() - timedelta(seconds=1)},
			update_modified=False,
		)
		self.assertEqual(events._recover_expired_leases(), 1)
		self.assertEqual(frappe.db.get_value("Automation Outbox Event", event.name, "status"), "FAILED")
