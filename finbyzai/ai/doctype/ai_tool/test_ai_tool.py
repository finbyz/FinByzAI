# Copyright (c) 2025, Finbyz Tech Pvt Ltd and Contributors
# See license.txt

import json
from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from finbyzai.ai.agent.builtin_tools import BuiltinToolCompiler


class TestAITool(FrappeTestCase):
	def _compiler(self, *, configuration=None, model="gemini/gemini-3.6-flash"):
		mapping = SimpleNamespace(
			provider="Google",
			model_pattern=r"^gemini/gemini-3.*",
			transport_strategy="LiteLLM Parameter",
			enabled=1,
			request_template=json.dumps(
				{"web_search_options": {}}
			),
			response_mapping="{}",
			compatibility_rules="{}",
		)
		tool = SimpleNamespace(
			name="Web Search",
			tool_type="Provider Built-in",
			tool_key="web_search",
			enabled=1,
			configuration_schema=json.dumps(
				{
					"type": "object",
					"properties": {},
					"additionalProperties": False,
				}
			),
			provider_mappings=[mapping],
		)
		agent = SimpleNamespace(
			agent_type="ReAct Agent",
			output_schema=None,
			tools=[
				SimpleNamespace(
					tool="Web Search",
					enabled=1,
					configuration=json.dumps(configuration or {}),
				)
			],
		)
		llm = SimpleNamespace(name=model, provider="Google")
		return BuiltinToolCompiler(
			agent,
			llm,
			tool_loader=lambda _name: tool,
		)

	def test_compiler_enables_google_search(self):
		compiled = self._compiler().compile()
		self.assertEqual(
			compiled.as_model_kwargs(),
			{"web_search_options": {}},
		)

	def test_compiler_rejects_unsupported_google_search_options(self):
		with self.assertRaisesRegex(Exception, "Invalid configuration"):
			self._compiler(configuration={"search_context_size": "high"}).compile()

	def test_compiler_rejects_unsupported_model(self):
		with self.assertRaisesRegex(Exception, "does not support model"):
			self._compiler(model="gemini/gemini-2.5-flash").compile()

	def test_compiler_rejects_invalid_configuration(self):
		with self.assertRaisesRegex(Exception, "Invalid configuration"):
			self._compiler(configuration={"search_context_size": "huge"}).compile()

	def test_function_tool_is_not_compiled_as_provider_tool(self):
		agent = SimpleNamespace(
			agent_type="ReAct Agent",
			output_schema=None,
			tools=[SimpleNamespace(tool="CRM Context", enabled=1)],
		)
		llm = SimpleNamespace(name="gemini/gemini-3.6-flash", provider="Google")
		function_tool = SimpleNamespace(
			name="CRM Context",
			tool_type="Function",
			enabled=1,
		)

		compiled = BuiltinToolCompiler(
			agent,
			llm,
			tool_loader=lambda _name: function_tool,
		).compile()

		self.assertEqual(compiled.as_model_kwargs(), {})
		self.assertEqual(compiled.tool_keys, [])
