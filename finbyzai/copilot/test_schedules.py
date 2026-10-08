"""Schedule regression checks without database writes or model calls."""

from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from finbyzai.copilot import schedules
from finbyzai.copilot.schedule_time import next_occurrence, schedule_start


class TestScheduleTime(TestCase):
    def test_one_time_and_timezone(self):
        start = schedule_start("2026-10-09T09:00", "Asia/Kolkata")
        self.assertEqual(start.astimezone(timezone.utc).hour, 3)
        self.assertEqual(next_occurrence(start, "Once"), start)
        self.assertIsNone(next_occurrence(start, "Once", after=start.astimezone(timezone.utc)))

    def test_daily_retains_local_time_across_dst(self):
        start = schedule_start("2026-03-07T09:00", "America/New_York")
        due = next_occurrence(start, "Daily", after=start.astimezone(timezone.utc))
        self.assertEqual(due.hour, 9)
        self.assertEqual(due.astimezone(timezone.utc) - start.astimezone(timezone.utc), timedelta(hours=23))

    def test_nonexistent_time_rejected(self):
        with self.assertRaises(ValueError):
            schedule_start("2026-03-08T02:30", "America/New_York")

    def test_offset_and_invalid_zone_rejected(self):
        with self.assertRaises(ValueError):
            schedule_start("2026-10-09T09:00+01:00", "Europe/Zurich")
        with self.assertRaises(KeyError):
            schedule_start("2026-10-09T09:00", "not/a-zone")

    def test_month_end_is_anchored_to_original_day(self):
        start = schedule_start("2026-01-31T09:00", "UTC")
        feb = next_occurrence(start, "Monthly", after=start)
        self.assertEqual(feb.day, 28)
        march = next_occurrence(start, "Monthly", after=feb)
        self.assertEqual((march.month, march.day), (3, 31))

    def test_missed_occurrences_are_skipped(self):
        start = schedule_start("2026-01-01T09:00", "UTC")
        after = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        self.assertEqual(next_occurrence(start, "Daily", after=after).day, 9)
        self.assertEqual(next_occurrence(start, "Weekly", after=after).day, 15)
        self.assertEqual(next_occurrence(start, "Hourly", after=after).hour, 13)

    def test_cron_lower_bound_is_inclusive(self):
        start = schedule_start("2026-10-09T09:00", "Asia/Kolkata")
        self.assertEqual(next_occurrence(start, "Custom", "0 9 * * 1-5"), start)
        due = next_occurrence(start, "Custom", "0 9 * * 1-5", after=start.astimezone(timezone.utc))
        self.assertEqual(due.day, 12)

    def test_storage_time_keeps_repeated_hour_unambiguous(self):
        first = schedule_start("2026-11-01T01:30", "America/New_York")
        second = first.replace(fold=1)
        self.assertEqual(schedules.to_storage_time(second) - schedules.to_storage_time(first), timedelta(hours=1))
        self.assertTrue(schedules.to_iso(schedules.to_storage_time(second)).endswith("+00:00"))

    def test_invalid_repeat_and_cron(self):
        start = schedule_start("2026-10-09T09:00", "UTC")
        for repeat, expression in [("Invalid", None), ("Custom", "* * * * * *"), ("Custom", "bad")]:
            with self.subTest(repeat=repeat, expression=expression), self.assertRaises(ValueError):
                next_occurrence(start, repeat, expression)


class TestScheduleOwnership(TestCase):
    def test_other_users_task_is_denied(self):
        with patch.object(frappe, "session", frappe._dict(user="alice")), patch.object(
            frappe, "get_doc", return_value=frappe._dict(owner="bob")
        ):
            with self.assertRaises(frappe.PermissionError):
                schedules.own_task("task")

    def test_save_whitelists_fields(self):
        doc = Mock()
        doc.name, doc.next_run, doc.timezone = "task", None, "UTC"
        with patch.object(schedules.access, "require_copilot"), patch.object(
            frappe, "new_doc", return_value=doc
        ):
            schedules.save_task({"title": "Test", "owner": "bob", "last_run": "forged"})
        doc.update.assert_called_once_with({"title": "Test"})
        doc.save.assert_called_once()


