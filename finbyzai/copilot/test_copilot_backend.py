# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Regression coverage for the 2026-09-17 review findings (see reviews/).

Findings 3 and 6 are concurrency bugs in the run lifecycle and exercise the Redis
ownership boundaries here. Findings 4's permission bypass
is proven against this site's own `sandeep.ambala@finbyz.tech` / Employee
HR-EMP-00002 restriction (see access.py-style tools) and skips itself if that
fixture is ever removed, rather than fabricate ERPNext master data a test run
should not be creating.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from finbyzai.copilot import access, api, runner, sandbox, setup
from finbyzai.copilot.tools.data import permitted_count


class TestCopilotSetup(FrappeTestCase):
	def test_migrate_never_reenables_an_explicit_disable(self):
		"""Finding 5: after_migrate must not flip an admin's own choice back on."""
		original = frappe.db.get_single_value("Copilot Settings", "enabled")
		frappe.db.set_single_value("Copilot Settings", "enabled", 0)
		frappe.db.commit()
		try:
			setup.ensure_defaults()
			self.assertEqual(frappe.db.get_single_value("Copilot Settings", "enabled"), 0)
		finally:
			frappe.db.set_single_value("Copilot Settings", "enabled", original)
			frappe.db.commit()


class TestCopilotConcurrency(FrappeTestCase):
	def setUp(self):
		self.conversation = frappe.get_doc(
			{"doctype": "Copilot Conversation", "title": "test", "user": "Administrator"}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.delete_doc("Copilot Conversation", self.conversation.name, ignore_permissions=True, force=1)

	def test_start_guard_rejects_a_competing_request(self):
		"""Finding 6: only one request can cross the check-and-insert boundary."""
		lock = api._acquire_start_lock(self.conversation.name)
		try:
			with self.assertRaises(Exception):
				api._acquire_start_lock(self.conversation.name)
		finally:
			api._release_lock(lock)

	def test_recover_leaves_a_claimed_run_alone(self):
		"""Finding 3: a run whose worker is still alive must never be failed out
		from under it, no matter how long it has been running."""
		run = frappe.get_doc(
			{"doctype": "Copilot Run", "conversation": self.conversation.name, "status": "Running"}
		).insert(ignore_permissions=True)
		# Old enough to fail the age check on its own — only the claim should save it.
		frappe.db.set_value(
			"Copilot Run",
			run.name,
			"modified",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-3600),
			update_modified=False,
		)
		claim = runner._claim(run.name)
		try:
			api.recover_conversation(self.conversation.name)
			self.assertEqual(frappe.db.get_value("Copilot Run", run.name, "status"), "Running")
		finally:
			runner._release(claim)

	def test_recover_reaps_a_genuinely_stale_run(self):
		"""A run nobody holds and that has had time to be picked up should still
		be recovered — the fix must not make recovery a no-op."""
		run = frappe.get_doc(
			{"doctype": "Copilot Run", "conversation": self.conversation.name, "status": "Running"}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Copilot Run",
			run.name,
			"modified",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-120),
			update_modified=False,
		)
		api.recover_conversation(self.conversation.name)
		self.assertEqual(frappe.db.get_value("Copilot Run", run.name, "status"), "Failed")

	def test_recovery_holds_the_worker_claim_while_marking_failed(self):
		run = frappe.get_doc(
			{"doctype": "Copilot Run", "conversation": self.conversation.name, "status": "Running"}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Copilot Run",
			run.name,
			"modified",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-120),
			update_modified=False,
		)
		real_set_value = frappe.db.set_value

		def assert_claimed(*args, **kwargs):
			if args[:2] == ("Copilot Run", run.name):
				self.assertTrue(runner.is_claimed(run.name))
			return real_set_value(*args, **kwargs)

		with patch.object(frappe.db, "set_value", side_effect=assert_claimed):
			api.recover_conversation(self.conversation.name)
		self.assertTrue(runner.is_claimed(run.name))
		frappe.db.after_rollback.run()
		self.assertFalse(runner.is_claimed(run.name))

	def test_recover_gives_a_fresh_unclaimed_run_a_grace_window(self):
		"""A run enqueued moments ago and not yet claimed by a worker must survive —
		only genuinely old, unclaimed runs are stale."""
		run = frappe.get_doc(
			{"doctype": "Copilot Run", "conversation": self.conversation.name, "status": "Running"}
		).insert(ignore_permissions=True)
		api.recover_conversation(self.conversation.name)
		self.assertEqual(frappe.db.get_value("Copilot Run", run.name, "status"), "Running")


class TestCopilotResourcePermissions(FrappeTestCase):
	def test_require_read_rejects_non_string_names(self):
		with self.assertRaises(frappe.ValidationError):
			access.require_read("Knowledge Base", ["!=", ""])

	def test_require_read_rejects_an_unreadable_record(self):
		doc = frappe._dict(name="Private KB")
		with (
			patch("frappe.get_doc", return_value=doc),
			patch("frappe.has_permission", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			access.require_read("Knowledge Base", doc.name)


class TestCopilotPermissionScopedCount(FrappeTestCase):
	FIXTURE_USER = "sandeep.ambala@finbyz.tech"
	FIXTURE_DOCTYPE = "Employee"

	def _skip_unless_fixture_present(self):
		restricted = frappe.get_all(
			"User Permission",
			filters={"user": self.FIXTURE_USER, "allow": self.FIXTURE_DOCTYPE},
		)
		if not frappe.db.exists("User", self.FIXTURE_USER) or not restricted:
			self.skipTest(
				f"{self.FIXTURE_USER} has no User Permission on {self.FIXTURE_DOCTYPE} on this site"
			)

	def test_permitted_count_is_scoped_below_the_raw_total(self):
		"""Finding 4: frappe.db.count ignores User Permissions; a restricted user's
		count must come back lower than the unrestricted total, never equal to it."""
		self._skip_unless_fixture_present()
		raw_total = frappe.db.count(self.FIXTURE_DOCTYPE, {})
		frappe.set_user(self.FIXTURE_USER)
		try:
			scoped_total = permitted_count(self.FIXTURE_DOCTYPE, {})
		finally:
			frappe.set_user("Administrator")
		self.assertLess(scoped_total, raw_total)

	def test_sandbox_gated_count_matches_permitted_count(self):
		self._skip_unless_fixture_present()
		frappe.set_user(self.FIXTURE_USER)
		try:
			via_sandbox = sandbox._gated_count(self.FIXTURE_DOCTYPE)
			via_tool = permitted_count(self.FIXTURE_DOCTYPE, {})
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(via_sandbox, via_tool)

	def test_sandbox_gated_exists_respects_permissions(self):
		self._skip_unless_fixture_present()
		frappe.set_user(self.FIXTURE_USER)
		try:
			self.assertTrue(sandbox._gated_exists(self.FIXTURE_DOCTYPE))
		finally:
			frappe.set_user("Administrator")
