"""Database-free checks for the workflow features backported to version 15."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from pypika import Field

from finbyzai.patches.v15_0.install_workflow_builder_schema import WORKFLOW_DOCTYPES
from finbyzai.workflow_builder import folders
from finbyzai.workflow_builder.api import JSONValue
from finbyzai.workflow_builder.errors import AutomationError


class TestFeaturePort(TestCase):
    def setUp(self):
        self.enterContext(patch.object(folders, "_", side_effect=lambda text: text))

    def test_folder_paths_are_normalized(self):
        self.assertEqual(folders.normalize_folder_path(" Sales / Follow Ups "), "Sales/Follow Ups")
        self.assertEqual(folders.normalize_folder_path(None), "")

    def test_ambiguous_or_overlong_folder_paths_are_rejected(self):
        for path in ("Sales//Follow Ups", "../Sales", "Sales/./New", "x" * 141):
            with self.subTest(path=path), self.assertRaises(AutomationError):
                folders.normalize_folder_path(path)

    def test_folder_creation_links_each_parent(self):
        doc = Mock()
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), patch(
            "frappe.get_doc", return_value=doc
        ) as create:
            self.assertEqual(folders.ensure_folder_path("Sales/New"), "Sales/New")
        self.assertEqual([call.args[0]["path"] for call in create.call_args_list], ["Sales", "Sales/New"])
        self.assertEqual(create.call_args_list[1].args[0]["parent_folder"], "Sales")
        self.assertEqual(doc.insert.call_count, 2)

    def test_concurrent_folder_creation_is_tolerated(self):
        doc = Mock()
        doc.insert.side_effect = frappe.DuplicateEntryError
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), patch(
            "frappe.get_doc", return_value=doc
        ):
            self.assertEqual(folders.ensure_folder_path("Sales"), "Sales")

    def test_nonempty_folders_cannot_be_deleted(self):
        for responses in ([True, True], [True, False, True]):
            with self.subTest(responses=responses), patch.object(
                frappe, "db", Mock(exists=Mock(side_effect=responses))
            ), patch("frappe.delete_doc") as delete, self.assertRaises(AutomationError):
                folders.delete_folder("Sales")
            delete.assert_not_called()

    def test_folder_schema_is_in_the_version15_migration(self):
        self.assertIn("automation_workflow_folder", WORKFLOW_DOCTYPES)

    def test_json_extraction_preserves_both_database_dialects(self):
        for dialect, function in (
            ("mariadb", "JSON_UNQUOTE(JSON_EXTRACT"),
            ("postgres", "jsonb_extract_path_text"),
        ):
            with self.subTest(dialect=dialect), patch.object(frappe, "db", Mock(db_type=dialect)):
                expression = JSONValue(Field("output_json"), "$.selected_handle").get_sql()
            self.assertIn(function, expression)
            self.assertIn("selected_handle", expression)

    def test_json_extraction_rejects_unsupported_nested_paths(self):
        with self.assertRaises(ValueError):
            JSONValue(Field("output_json"), "$.result.value")
