import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.ai_authoring import (
	CONFIGURED_VALUE_SENTINEL,
	_authoring_suggestions,
	_normalise_graph,
	_parse_response,
	_response_schema,
	_safe_current_graph,
	generate_draft,
)
from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.schema import empty_graph


class TestWorkflowAIAuthoring(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.workflow = SimpleNamespace(
			name="AWF-AI-TEST",
			primary_doctype="Lead",
			execution_user="Administrator",
			check_permission=Mock(),
		)
		self.catalog = [
			{
				"type": "trigger.manual",
				"type_version": 1,
				"label": "Manual enrollment",
				"description": "Enroll manually",
				"default_config": {},
				"output_paths": [],
			},
			{
				"type": "action.add_comment",
				"type_version": 1,
				"label": "Add comment",
				"description": "Add a comment",
				"default_config": {"content": ""},
				"output_paths": ["comment"],
			},
		]

	def _response(self, *, graph=None):
		graph = graph or {
			"start_node_id": "start",
			"nodes": [
				{"id": "start", "type": "trigger.manual", "config": {}},
				{"id": "comment", "type": "action.add_comment", "config": {"content": "Follow up"}},
			],
			"edges": [{"source": "start", "source_handle": "default", "target": "comment"}],
		}
		return SimpleNamespace(content=json.dumps({
			"summary": "Add a follow-up comment after manual enrollment.",
			"assumptions": ["An operator enrolls the Lead."],
			"warnings": [],
			"graph": graph,
		}), usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})

	def test_response_schema_rejects_unknown_node_types(self):
		schema = _response_schema(["trigger.manual"])
		with self.assertRaisesRegex(AutomationError, "schema validation"):
			_parse_response(
				SimpleNamespace(content=json.dumps({
					"summary": "Unsafe",
					"assumptions": [],
					"warnings": [],
					"graph": {
						"start_node_id": "start",
						"nodes": [{"id": "start", "type": "action.unknown", "config": {}}],
						"edges": [],
					},
				})),
				schema,
			)

	def test_authoring_suggestions_follow_the_workflow_doctype(self):
		lead = _authoring_suggestions("Lead", "Administrator")
		customer = _authoring_suggestions("Customer", "Administrator")
		issue = _authoring_suggestions("Issue", "Administrator")

		self.assertEqual(len(lead), 3)
		self.assertEqual(len(customer), 3)
		self.assertEqual(len(issue), 3)
		self.assertNotEqual(lead, customer)
		self.assertNotEqual(customer, issue)
		self.assertTrue(any("Qualified" in suggestion for suggestion in lead))
		self.assertTrue(any("order" in suggestion for suggestion in customer))
		self.assertTrue(any("Issue" in suggestion for suggestion in issue))
		self.assertFalse(any("Customer" in suggestion for suggestion in lead))

	def test_normalisation_uses_server_versions_defaults_and_doctype(self):
		definitions = {row["type"]: row for row in self.catalog}
		graph = _normalise_graph(
			{
				"start_node_id": "start",
				"nodes": [
					{"id": "start", "type": "trigger.manual", "config": {}},
					{"id": "comment", "type": "action.add_comment", "config": {}, "placeholder": True},
				],
				"edges": [{"source": "start", "source_handle": "default", "target": "comment"}],
			},
			self.workflow,
			definitions,
		)
		self.assertEqual(graph["primary_doctype"], "Lead")
		self.assertEqual(graph["nodes"][1]["config"], {"content": ""})
		self.assertTrue(graph["nodes"][1]["placeholder"])
		self.assertEqual(graph["edges"][0]["id"], "ai-edge-1")

	def test_current_graph_masks_and_restores_configured_literals(self):
		current = {
			"nodes": [{"id": "comment", "type": "action.add_comment", "config": {"content": "private text", "recipient": {"kind": "literal", "value": "person@example.com"}, "api_secret": "hidden"}}],
			"edges": [],
			"start_node_id": "comment",
		}
		safe = _safe_current_graph(current)
		self.assertEqual(safe["nodes"][0]["config"]["recipient"]["value"], CONFIGURED_VALUE_SENTINEL)
		self.assertEqual(safe["nodes"][0]["config"]["api_secret"], CONFIGURED_VALUE_SENTINEL)
		self.assertNotIn("person@example.com", json.dumps(safe))
		self.assertNotIn("private text", json.dumps(safe))
		definitions = {row["type"]: row for row in self.catalog}
		graph = _normalise_graph(
			{"start_node_id": "comment", "nodes": [{"id": "comment", "type": "action.add_comment", "config": {"content": "private text", "recipient": {"kind": "literal", "value": CONFIGURED_VALUE_SENTINEL}, "api_secret": CONFIGURED_VALUE_SENTINEL}}], "edges": []},
			self.workflow,
			definitions,
			current,
		)
		self.assertEqual(graph["nodes"][0]["config"]["recipient"]["value"], "person@example.com")
		self.assertEqual(graph["nodes"][0]["config"]["api_secret"], "hidden")

	def test_generate_returns_non_mutating_validated_proposal(self):
		llm = Mock()
		llm.model_copy.return_value = llm
		llm.invoke.return_value = self._response()
		agent = SimpleNamespace(temperature=0, max_tokens=1024)
		model = SimpleNamespace(name="Authoring Model", provider="Provider", llm=llm)
		current = empty_graph("Lead", "trigger.manual")
		with (
			patch("finbyzai.workflow_builder.ai_authoring.frappe.get_doc", return_value=self.workflow),
			patch("finbyzai.workflow_builder.ai_authoring._authoring_agent", return_value=(agent, model)),
			patch("finbyzai.workflow_builder.ai_authoring._safe_catalog", return_value=(self.catalog, [], [])),
			patch("finbyzai.workflow_builder.ai_authoring._rate_limit"),
			patch("finbyzai.workflow_builder.ai_authoring.authoring.create_audit") as audit,
		):
			result = generate_draft(
				self.workflow.name,
				"When manually enrolled, add a follow-up comment.",
				current,
			)
		self.assertFalse(result["mutated"])
		self.assertFalse(result["published"])
		self.assertEqual(result["graph"]["primary_doctype"], "Lead")
		self.assertEqual(result["node_count"], 2)
		self.assertEqual(result["usage"], {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
		self.assertFalse(result["issues"])
		self.assertEqual(current, empty_graph("Lead", "trigger.manual"))
		self.assertEqual(audit.call_count, 2)

	def test_generate_rejects_unsafe_cycle_instead_of_returning_it(self):
		llm = Mock()
		llm.model_copy.return_value = llm
		llm.invoke.return_value = self._response(graph={
			"start_node_id": "start",
			"nodes": [
				{"id": "start", "type": "trigger.manual", "config": {}},
				{"id": "comment", "type": "action.add_comment", "config": {"content": "Follow up"}},
			],
			"edges": [
				{"source": "start", "source_handle": "default", "target": "comment"},
				{"source": "comment", "source_handle": "default", "target": "start"},
			],
		})
		with (
			patch("finbyzai.workflow_builder.ai_authoring.frappe.get_doc", return_value=self.workflow),
			patch("finbyzai.workflow_builder.ai_authoring._authoring_agent", return_value=(SimpleNamespace(temperature=0, max_tokens=1024), SimpleNamespace(name="Authoring Model", provider="Provider", llm=llm))),
			patch("finbyzai.workflow_builder.ai_authoring._safe_catalog", return_value=(self.catalog, [], [])),
			patch("finbyzai.workflow_builder.ai_authoring._rate_limit"),
			patch("finbyzai.workflow_builder.ai_authoring.authoring.create_audit"),
		):
			with self.assertRaisesRegex(AutomationError, "unsafe workflow structure"):
				generate_draft(self.workflow.name, "Build a workflow with an unsafe cycle.", empty_graph("Lead"))

	def test_provider_failure_is_returned_as_a_safe_configuration_error(self):
		llm = Mock()
		llm.model_copy.return_value = llm
		llm.invoke.side_effect = RuntimeError("api_key=private-provider-secret")
		with (
			patch("finbyzai.workflow_builder.ai_authoring.frappe.get_doc", return_value=self.workflow),
			patch("finbyzai.workflow_builder.ai_authoring._authoring_agent", return_value=(SimpleNamespace(temperature=0, max_tokens=1024), SimpleNamespace(name="Authoring Model", provider="Provider", llm=llm))),
			patch("finbyzai.workflow_builder.ai_authoring._safe_catalog", return_value=(self.catalog, [], [])),
			patch("finbyzai.workflow_builder.ai_authoring._rate_limit"),
			patch("finbyzai.workflow_builder.ai_authoring.authoring.create_audit"),
		):
			with self.assertRaisesRegex(AutomationError, "Verify its credentials") as failure:
				generate_draft(self.workflow.name, "When manually enrolled, add a safe comment.", empty_graph("Lead"))
		self.assertNotIn("private-provider-secret", str(failure.exception))
