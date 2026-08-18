import threading
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder import engine, events
from finbyzai.workflow_builder.authoring import create_workflow_record, publish_workflow


class TestDeadlocks(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.enabled = patch.object(events, "automation_enabled", return_value=True)
		self.enabled.start()
		self.addCleanup(self.enabled.stop)
		self.test_events = []
		self.test_runs = []
		self.test_workflows = []
		fixture = create_workflow_record(f"Concurrency fixture {frappe.generate_hash(length=8)}", "Lead")
		fixture_version = publish_workflow(fixture["workflow"], 0)
		self.fixture_workflow = fixture["workflow"]
		self.fixture_version = fixture_version["version"]
		self.test_workflows.append(self.fixture_workflow)
		self.addCleanup(self._cleanup_test_rows)

	def _cleanup_test_rows(self):
		for event_name in self.test_events:
			frappe.db.delete("Automation Outbox Event", {"name": event_name})
		run_names = set(self.test_runs)
		if self.test_workflows:
			run_names.update(
				frappe.get_all(
					"Automation Run", filters={"workflow": ["in", self.test_workflows]}, pluck="name", limit=0
				)
			)
		for run_name in run_names:
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
		for workflow_name in self.test_workflows:
			versions = frappe.get_all(
				"Automation Workflow Version", filters={"workflow": workflow_name}, pluck="name", limit=0
			)
			if versions:
				frappe.db.delete("Automation Round Robin Cursor", {"workflow_version": ["in", versions]})
			frappe.db.delete("Automation Enrollment Ledger", {"workflow": workflow_name})
			frappe.db.delete("Automation Trigger Subscription", {"workflow": workflow_name})
			frappe.db.delete("Automation Audit Event", {"workflow": workflow_name})
			frappe.db.delete("Automation Workflow Draft", {"workflow": workflow_name})
			frappe.db.delete("Automation Workflow Version", {"workflow": workflow_name})
			frappe.db.delete("Automation Workflow", {"name": workflow_name})
		frappe.db.commit()

	def test_concurrent_outbox_claim(self):
		"""Simulate concurrent worker delivery bursts and verify deadlock handling."""
		# Create a pending outbox event
		event = frappe.get_doc({
			"doctype": "Automation Outbox Event",
			"event_id": frappe.generate_hash(length=20),
			"event_type": "ON_UPDATE",
			"object_doctype": "Lead",
			"object_name": "Test Lead",
			"status": "PENDING",
			"available_at": frappe.utils.now_datetime(),
		}).insert(ignore_permissions=True, ignore_links=True)
		self.test_events.append(event.name)

		frappe.db.commit()
		site_name = frappe.local.site

		# Define worker function
		errors = []

		def worker(results, index):
			try:
				frappe.init(site=site_name)
				frappe.connect()
				row = events._claim_event(lease_owner=f"worker-{index}", event_name=event.name)
				if row:
					frappe.db.commit()
					results.append(f"worker-{index}")
			except Exception as exc:
				errors.append(str(exc))
			finally:
				if getattr(frappe.local, "db", None):
					frappe.db.rollback()
					frappe.destroy()

		results = []
		threads = []
		for i in range(5):
			t = threading.Thread(target=worker, args=(results, i))
			threads.append(t)
			t.start()

		for t in threads:
			t.join()

		# Only one worker should have successfully claimed the event
		self.assertEqual(errors, [])
		self.assertEqual(len(results), 1)

		# Verify the database state
		frappe.db.rollback() # clean up main thread transaction state
		doc = frappe.get_doc("Automation Outbox Event", event.name)
		self.assertEqual(doc.status, "PROCESSING")
		self.assertEqual(doc.lease_owner, results[0])

	def test_concurrent_token_claim(self):
		"""Verify concurrent token claiming does not deadlock."""
		run_name = f"test-run-{frappe.generate_hash(length=12)}"
		frappe.get_doc({
			"doctype": "Automation Run",
			"name": run_name,
			"workflow": self.fixture_workflow,
			"workflow_version": self.fixture_version,
			"record_doctype": "Lead",
			"record_name": "Test",
			"record_key": "Lead:Test",
			"source": "MANUAL",
			"status": "RUNNING",
		}).insert(ignore_permissions=True, ignore_links=True)
		self.test_runs.append(run_name)

		token = frappe.get_doc({
			"doctype": "Automation Run Token",
			"run": run_name,
			"node_id": "trigger-1",
			"status": "READY",
			"available_at": frappe.utils.now_datetime(),
		}).insert(ignore_permissions=True, ignore_links=True)

		frappe.db.commit()
		site_name = frappe.local.site

		errors = []

		def worker(results, index):
			try:
				frappe.init(site=site_name)
				frappe.connect()
				# Try to claim the token using the same logic as execute_token
				token_doc = frappe.get_doc("Automation Run Token", token.name, for_update=True)
				if token_doc.status == "READY":
					frappe.db.set_value(
						"Automation Run Token",
						token_doc.name,
						{"status": "RUNNING", "lease_owner": f"worker-{index}"},
						update_modified=False,
					)
					frappe.db.commit()
					results.append(f"worker-{index}")
			except Exception as exc:
				errors.append(str(exc))
			finally:
				if getattr(frappe.local, "db", None):
					frappe.db.rollback()
					frappe.destroy()

		results = []
		threads = []
		for i in range(5):
			t = threading.Thread(target=worker, args=(results, i))
			threads.append(t)
			t.start()

		for t in threads:
			t.join()

		self.assertEqual(errors, [])
		self.assertEqual(len(results), 1)
		frappe.db.rollback()
		doc = frappe.get_doc("Automation Run Token", token.name)
		self.assertEqual(doc.status, "RUNNING")

	def test_concurrent_round_robin_rotation_uses_one_locked_cursor(self):
		created = create_workflow_record("Concurrent cursor", "Lead")
		self.test_workflows.append(created["workflow"])
		published = publish_workflow(created["workflow"], 0)
		frappe.db.commit()
		site_name = frappe.local.site
		node = {"id": "round-robin", "type": "action.round_robin", "type_version": 2}
		results: list[str] = []
		errors: list[str] = []

		def worker():
			try:
				frappe.init(site=site_name)
				frappe.connect()
				member = engine._next_round_robin_member(
					SimpleNamespace(workflow_version=published["version"]),
					node,
					["first@example.com", "second@example.com"],
				)
				frappe.db.commit()
				results.append(member)
			except Exception as exc:
				errors.append(str(exc))
			finally:
				if getattr(frappe.local, "db", None):
					frappe.db.rollback()
					frappe.destroy()

		threads = [threading.Thread(target=worker) for _ in range(2)]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()
		self.assertEqual(errors, [])
		self.assertEqual(sorted(results), ["first@example.com", "second@example.com"])
		frappe.db.rollback()
		self.assertEqual(
			frappe.db.get_value(
				"Automation Round Robin Cursor",
				{"workflow_version": published["version"], "node_id": "round-robin"},
				"next_index",
			),
			2,
		)
