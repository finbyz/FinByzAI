"""Metric aggregation may retry its transaction without replaying a workflow."""
import sqlite3
from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
from finbyzai.workflow_builder import observability as metrics


class MetricDeltaTest(TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.addCleanup(self.connection.close)
        self.connection.execute('CREATE TABLE deltas (name TEXT PRIMARY KEY, creation TEXT, modified TEXT, owner TEXT, modified_by TEXT, metric_date TEXT, workflow TEXT, workflow_version TEXT, metric TEXT, amount REAL)')
        self.connection.execute('CREATE TABLE totals (metric TEXT PRIMARY KEY, amount REAL)')
        self.db = MagicMock()
        self.db.bulk_insert.side_effect = lambda doctype, fields, rows: self.connection.executemany(
            'INSERT INTO deltas VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
        self.db.commit.side_effect = self.connection.commit
        self.db.rollback.side_effect = self.connection.rollback
        self.db.delete.side_effect = self.delete
        self.query = MagicMock()
        self.query.run.side_effect = self.pending
        for item in (patch.object(frappe.local, 'db', self.db, create=True),
                     patch.object(frappe.local, 'session', frappe._dict(user='Administrator'), create=True),
                     patch.object(metrics, 'now_datetime', return_value=datetime(2026, 11, 25, 23, 59)),
                     patch.object(frappe.qb, 'get_query', return_value=self.query),
                     patch.object(metrics, 'filelock', MagicMock()),
                     patch.object(metrics, 'sleep')):
            item.start()
            self.addCleanup(item.stop)

    def pending(self, **kwargs):
        return [frappe._dict(name=r[0], metric_date=r[1], workflow=r[2], workflow_version=r[3], metric=r[4], amount=r[5])
                for r in self.connection.execute('SELECT name,metric_date,workflow,workflow_version,metric,amount FROM deltas ORDER BY rowid LIMIT 200')]

    def delete(self, doctype, filters):
        for name in filters['name'][1]:
            self.connection.execute('DELETE FROM deltas WHERE name=?', (name,))

    def apply(self, metric_date, workflow, version, metric, amount):
        self.assertEqual(str(metric_date), '2026-11-25')
        self.connection.execute('INSERT INTO totals VALUES (?,?) ON CONFLICT(metric) DO UPDATE SET amount=amount+excluded.amount', (metric, amount))

    def test_capture_is_transactional_and_does_not_read_or_lock_daily_totals(self):
        metrics.increment_metric('W', 'V', 'completed_runs')
        metrics.increment_metric('W', 'V', 'completed_runs')
        self.assertEqual(len(self.pending()), 2)
        self.db.get_value.assert_not_called()
        self.db.set_value.assert_not_called()
        self.db.commit.assert_not_called()
        self.db.rollback.assert_not_called()
        self.connection.rollback()
        self.assertEqual(self.pending(), [])

    def test_conflict_rolls_back_totals_and_consumption_then_retries_exactly_once(self):
        metrics.increment_metric('W', 'V', 'completed_runs', 2)
        metrics.increment_metric('W', 'V', 'completed_runs', 3)
        metrics.increment_metric('W', 'V', 'total_duration_seconds', 1.5)
        self.connection.commit()
        calls = []
        def fail_first_delete(doctype, filters):
            calls.append(1)
            if len(calls) == 1:
                raise frappe.QueryDeadlockError('1020')
            self.delete(doctype, filters)
        self.db.delete.side_effect = fail_first_delete
        with patch.object(metrics, '_apply_metric_delta', side_effect=self.apply):
            metrics.aggregate_metric_deltas()
            metrics.aggregate_metric_deltas()
        self.assertEqual(dict(self.connection.execute('SELECT metric,amount FROM totals')), {'completed_runs': 5.0, 'total_duration_seconds': 1.5})
        self.assertEqual(self.pending(), [])
        self.db.rollback.assert_called_once()
        self.assertEqual(len(calls), 2)

    def test_exhausted_retries_retain_deltas_and_do_not_leave_partial_totals(self):
        metrics.increment_metric('W', 'V', 'completed_runs')
        self.connection.commit()
        self.db.delete.side_effect = frappe.QueryDeadlockError('1020')
        with patch.object(metrics, '_apply_metric_delta', side_effect=self.apply), self.assertRaises(frappe.QueryDeadlockError):
            metrics.aggregate_metric_deltas()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(list(self.connection.execute('SELECT * FROM totals')), [])
        self.assertEqual(self.db.rollback.call_count, metrics.METRIC_WRITE_ATTEMPTS)

    def test_invalid_metrics_cannot_be_queued(self):
        with self.assertRaises(ValueError):
            metrics.increment_metric('W', 'V', 'untrusted_column')
        self.assertEqual(self.pending(), [])


class MetricDeltaDatabaseTest(TestCase):
    def test_real_schema_aggregates_deltas_and_rolls_back_with_the_business_transaction(self):
        if frappe.db.db_type != 'mariadb' or not frappe.db.table_exists(metrics.METRIC_DELTA):
            self.skipTest('Requires the migrated MariaDB test site')
        if frappe.db.count(metrics.METRIC_DELTA):
            self.skipTest('Do not consume unrelated pending metrics')
        version = frappe.db.get_value('Automation Workflow Version', {}, ['name', 'workflow'], as_dict=True)
        if not version:
            self.skipTest('Requires a workflow version')
        date = '2099-11-26'
        if frappe.db.exists('Automation Metric Daily', {'workflow': version.workflow, 'metric_date': date}):
            self.skipTest('Test date already has metrics')
        try:
            with patch.object(metrics, 'now_datetime', return_value=datetime(2099, 11, 26, 12)):
                metrics.increment_metric(version.workflow, version.name, 'completed_runs', 2)
                metrics.increment_metric(version.workflow, version.name, 'completed_runs', 3)
                metrics.increment_metric(version.workflow, version.name, 'total_duration_seconds', 1.5)
            self.assertEqual(metrics._aggregate_metric_batch(), 3)
            self.assertEqual(metrics._aggregate_metric_batch(), 0)
            daily = frappe.db.get_value('Automation Metric Daily', {'workflow': version.workflow, 'metric_date': date},
                                       ['completed_runs', 'total_duration_seconds'], as_dict=True)
            self.assertEqual(daily.completed_runs, 5)
            self.assertEqual(daily.total_duration_seconds, 1.5)
        finally:
            frappe.db.rollback()
        self.assertFalse(frappe.db.exists('Automation Metric Daily', {'workflow': version.workflow, 'metric_date': date}))
        self.assertEqual(frappe.db.count(metrics.METRIC_DELTA), 0)
