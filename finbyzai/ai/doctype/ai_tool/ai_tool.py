# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import importlib
import os
import frappe
from frappe.model.document import Document
from langchain_core.tools import BaseTool,Tool, StructuredTool
import sys


class AITool(Document):
    def after_insert(self):
        if not frappe.conf.developer_mode and not self.from_package and self.is_custom:
            return

        file_path = self._get_file_path()
        self.tool_function = self.get_tool_path()

        safe_name = frappe.scrub(self.name)
        boilerplate = f'''# Auto-generated LangChain tool for: {safe_name}
# Module: {self.module}

from typing import Any
from langchain_core.tools import tool

@tool("{safe_name}", return_direct=False)
def {frappe.scrub(self.name)}_tool(input: Any) -> Any:
    """
    {self.description}

    Args:
        input (Any): Input to the tool

    Returns:
        Any: Tool output
    """
    # TODO: Implement your logic here
    return input

'''

        # Only create if it doesn’t already exist
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                f.write(boilerplate)

    def on_update(self):
        """Handle renaming (if the AI Tool name changes)"""
        if not frappe.conf.developer_mode:
            return

        prev_doc = self.get_doc_before_save() or {}
        old_name = prev_doc.get('name')
        if old_name and old_name != self.name:
            old_file = self._get_file_path(old_name)
            new_file = self._get_file_path(self.name)

            if os.path.exists(old_file):
                os.rename(old_file, new_file)

    def on_trash(self):
        """Delete the tool file if AI Tool is deleted"""
        if not frappe.conf.developer_mode:
            return

        file_path = self._get_file_path()
        if os.path.exists(file_path):
            os.remove(file_path)

    def _get_file_path(self, name=None):
        """Helper to get the tool file path inside the correct module"""
        if not name:
            name = self.name

        tool_path = frappe.get_module_path(self.module, 'ai_tools')

        os.makedirs(tool_path, exist_ok=True)

        file_name = f"{frappe.scrub(name)}.py"
        return os.path.join(tool_path, file_name)
    
    def get_tool_path(self):
        """Return the dotted import path to the tool function"""
        if self.from_package:
            dotted = self.tool_function or ""
            if not dotted:
                frappe.throw("For packaged tools, provide full dotted function path in Tool Function")
            return dotted

        app_name = frappe.get_value("Module Def", self.module, 'app_name')
        if not app_name:
            frappe.throw(f"App not found for module '{self.module}'")

        module_name = frappe.scrub(self.module)
        tool_name = frappe.scrub(self.name)
        base_path = f"{app_name}.{module_name}.ai_tools.{tool_name}.{tool_name}_tool"

        return base_path
    
    def get_tool(self):
        tool_path = self.get_tool_path()
        safe_name = frappe.scrub(self.name)
        try:
            module_path, func_name = tool_path.rsplit('.', 1)
            if module_path in sys.modules:
                del sys.modules[module_path]
            mod = __import__(module_path, fromlist=[func_name])
            importlib.reload(mod)
            func = getattr(mod, func_name)

            if isinstance(func,BaseTool):
                return func
            try:
                return StructuredTool.from_function(
                    func=func,
                    name=safe_name,
                    description=self.description or f"Tool: {safe_name}",
                    infer_schema=True,
                )
            except Exception:
                return Tool.from_function(
                    func=func,
                    name=safe_name,
                    description=self.description or f"Tool: {safe_name}"
                )
        except Exception as e:
            frappe.log_error(f"Failed to load tool {self.name}: {e}")