class TestScheduleExecution(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 9, 9)
        self.doc = frappe._dict(
            name="task", owner="alice", enabled=1, completed=0, next_run=self.now,
            title="Daily summary", prompt="Summarize overdue invoices", recurrence="Once",
            timezone="UTC", scheduled_for="2026-10-09T09:00", last_run=None,
        )
        self.db = Mock()
        self.db.get_value.return_value = 1
        self.enterContext(patch.object(frappe, "db", self.db))
        self.enterContext(patch.object(frappe, "session", frappe._dict(user="Administrator")))
        self.set_user = self.enterContext(patch.object(frappe, "set_user"))
        self.enterContext(patch.object(schedules, "utc_now", return_value=self.now.replace(tzinfo=timezone.utc)))
        self.enterContext(patch.object(schedules.runner, "get_settings", return_value=frappe._dict(enabled=1)))
        self.enterContext(patch.object(schedules.access, "require_copilot"))
        self.enterContext(patch.object(schedules.access, "require_read"))
        conversation = Mock()
        conversation.insert.return_value = frappe._dict(name="conversation")
        self.get_doc = self.enterContext(patch.object(frappe, "get_doc", side_effect=lambda *a, **k: conversation if isinstance(a[0], dict) else self.doc))
        self.start = self.enterContext(patch("finbyzai.copilot.api.start_run", return_value={"run": "run", "conversation": "conversation"}))
        self.enterContext(patch.object(frappe, "log_error"))
        self.enterContext(patch.object(frappe, "get_traceback", return_value="test"))

    def test_run_and_advance_are_committed_together(self):
        calls = []
        self.start.side_effect = lambda *a, **k: calls.append("start") or {"run": "run", "conversation": "conversation"}
        self.db.set_value.side_effect = lambda *a, **k: calls.append("advance")
        self.db.commit.side_effect = lambda: calls.append("commit")
        schedules.execute_task("task")
        self.assertEqual(calls, ["start", "advance", "commit"])
        self.assertEqual(self.db.set_value.call_args.args[2]["completed"], 1)
        self.get_doc.assert_any_call(schedules.DOCTYPE, "task", for_update=True)
        self.set_user.assert_any_call("alice")
        self.assertEqual(self.set_user.call_args.args, ("Administrator",))

    def test_duplicate_dispatch_does_not_repeat_completed_task(self):
        self.db.set_value.side_effect = lambda dt, name, values: self.doc.update(values)
        schedules.execute_task("task")
        schedules.execute_task("task")
        self.assertEqual(self.start.call_count, 1)

    def test_paused_and_running_runs_block_next_occurrence(self):
        self.doc.last_run = "previous"
        for status in ("Paused", "Running"):
            self.db.get_value.return_value = status
            schedules.execute_task("task")
        self.start.assert_not_called()

    def test_paused_deleted_or_future_schedule_does_not_start(self):
        self.doc.enabled = 0
        schedules.execute_task("task")
        self.doc.enabled = 1
        self.doc.next_run += timedelta(days=1)
        schedules.execute_task("task")
        self.get_doc.side_effect = frappe.DoesNotExistError
        schedules.execute_task("task")
        self.start.assert_not_called()

    def test_missing_resource_is_reported_instead_of_retried_forever(self):
        schedules.access.require_read.side_effect = frappe.DoesNotExistError("Agent removed")
        schedules.execute_task("task")
        self.start.assert_not_called()
        values = self.db.set_value.call_args.args[2]
        self.assertEqual(values["enabled"], 0)
        self.assertIn("Agent removed", values["error"])

    def test_disabled_owner_is_rejected_and_task_paused(self):
        self.db.get_value.return_value = 0
        schedules.execute_task("task")
        self.start.assert_not_called()
        self.db.rollback.assert_called_once()
        self.assertEqual(self.db.set_value.call_args.args[2]["enabled"], 0)


class TestScheduleDispatch(TestCase):
    def test_query_excludes_busy_runs_and_enqueues_after_commit(self):
        from frappe.query_builder.builder import MariaDB

        db = Mock()
        db.sql.return_value = ["task"]
        db.format_datetime.side_effect = lambda value: value.isoformat(sep=" ")
        with patch.object(frappe, "db", db), patch.object(frappe, "qb", MariaDB), patch.object(
            frappe, "local", frappe._dict(db=db, flags=frappe._dict())
        ), patch.object(frappe, "enqueue") as enqueue, patch.object(
            schedules.runner, "get_settings", return_value=frappe._dict(enabled=1)
        ):
            schedules.dispatch_due_tasks()
        query = db.sql.call_args.args[0]
        params = db.sql.call_args.args[1]
        self.assertIn("LEFT JOIN", query)
        self.assertIn("NOT IN", query)
        self.assertIn("Running", params.values())
        self.assertIn("Paused", params.values())
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
        self.assertTrue(enqueue.call_args.kwargs["deduplicate"])

    def test_disabled_copilot_does_not_dispatch(self):
        with patch.object(schedules.runner, "get_settings", return_value=frappe._dict(enabled=0)), patch.object(frappe, "enqueue") as enqueue:
            schedules.dispatch_due_tasks()
        enqueue.assert_not_called()
