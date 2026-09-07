import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.ai_support import (
	_assert_provider_circuit_closed,
	_context_manifest,
	_parse_json_response,
	_sanitize_ai_result,
	_support_policy,
	build_ai_context,
	build_profile_snapshot,
	execute_ai_action,
	output_schema_for_node,
)
from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.external import execute_external
from finbyzai.workflow_builder.engine import simulate_graph
from finbyzai.workflow_builder.principal import execution_principal
from finbyzai.workflow_builder.schema import empty_graph, validate_graph


class TestWorkflowAISupport(IntegrationTestCase):
	def _ai_graph(self, *, node_type="action.ai_generate", handles=None):
		graph = empty_graph("Issue", "trigger.manual")
		config = {
			"ai_profile": "Support Agent",
			"knowledge_base": "Support Knowledge",
			"field_allowlist": ["subject", "description"],
			"mode": "summarize",
			"confidence_threshold": 0.75,
			"timeout_seconds": 60,
			"max_tokens": 512,
			"failure_mode": "branch",
		}
		if node_type == "action.ai_support_agent":
			config.update({"mode": "grounded_answer", "response_policy": "draft_only", "max_automatic_turns": 3})
		graph["nodes"].append(
			{"id": "ai", "type": node_type, "type_version": 1, "position": {"x": 0, "y": 200}, "config": config}
		)
		graph["edges"].append(
			{"id": "start-ai", "source": graph["start_node_id"], "source_handle": "default", "target": "ai"}
		)
		for index, handle in enumerate(handles or []):
			node_id = f"path-{index}"
			graph["nodes"].append(
				{
					"id": node_id,
					"type": "action.add_comment",
					"type_version": 1,
					"position": {"x": index * 200, "y": 400},
					"config": {"content": handle},
				}
			)
			graph["edges"].append(
				{"id": f"ai-{handle}", "source": "ai", "source_handle": handle, "target": node_id}
			)
		return graph

	def test_ai_generate_requires_all_explicit_outcome_paths_for_publication(self):
		incomplete = validate_graph(
			self._ai_graph(handles=["success", "failure"]),
			primary_doctype="Issue",
			publish=True,
		)
		self.assertIn("AI_PATHS_INCOMPLETE", {issue["code"] for issue in incomplete["issues"]})

		complete = validate_graph(
			self._ai_graph(handles=["success", "low_confidence", "failure"]),
			primary_doctype="Issue",
			publish=True,
		)
		self.assertNotIn("AI_PATHS_INCOMPLETE", {issue["code"] for issue in complete["issues"]})

	def test_ai_support_requires_response_handoff_and_failure_paths(self):
		validation = validate_graph(
			self._ai_graph(
				node_type="action.ai_support_agent",
				handles=["respond", "handoff", "failure"],
			),
			primary_doctype="Issue",
			publish=True,
		)
		self.assertNotIn("AI_PATHS_INCOMPLETE", {issue["code"] for issue in validation["issues"]})

	def test_provider_output_is_strict_json_and_schema_validated(self):
		schema = output_schema_for_node("action.ai_generate", "summarize")
		valid = _parse_json_response(
			SimpleNamespace(content='{"summary":"Ready","confidence":0.9,"risk_flags":[]}'),
			schema,
		)
		self.assertEqual(valid["summary"], "Ready")
		with self.assertRaisesRegex(AutomationError, "valid JSON"):
			_parse_json_response(SimpleNamespace(content="not-json"), schema)
		with self.assertRaisesRegex(AutomationError, "schema validation"):
			_parse_json_response(SimpleNamespace(content='{"summary":"Missing confidence","risk_flags":[]}'), schema)

	def test_model_text_is_inert_and_secret_patterns_are_redacted(self):
		result = _sanitize_ai_result(
			{
				"summary": '<script>alert(1)</script><b>Password: top-secret</b>',
				"confidence": 0.9,
				"risk_flags": ["<b>security</b>"],
				"knowledge_gaps": ["<a href='x'>Missing policy</a>"],
			}
		)
		self.assertNotIn("<", result["summary"])
		self.assertNotIn("top-secret", result["summary"])
		self.assertIn("[REDACTED]", result["summary"])
		self.assertEqual(result["risk_flags"], ["security"])

	def test_profile_rejects_credentials_in_agent_instructions(self):
		agent = frappe._dict(
			name="Unsafe Agent", modified="2026-08-25", agent_type="Chat Agent", llm="Text Model",
			tools=[], messages=[frappe._dict(type="system", content_type="text", content="api_key=sk-example-secret-value")],
			knowledge_base=None, temperature=0, max_tokens=512,
		)
		model = frappe._dict(name="Text Model", provider="Provider", enabled=1, is_embedding_model=0, supports_image_generation=0)
		provider = frappe._dict(name="Provider", disabled=0)
		with (
			patch("finbyzai.workflow_builder.ai_support.frappe.get_doc", side_effect=lambda doctype, _name: {"AI Agent": agent, "LLM": model, "LLM Provider": provider}[doctype]),
			patch("finbyzai.workflow_builder.ai_support._has_doc_permission", return_value=True),
		):
			with self.assertRaisesRegex(AutomationError, "must not contain"):
				build_profile_snapshot("Unsafe Agent", execution_user="Administrator")

	def test_provider_context_uses_hashed_record_identity_and_a_noncontent_manifest(self):
		run = frappe._dict(record_doctype="Issue", record_name="ISS-PRIVATE")
		with (
			patch("finbyzai.workflow_builder.ai_support.int_setting", side_effect=lambda _field, default: default),
			patch("finbyzai.workflow_builder.ai_support.assert_field_access"),
			execution_principal("Administrator", {"trace_id": "ai-context-test"}),
		):
			context, last_communication = build_ai_context(
				run,
				{"field_allowlist": ["subject"], "include_thread": 0},
				record={"subject": "Private subject"},
			)
		self.assertIsNone(last_communication)
		self.assertNotIn("name", context["record"])
		self.assertNotIn("ISS-PRIVATE", json.dumps(context))
		manifest = json.loads(_context_manifest(context))
		self.assertEqual(manifest["fieldnames"], ["subject"])
		self.assertNotIn("Private subject", json.dumps(manifest))

	def test_support_policy_fails_closed_for_human_risk_confidence_and_review_modes(self):
		base = {"confidence": 0.95, "risk_flags": [], "citations": [{"source_id": "kb:1"}], "decision": "respond"}
		self.assertEqual(_support_policy(base, {"max_automatic_turns": 3, "confidence_threshold": 0.8, "response_policy": "eligible_auto_response"}, {"message": "Please connect me to a human"}, turn_count=1)[0], "handoff")
		self.assertEqual(_support_policy(base, {"max_automatic_turns": 3, "confidence_threshold": 0.8, "response_policy": "eligible_auto_response"}, {"message": "My password was stolen in a breach"}, turn_count=1)[0], "handoff")
		self.assertEqual(_support_policy({**base, "confidence": 0.2}, {"max_automatic_turns": 3, "confidence_threshold": 0.8, "response_policy": "eligible_auto_response"}, {}, turn_count=1)[0], "handoff")
		self.assertEqual(_support_policy(base, {"max_automatic_turns": 3, "confidence_threshold": 0.8, "response_policy": "approval_required"}, {}, turn_count=1)[0], "handoff")
		self.assertEqual(_support_policy(base, {"max_automatic_turns": 3, "confidence_threshold": 0.8, "response_policy": "eligible_auto_response"}, {}, turn_count=1)[0], "respond")

	def test_ai_generate_routes_low_confidence_without_side_effects(self):
		profile = SimpleNamespace(
			name="AIP-1",
			provider="OpenAI",
			model="model-1",
			snapshot_json=json.dumps({"provider": "OpenAI", "model": "model-1"}),
		)
		attempt = Mock(name="attempt")
		attempt.name = "AIA-1"
		run = SimpleNamespace(
			name="RUN-1",
			workflow="WF-1",
			workflow_version="WV-1",
			record_doctype="Issue",
			record_name="ISS-1",
		)
		response = SimpleNamespace(
			content='{"summary":"A concise summary","confidence":0.4,"risk_flags":[]}',
			usage_metadata={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
		)
		with (
			patch("finbyzai.workflow_builder.ai_support.ai_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.ai_support._daily_budget_available"),
			patch("finbyzai.workflow_builder.ai_support._profile_for_run", return_value=(profile, {"provider": "OpenAI", "model": "model-1"})),
			patch("finbyzai.workflow_builder.ai_support.build_ai_context", return_value=({"record": {"fields": {"subject": "Help"}}, "conversation": []}, None)),
			patch("finbyzai.workflow_builder.ai_support._attempt", return_value=attempt),
			patch("finbyzai.workflow_builder.ai_support._knowledge_sources", return_value=[]),
			patch("finbyzai.workflow_builder.ai_support._invoke_profile", return_value=response),
		):
			result = execute_ai_action(
				"action.ai_generate",
				run,
				{"mode": "summarize", "confidence_threshold": 0.75},
				record={"subject": "Help"},
				effect_key="effect-1",
				node_id="ai",
				token_name="TOKEN-1",
			)

		self.assertEqual(result["handle"], "low_confidence")
		self.assertEqual(result["output"]["status"], "low_confidence")
		self.assertEqual(result["output"]["usage"]["total_tokens"], 20)
		self.assertEqual(result["output"]["text"], "A concise summary")
		self.assertEqual(result["output"]["result"]["summary"], "A concise summary")
		attempt.save.assert_called_once_with(ignore_permissions=True)

	def test_ai_generate_routes_high_confidence_to_success(self):
		profile = SimpleNamespace(
			name="AIP-1",
			provider="OpenAI",
			model="model-1",
			snapshot_json=json.dumps({"provider": "OpenAI", "model": "model-1"}),
		)
		attempt = Mock(name="attempt")
		attempt.name = "AIA-1"
		run = SimpleNamespace(
			name="RUN-1",
			workflow="WF-1",
			workflow_version="WV-1",
			record_doctype="Issue",
			record_name="ISS-1",
		)
		response = SimpleNamespace(
			content='{"summary":"Customer needs an invoice copy","confidence":0.96,"risk_flags":[]}',
			usage_metadata={"input_tokens": 14, "output_tokens": 9, "total_tokens": 23},
		)
		with (
			patch("finbyzai.workflow_builder.ai_support.ai_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.ai_support._daily_budget_available"),
			patch("finbyzai.workflow_builder.ai_support._profile_for_run", return_value=(profile, {"provider": "OpenAI", "model": "model-1"})),
			patch("finbyzai.workflow_builder.ai_support.build_ai_context", return_value=({"record": {"fields": {"subject": "Invoice"}}, "conversation": []}, None)),
			patch("finbyzai.workflow_builder.ai_support._attempt", return_value=attempt),
			patch("finbyzai.workflow_builder.ai_support._knowledge_sources", return_value=[]),
			patch("finbyzai.workflow_builder.ai_support._invoke_profile", return_value=response),
		):
			result = execute_ai_action(
				"action.ai_generate",
				run,
				{"mode": "summarize", "confidence_threshold": 0.75},
				record={"subject": "Invoice"},
				effect_key="effect-success",
				node_id="ai",
				token_name="TOKEN-1",
			)

		self.assertEqual(result["handle"], "success")
		self.assertEqual(result["output"]["status"], "completed")
		self.assertEqual(result["output"]["text"], "Customer needs an invoice copy")
		self.assertEqual(result["output"]["usage"]["total_tokens"], 23)
		attempt.save.assert_called_once_with(ignore_permissions=True)

	def test_support_agent_routes_grounded_high_confidence_response(self):
		profile = SimpleNamespace(
			name="AIP-1",
			provider="OpenAI",
			model="model-1",
			snapshot_json=json.dumps({"provider": "OpenAI", "model": "model-1"}),
		)
		attempt = Mock(name="attempt")
		attempt.name = "AIA-1"
		session = Mock(name="session")
		session.name = "AIS-1"
		session.state = "ACTIVE"
		session.turn_count = 0
		run = SimpleNamespace(
			name="RUN-1",
			workflow="WF-1",
			workflow_version="WV-1",
			record_doctype="Issue",
			record_name="ISS-1",
		)
		source = {"source_id": "kb:1", "title": "Invoices", "locator": "KB-1", "content": "Invoice copies are available in the portal."}
		response = SimpleNamespace(
			content=json.dumps({
				"summary": "Customer requested an invoice copy.",
				"confidence": 0.95,
				"risk_flags": [],
				"decision": "respond",
				"answer": "You can download the invoice from the customer portal.",
				"citations": [{"source_id": "kb:1"}],
				"knowledge_gaps": [],
				"handoff_reason": None,
			}),
			usage_metadata={"input_tokens": 30, "output_tokens": 18, "total_tokens": 48},
		)
		with (
			patch("finbyzai.workflow_builder.ai_support.ai_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.ai_support._daily_budget_available"),
			patch("finbyzai.workflow_builder.ai_support._profile_for_run", return_value=(profile, {"provider": "OpenAI", "model": "model-1"})),
			patch("finbyzai.workflow_builder.ai_support.build_ai_context", return_value=({"record": {"fields": {"subject": "Invoice copy"}}, "conversation": []}, None)),
			patch("finbyzai.workflow_builder.ai_support._attempt", return_value=attempt),
			patch("finbyzai.workflow_builder.ai_support._get_or_create_session", return_value=session),
			patch("finbyzai.workflow_builder.ai_support._knowledge_sources", return_value=[source]),
			patch("finbyzai.workflow_builder.ai_support._invoke_profile", return_value=response),
		):
			result = execute_ai_action(
				"action.ai_support_agent",
				run,
				{
					"mode": "grounded_answer",
					"knowledge_base": "KB-1",
					"confidence_threshold": 0.8,
					"response_policy": "eligible_auto_response",
					"max_automatic_turns": 3,
				},
				record={"subject": "Invoice copy"},
				effect_key="effect-support",
				node_id="support",
				token_name="TOKEN-1",
			)

		self.assertEqual(result["handle"], "respond")
		self.assertEqual(result["output"]["status"], "completed")
		self.assertEqual(result["output"]["citations"], [{"source_id": "kb:1", "title": "Invoices", "locator": "KB-1"}])
		self.assertEqual(result["output"]["session_id"], "AIS-1")
		self.assertEqual(session.state, "AWAITING_CUSTOMER")
		self.assertEqual(session.turn_count, 1)
		session.save.assert_called_once_with(ignore_permissions=True)
		attempt.save.assert_called_once_with(ignore_permissions=True)

	def test_handed_off_support_session_never_calls_provider_again(self):
		profile = SimpleNamespace(name="AIP-1", provider="OpenAI", model="model-1", snapshot_json=json.dumps({"provider": "OpenAI", "model": "model-1"}))
		attempt = Mock(name="attempt")
		attempt.name = "AIA-1"
		session = Mock(name="session")
		session.name = "AIS-1"
		session.state = "AWAITING_HUMAN"
		session.turn_count = 2
		session.handoff_reason = "Customer requested a human"
		run = SimpleNamespace(name="RUN-1", workflow="WF-1", workflow_version="WV-1", record_doctype="Issue", record_name="ISS-1")
		with (
			patch("finbyzai.workflow_builder.ai_support.ai_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.ai_support._daily_budget_available"),
			patch("finbyzai.workflow_builder.ai_support._profile_for_run", return_value=(profile, {"provider": "OpenAI", "model": "model-1"})),
			patch("finbyzai.workflow_builder.ai_support.build_ai_context", return_value=({"record": {"fields": {}}, "conversation": []}, None)),
			patch("finbyzai.workflow_builder.ai_support._attempt", return_value=attempt),
			patch("finbyzai.workflow_builder.ai_support._get_or_create_session", return_value=session),
			patch("finbyzai.workflow_builder.ai_support._invoke_profile") as invoke,
		):
			result = execute_ai_action(
				"action.ai_support_agent", run, {"mode": "grounded_answer"}, record={},
				effect_key="effect-1", node_id="ai", token_name="TOKEN-1",
			)
		self.assertEqual(result["handle"], "handoff")
		self.assertEqual(result["output"]["turn_number"], 2)
		self.assertEqual(result["output"]["session_id"], "AIS-1")
		invoke.assert_not_called()

	def test_deterministic_simulation_stops_at_ai_instead_of_guessing_a_branch(self):
		graph = self._ai_graph(handles=["success", "low_confidence", "failure"])
		result = simulate_graph(graph, frappe._dict(doctype="Issue", name="ISS-1", subject="Help"))
		self.assertFalse(result["completed"])
		self.assertTrue(result["requires_ai_test"])
		self.assertEqual(result["path"][-1]["status"], "REQUIRES_AI_TEST")
		self.assertEqual(result["path"][-1]["node_id"], "ai")

	def test_provider_circuit_opens_only_after_consecutive_failures(self):
		snapshot = {"provider": "Provider", "model": "Model"}
		with (
			patch("finbyzai.workflow_builder.ai_support.int_setting", side_effect=lambda field, default: default),
			patch("finbyzai.workflow_builder.ai_support.frappe.get_all", return_value=[frappe._dict(status="FAILED", error_code="TimeoutError") for _ in range(5)]),
		):
			with self.assertRaisesRegex(AutomationError, "temporarily paused"):
				_assert_provider_circuit_closed(snapshot)
		with (
			patch("finbyzai.workflow_builder.ai_support.int_setting", side_effect=lambda field, default: default),
			patch("finbyzai.workflow_builder.ai_support.frappe.get_all", return_value=[frappe._dict(status="COMPLETED", error_code=None), *[frappe._dict(status="FAILED", error_code="TimeoutError") for _ in range(4)]]),
		):
			_assert_provider_circuit_closed(snapshot)

	def test_provider_failure_uses_failure_branch_without_leaking_context(self):
		profile = SimpleNamespace(
			name="AIP-1",
			provider="OpenAI",
			model="model-1",
			snapshot_json=json.dumps({"provider": "OpenAI", "model": "model-1"}),
		)
		attempt = Mock(name="attempt")
		attempt.name = "AIA-1"
		run = SimpleNamespace(name="RUN-1", workflow="WF-1", workflow_version="WV-1", record_doctype="Issue", record_name="ISS-1")
		with (
			patch("finbyzai.workflow_builder.ai_support.ai_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.ai_support._daily_budget_available"),
			patch("finbyzai.workflow_builder.ai_support._profile_for_run", return_value=(profile, {"provider": "OpenAI", "model": "model-1"})),
			patch("finbyzai.workflow_builder.ai_support.build_ai_context", return_value=({"record": {"fields": {"subject": "private"}}, "conversation": []}, None)),
			patch("finbyzai.workflow_builder.ai_support._attempt", return_value=attempt),
			patch("finbyzai.workflow_builder.ai_support._knowledge_sources", return_value=[]),
			patch("finbyzai.workflow_builder.ai_support._invoke_profile", side_effect=TimeoutError("provider timed out")),
		):
			result = execute_ai_action(
				"action.ai_generate", run, {"mode": "summarize", "failure_mode": "branch"},
				record={"subject": "private"}, effect_key="effect-1", node_id="ai", token_name="TOKEN-1",
			)
		self.assertEqual(result["handle"], "failure")
		self.assertEqual(result["output"]["error_code"], "TimeoutError")
		self.assertEqual(result["output"]["error_message"], "AI provider call failed. Review provider credentials and the attempt error code.")
		self.assertNotIn("private", json.dumps(result["output"]))

	def test_external_dispatch_preserves_ai_branch_handle(self):
		runtime_result = {"status": "COMPLETE", "handle": "success", "output": {"summary": "Ready"}}
		with patch("finbyzai.workflow_builder.ai_support.execute_ai_action", return_value=runtime_result) as execute:
			result = execute_external(
				"action.ai_generate",
				SimpleNamespace(),
				{"mode": "summarize"},
				record={},
				outputs={},
				effect_key="effect-1",
				node_id="ai",
				token_name="TOKEN-1",
			)
		self.assertEqual(result, runtime_result)
		execute.assert_called_once()

	def test_inline_profile_snapshot_and_jinja_rendering(self):
		model_doc = SimpleNamespace(name="gemini-2.0-flash", provider="Google", enabled=1, is_embedding_model=0, supports_image_generation=0)
		provider_doc = SimpleNamespace(name="Google", disabled=0)
		with (
			patch("frappe.get_doc", side_effect=lambda doctype, name=None: model_doc if doctype == "LLM" else provider_doc if doctype == "LLM Provider" else None),
			patch("finbyzai.workflow_builder.ai_support._has_doc_permission", return_value=True),
		):
			snapshot = build_profile_snapshot(
				execution_user="Administrator",
				inline_config={
					"prompt_mode": "inline",
					"model": "gemini-2.0-flash",
					"system_prompt": "You are a triage assistant for {{ doc.company }}.",
					"user_prompt": "Please review: {{ doc.subject }}",
					"output_format": "text",
					"temperature": 0.3,
				},
			)
		self.assertEqual(snapshot["prompt_mode"], "inline")
		self.assertEqual(snapshot["model"], "gemini-2.0-flash")
		self.assertEqual(snapshot["provider"], "Google")
		self.assertEqual(snapshot["output_format"], "text")

		from finbyzai.workflow_builder.ai_support import _provider_prompt
		context = {"record": {"fields": {"company": "Megasol", "subject": "Billing issue"}}, "conversation": []}
		system, human = _provider_prompt(snapshot, {"output_format": "text"}, context, [], {})
		self.assertIn("Megasol", system)
		self.assertIn("Billing issue", human)
