# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Direct tool authorization tests; no site or database writes required."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from finbyzai.copilot.ai_tools import describe_report, list_reports, run_report


class TestReportPermissions(TestCase):
    def setUp(self):
        self.report = Mock(
            ref_doctype="Customer", report_type="Script Report", disabled=0
        )
        self.report.name = "Customer Summary"
        self.report.is_permitted.return_value = True
        self.get_doc = self.enterContext(patch("frappe.get_doc", return_value=self.report))
        self.permission = self.enterContext(patch("frappe.has_permission", return_value=True))
        self.run = self.enterContext(patch("frappe.desk.query_report.run"))
        self.defaults = self.enterContext(patch.object(run_report, "_apply_filter_defaults", return_value={}))
        self.enterContext(patch("finbyzai.copilot.access._", side_effect=lambda text: text))

    def assert_denied_before_execution(self):
        for tool in (describe_report.describe_report_tool, run_report.run_report_tool):
            with self.subTest(tool=tool.__name__), self.assertRaises(frappe.PermissionError):
                tool(self.report.name)
        self.defaults.assert_not_called()
        self.run.assert_not_called()

    def test_missing_read_permission(self):
        self.permission.side_effect = lambda dt, ptype, **kwargs: ptype != "read"
        self.assert_denied_before_execution()

    def test_missing_report_permission(self):
        self.permission.side_effect = lambda dt, ptype, **kwargs: ptype != "report"
        self.assert_denied_before_execution()

    def test_report_roles_deny_access(self):
        self.report.is_permitted.return_value = False
        self.assert_denied_before_execution()

    def test_disabled_report_is_denied(self):
        self.report.disabled = 1
        self.assert_denied_before_execution()

    def test_custom_report_cannot_bypass_reference_roles(self):
        reference = Mock(ref_doctype="Customer", disabled=0, report_type="Script Report")
        reference.name = "Restricted Customer Summary"
        reference.is_permitted.return_value = False
        self.report.report_type = "Custom Report"
        self.report.reference_report = reference.name
        self.get_doc.side_effect = lambda dt, name: self.report if name == self.report.name else reference
        self.assert_denied_before_execution()

    def test_allowed_report_runs_as_current_user(self):
        self.run.return_value = {"columns": [], "result": []}
        with patch.object(frappe, "session", frappe._dict(user="restricted@example.com")):
            result = run_report.run_report_tool(self.report.name)
        self.run.assert_called_once_with(
            self.report.name, filters={}, user="restricted@example.com", are_default_filters=False
        )
        self.assertEqual(result["row_count"], 0)

    def test_discovery_hides_report_without_read_permission(self):
        self.permission.side_effect = lambda dt, ptype, **kwargs: ptype != "read"
        self.assertFalse(list_reports._report_permitted(self.report.name, "Customer", {}, set()))
