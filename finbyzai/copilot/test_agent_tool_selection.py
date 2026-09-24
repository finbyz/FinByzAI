# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Admin tool selection is enforced without database or model calls."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from finbyzai.copilot import agent, registry, runner, setup


class TestAgentToolSelection(TestCase):
    def setUp(self):
        registry.load_tools()
        self.enterContext(patch.object(frappe, "local", frappe._dict()))

    def test_native_registry_has_one_implementation_per_ai_tool(self):
        self.assertEqual(set(registry.TOOLS), set(registry.TOOL_MODULES))
        for name, spec in registry.TOOLS.items():
            with self.subTest(tool=name):
                self.assertEqual(spec["fn"].__module__, f"finbyzai.copilot.ai_tools.{name}")
                self.assertEqual(spec["fn"].__name__, f"{name}_tool")

    def test_no_agent_or_empty_agent_has_no_tools(self):
        self.assertEqual(agent.tools(None, "Knowledge Base"), [])
        self.assertEqual(agent.tools(frappe._dict(tools=[])), [])

    def test_disabled_tool_is_not_loaded(self):
        row = frappe._dict(tool="read", enabled=0)
        with patch.object(agent.access, "require_read") as require_read:
            self.assertEqual(agent.tools(frappe._dict(tools=[row])), [])
        require_read.assert_not_called()

        row.enabled = 1
        doc = frappe._dict(enabled=0)
        with patch.object(agent.access, "require_read", return_value="read"), patch(
            "frappe.get_doc", return_value=doc
        ):
            self.assertEqual(agent.tools(frappe._dict(tools=[row])), [])

    def test_only_selected_builtin_is_loaded(self):
        selected = frappe._dict(tools=[frappe._dict(tool="read")])
        doc = Mock()
        doc.name = "read"
        doc.requires_confirmation = 1
        doc.get_tool.return_value = registry.langchain_tools(["read"])[0]
        doc.get_tool_path.return_value = "finbyzai.copilot.ai_tools.read.read_tool"
        with patch.object(agent.access, "require_read", return_value="read"), patch("frappe.get_doc", return_value=doc):
            tools = agent.tools(selected)
        self.assertEqual([tool.name for tool in tools], ["read"])
        self.assertEqual(agent.confirming_tools({tools[0].name: tools[0]}), {"read"})

    def test_unselected_builtin_cannot_execute(self):
        frappe.local.copilot_tools = {}
        with patch.object(registry, "call") as call:
            result = runner._call_tool("delete", {"doctype": "Customer"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "PermissionError")
        call.assert_not_called()

    def test_selected_builtin_keeps_guarded_execution(self):
        frappe.local.copilot_tools = {"read": Mock()}
        with patch.object(registry, "call", return_value={"ok": True}) as call:
            self.assertTrue(runner._call_tool("read", {"doctype": "Customer"})["ok"])
        call.assert_called_once_with("read", {"doctype": "Customer"})

    def test_empty_agent_clears_previous_tools_and_does_not_bind(self):
        frappe.local.copilot_tools = {"delete": Mock()}
        model = Mock()
        with patch.object(agent, "model", return_value=model), patch.object(agent, "tools", return_value=[]):
            self.assertIs(runner._bound_model(None, Mock()), model)
        self.assertEqual(frappe.local.copilot_tools, {})
        model.bind_tools.assert_not_called()

    def test_migration_does_not_attach_tools(self):
        with (
            patch.object(setup, "_ensure_role_permissions"),
            patch.object(setup, "_ensure_agent"),
            patch.object(setup, "_ensure_suggestion_agent"),
            patch.object(setup, "sync_ai_tools"),
            patch.object(setup, "_ensure_settings"),
            patch("frappe.get_doc") as get_doc,
        ):
            setup.ensure_defaults()
        get_doc.assert_not_called()

    def test_resumed_approval_checks_new_selection(self):
        frappe.local.copilot_tools = {}
        doc = Mock()
        doc.pending_call = {"name": "delete", "id": "old-call", "arguments": {}}
        doc.decision = {"decision": "approve"}
        with patch.object(runner, "_run_tool", side_effect=lambda doc, call_id, name, args: runner._call_tool(name, args)), patch.object(registry, "call") as call:
            outcome = runner._resolve_pending_call(doc)
        self.assertIn("delete", outcome)
        call.assert_not_called()
