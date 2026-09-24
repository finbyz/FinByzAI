# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Permission boundaries for generated code; no site or database required."""

from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from finbyzai.copilot import sandbox
from finbyzai.copilot.ai_tools.run_query import run_query_tool


class TestComputePermissions(TestCase):
    def setUp(self):
        self.enterContext(patch.object(frappe, "session", frappe._dict(user="restricted@example.com")))

    def test_generated_code_cannot_override_permissions(self):
        with patch("frappe.get_list") as query, patch.object(sandbox, "safe_exec_flags", nullcontext):
            for option in (
                "ignore_permissions=True", "user='Administrator'", "ignore_ddl=True",
                "run=False", "ignore_ifnull=True",
            ):
                with self.subTest(option=option), self.assertRaises(TypeError):
                    sandbox.execute_code(f'result = frappe.get_list("Customer", {option})')
        query.assert_not_called()

    def test_list_query_is_bound_to_current_user(self):
        with patch("frappe.get_list", return_value=[]) as query:
            sandbox._gated_get_list("Customer", fields=["name"], limit=10)
        self.assertEqual(query.call_args.kwargs["user"], "restricted@example.com")
        self.assertIs(query.call_args.kwargs["ignore_permissions"], False)

    def test_document_read_applies_field_permissions_before_serialization(self):
        doc = Mock()
        with patch("frappe.get_doc", return_value=doc):
            sandbox._gated_get_doc("Customer", "CUST-1")
        self.assertEqual(
            [call[0] for call in doc.mock_calls],
            ["check_permission", "apply_fieldlevel_read_permissions", "as_dict"],
        )

    def test_denied_document_is_not_serialized(self):
        doc = Mock()
        doc.check_permission.side_effect = frappe.PermissionError
        with patch("frappe.get_doc", return_value=doc), self.assertRaises(frappe.PermissionError):
            sandbox._gated_get_doc("Customer", "CUST-1")
        doc.as_dict.assert_not_called()

    def test_system_manager_cannot_bypass_permissions_with_sql(self):
        with patch("frappe.get_roles", return_value=["System Manager"]), patch.object(frappe, "db", Mock()) as db:
            with self.assertRaises(frappe.PermissionError):
                run_query_tool("select * from `tabCustomer`")
        db.sql.assert_not_called()
