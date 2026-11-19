from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from finbyzai.workflow_builder import trigger_groups
from finbyzai.workflow_builder.trigger_groups import all_triggers_satisfied, match_settings


class FakeDb:
	"""Small in-memory stand-in for the Trigger Match table."""

	def __init__(self):
		self.rows: dict[str, dict] = {}

	def delete(self, doctype, filters):
		group_ids = filters.get("trigger_group_id", [None, []])[1] if "trigger_group_id" in filters else None
		for name in list(self.rows):
			row = self.rows[name]
			if group_ids is not None and row["trigger_group_id"] not in group_ids:
				continue
			if "matched_at" in filters and not row["matched_at"] < filters["matched_at"][1]:
				continue
			self.rows.pop(name)

	def get_value(self, doctype, filters, fieldname, for_update=False):
		return next((n for n, r in self.rows.items() if r["trigger_group_id"] == filters["trigger_group_id"]), None)

	def set_value(self, doctype, name, field, value, update_modified=True):
		self.rows[name][field] = value


class TriggerGroupTests(TestCase):
	def setUp(self):
		self.db = FakeDb()
		self.counter = 0
		patcher = patch.object(frappe, "db", self.db)
		patcher.start()
		self.addCleanup(patcher.stop)

		def insert_doc(values):
			doc = MagicMock()

			def insert(ignore_permissions=False):
				self.counter += 1
				self.db.rows[f"m{self.counter}"] = dict(values)

			doc.insert = insert
			return doc

		import datetime

		patch.object(trigger_groups, "now_datetime", lambda: datetime.datetime(2026, 11, 19, 12, 0)).start()
		patch.object(frappe, "get_doc", insert_doc).start()
		patch.object(frappe, "get_all", lambda *a, **k: [r["trigger_group_id"] for r in self.db.rows.values()]).start()
		self.addCleanup(patch.stopall)

	def config(self, group, **extra):
		return {"_trigger_group_id": group, "_trigger_group_ids": ["a", "b"], "_match_mode": "all", **extra}

	def test_any_mode_always_enrolls(self):
		self.assertTrue(all_triggers_satisfied({"_trigger_group_id": "a"}, "WF", "Lead", "L1"))

	def test_all_mode_waits_until_every_trigger_fired(self):
		self.assertFalse(all_triggers_satisfied(self.config("a"), "WF", "Lead", "L1"))
		self.assertFalse(all_triggers_satisfied(self.config("a"), "WF", "Lead", "L1"))
		self.assertTrue(all_triggers_satisfied(self.config("b"), "WF", "Lead", "L1"))
		self.assertEqual(self.db.rows, {})

	def test_match_settings_defaults_and_limits(self):
		self.assertEqual(match_settings(None), {"mode": "any", "window_hours": 0})
		self.assertEqual(match_settings({"match_mode": "ALL", "match_window_hours": 99999})["window_hours"], trigger_groups.MAX_WINDOW_HOURS)
