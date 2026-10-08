"""Regression checks for the Copilot settings port, without a site or API calls."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from finbyzai.ai.doctype.ai_agent.ai_agent import AIAgent
from finbyzai.copilot import access, api, boot
from finbyzai.copilot.doctype.copilot_user_settings import copilot_user_settings


class TestVersion16Compatibility(TestCase):
    def test_model_picker_uses_the_existing_embedding_field(self):
        settings = frappe._dict(available_models=[], default_model=None)
        with patch("frappe.get_list", return_value=[]) as query:
            self.assertEqual(api._model_rows(settings), [])
        self.assertEqual(query.call_args.kwargs["filters"], {"enabled": 1, "embeding_model": 0})

    def test_missing_optional_picker_permission_returns_empty(self):
        with patch("frappe.clear_last_message"), patch("frappe.log_error"), patch(
            "frappe.get_traceback", return_value="denied"
        ):
            self.assertEqual(api._picker(Mock(side_effect=frappe.PermissionError), "model"), [])

    def test_old_settings_keep_all_models_available(self):
        self.assertIsNone(api._configured_names(frappe._dict(), "available_models"))
        rows = [frappe._dict(name="one"), frappe._dict(name="two")]
        self.assertEqual(api._apply_allowlist(rows, None), rows)
        self.assertEqual(api._apply_allowlist(rows, ["two"]), rows[1:])

    def test_user_settings_permission_checks_the_requested_user(self):
        doc = frappe._dict(user="other@example.com")
        with patch.object(frappe, "session", frappe._dict(user="admin@example.com")), patch(
            "frappe.get_roles", side_effect=lambda user: ["Copilot Admin"] if user == "admin@example.com" else ["Copilot User"]
        ), patch.object(frappe, "db", Mock(escape=lambda user: repr(user))):
            self.assertFalse(copilot_user_settings.has_permission(doc, user="employee@example.com"))
            self.assertIn("employee@example.com", copilot_user_settings.get_permission_query_conditions("employee@example.com"))

    def test_guest_cannot_enable_copilot(self):
        with patch("frappe.get_roles") as roles:
            self.assertFalse(access.has_copilot("Guest"))
        roles.assert_not_called()

    def test_boot_failure_disables_the_panel(self):
        info = frappe._dict()
        with patch.object(access, "has_copilot", side_effect=RuntimeError("unavailable")):
            boot.boot_session(info)
        self.assertFalse(info.copilot_enabled)
        self.assertFalse(info.copilot_admin)

    def test_iteration_default_preserves_supported_sampling_values(self):
        for temperature in (0, 2):
            with self.subTest(temperature=temperature):
                doc = frappe._dict(
                    llm=None, agent_type="Gemini Cache Agent", gemini_cache=None,
                    tools=[], messages=[], enable_memory=0, memory_type=None,
                    max_iterations=0, temperature=temperature, max_tokens=128,
                )
                AIAgent.validate(doc)
                self.assertEqual(doc.max_iterations, 25)
                self.assertEqual(doc.temperature, temperature)
                self.assertEqual(doc.max_tokens, 128)
