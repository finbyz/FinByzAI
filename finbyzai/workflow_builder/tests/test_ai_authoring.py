import json

import frappe
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.ai_authoring import (
	CONFIGURED_VALUE_SENTINEL,
	_authoring_suggestions,
	_effective_response_schema,
	_format_chat_history,
	_normalise_graph,
	_parse_response,
	_effective_request,
	_looks_like_approval,
	_reply_type,
	_repair_ai_payload,
	_repair_condition,
	_response_schema,
	_safe_current_graph,
	_sanitise_turn,
	generate_draft,
)
from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.schema import empty_graph


def _agent(**overrides):
	base = dict(name="Workflow Builder Generator", temperature=0, max_tokens=1024, messages=[], output_schema=None)
	base.update(overrides)
	return SimpleNamespace(**base)


def _model():
	return SimpleNamespace(name="Authoring Model", provider="Provider")


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

	def _payload(self, *, graph=None):
		"""What _invoke_authoring_agent returns: the agent's parsed structured output."""
		graph = graph or {
			"start_node_id": "start",
			"nodes": [
				{"id": "start", "type": "trigger.manual", "config": {}},
				{"id": "comment", "type": "action.add_comment", "config": {"content": "Follow up"}},
			],
			"edges": [{"source": "start", "source_handle": "default", "target": "comment"}],
		}
		return {
			"summary": "Add a follow-up comment after manual enrollment.",
			"assumptions": ["An operator enrolls the Lead."],
			"warnings": [],
			"graph": graph,
		}

	def _generate(self, prompt, current, *, invoke, **kwargs):
		with (
			patch("finbyzai.workflow_builder.ai_authoring.frappe.get_doc", return_value=self.workflow),
			patch("finbyzai.workflow_builder.ai_authoring._authoring_agent", return_value=(_agent(), _model())),
			patch("finbyzai.workflow_builder.ai_authoring._safe_catalog", return_value=(self.catalog, [], [])),
			patch("finbyzai.workflow_builder.ai_authoring._rate_limit"),
			patch("finbyzai.workflow_builder.ai_authoring._invoke_authoring_agent", **invoke),
			patch("finbyzai.workflow_builder.ai_authoring.authoring.create_audit") as audit,
		):
			return generate_draft(self.workflow.name, prompt, current, **kwargs), audit

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

	def test_effective_schema_reimposes_permission_scoped_node_types(self):
		"""A permissive agent output_schema is narrowed to the live catalogue."""
		agent = _agent(output_schema=json.dumps({
			"type": "object",
			"properties": {
				"summary": {"type": "string"},
				"graph": {
					"type": "object",
					"properties": {
						"nodes": {
							"type": "array",
							"items": {"type": "object", "properties": {"type": {"type": "string", "enum": ["anything.at.all"]}}},
						}
					},
				},
			},
		}))
		schema = _effective_response_schema(agent, ["trigger.manual", "action.add_comment"])
		node_type = schema["properties"]["graph"]["properties"]["nodes"]["items"]["properties"]["type"]
		self.assertEqual(set(node_type["enum"]), {"trigger.manual", "action.add_comment"})
		self.assertNotIn("anything.at.all", node_type["enum"])

	def test_effective_schema_falls_back_when_agent_schema_missing(self):
		schema = _effective_response_schema(_agent(output_schema=None), ["trigger.manual"])
		self.assertEqual(
			schema["properties"]["graph"]["properties"]["nodes"]["items"]["properties"]["type"]["enum"],
			["trigger.manual"],
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
		current = empty_graph("Lead", "trigger.manual")
		result, audit = self._generate(
			"When manually enrolled, add a follow-up comment.",
			current,
			invoke={"return_value": self._payload()},
		)
		self.assertFalse(result["mutated"])
		self.assertFalse(result["published"])
		self.assertEqual(result["graph"]["primary_doctype"], "Lead")
		self.assertEqual(result["node_count"], 2)
		self.assertEqual(result["mode"], "generate")
		self.assertEqual(result["agent"], "Workflow Builder Generator")
		self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
		self.assertFalse(result["issues"])
		self.assertEqual(current, empty_graph("Lead", "trigger.manual"))
		self.assertEqual(audit.call_count, 2)

	def test_generate_accepts_a_raw_json_string_from_the_agent(self):
		result, _ = self._generate(
			"When manually enrolled, add a follow-up comment.",
			empty_graph("Lead", "trigger.manual"),
			invoke={"return_value": "```json\n" + json.dumps(self._payload()) + "\n```"},
		)
		self.assertEqual(result["node_count"], 2)

	def test_generate_rejects_unsafe_cycle_instead_of_returning_it(self):
		cyclic = self._payload(graph={
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
		with self.assertRaisesRegex(AutomationError, "unsafe workflow structure"):
			self._generate(
				"Build a workflow with an unsafe cycle.",
				empty_graph("Lead"),
				invoke={"return_value": cyclic},
			)

	def test_provider_failure_is_returned_as_a_safe_configuration_error(self):
		with self.assertRaisesRegex(AutomationError, "Verify its credentials") as failure:
			self._generate(
				"When manually enrolled, add a safe comment.",
				empty_graph("Lead"),
				invoke={"side_effect": RuntimeError("api_key=private-provider-secret")},
			)
		self.assertNotIn("private-provider-secret", str(failure.exception))

	def test_converse_turn_returns_a_follow_up_question_without_a_graph(self):
		result, audit = self._generate(
			"set up an automation",
			empty_graph("Lead"),
			invoke={"return_value": {
				"reply_type": "question",
				"message": "What should this run on?",
				"questions": ["Which trigger event?", "Who should be notified?"],
			}},
			allow_clarify=True,
		)
		self.assertEqual(result["reply_type"], "question")
		self.assertEqual(result["message"], "What should this run on?")
		self.assertEqual(len(result["questions"]), 2)
		self.assertNotIn("graph", result)
		self.assertEqual(audit.call_args_list[-1][0][1], "AI_DRAFT_CLARIFY")

	def test_one_shot_endpoint_never_treats_a_question_as_a_valid_draft(self):
		with self.assertRaises(AutomationError):
			self._generate(
				"set up an automation",
				empty_graph("Lead"),
				invoke={"return_value": {"reply_type": "question", "message": "More detail?", "questions": []}},
			)

	def test_converse_still_builds_a_proposal_when_the_request_is_complete(self):
		result, _ = self._generate(
			"When manually enrolled, add a follow-up comment.",
			empty_graph("Lead", "trigger.manual"),
			invoke={"return_value": self._payload()},
			allow_clarify=True,
		)
		self.assertEqual(result["reply_type"], "proposal")
		self.assertEqual(result["node_count"], 2)

	def test_reply_type_is_inferred_when_the_agent_omits_it(self):
		self.assertEqual(_reply_type({"graph": {"nodes": []}}), "proposal")
		self.assertEqual(_reply_type({"questions": ["x"]}), "question")
		self.assertEqual(_reply_type({"message": "hi"}), "question")
		self.assertEqual(_reply_type({"summary": "done"}), "proposal")

	def test_repair_condition_normalises_loose_model_output(self):
		# flat predicate -> kind added, synonym operator mapped, value dropped for is_set
		self.assertEqual(
			_repair_condition({"field": "recording_url", "operator": "present", "value": True}),
			{"kind": "predicate", "field": "recording_url", "operator": "is_set"},
		)
		self.assertEqual(
			_repair_condition({"field": "amount", "operator": "equals", "value": 5}),
			{"kind": "predicate", "field": "amount", "operator": "eq", "value": 5},
		)
		# prose string is not salvageable
		self.assertIsNone(_repair_condition("recording is present"))
		# groups recurse
		group = _repair_condition({"kind": "all", "children": [{"field": "x", "operator": "is"}]})
		self.assertEqual(group["children"][0], {"kind": "predicate", "field": "x", "operator": "eq"})

	def test_repair_payload_fixes_types_edges_and_credentials(self):
		allowed = {"trigger.filter_criteria", "action.ai_generate", "action.create_todo", "action.create_record"}
		payload = {
			"summary": "s",
			"message": None,
			"graph": {
				"nodes": [
					{
						"id": "t",
						"type": "trigger.any",
						"config": {
							"triggers": [
								{"id": "t1", "type": "trigger.filter_criteria", "config": {"condition": {"field": "recording_url", "operator": "exists"}}}
							]
						},
					},
					{"id": "ai", "type": "ai.transcribe", "config": {"model": "default", "api_key": "x"}, "placeholder": None},
					{"id": "task", "type": "action.create", "config": {}},
				],
				"edges": [
					{"source": "t", "target": "ai"},
					{"source": "ai", "target": "task"},
				],
			},
		}
		out = _repair_ai_payload(payload, allowed)
		nodes = out["graph"]["nodes"]
		self.assertNotIn("message", out)  # explicit null dropped
		self.assertEqual(nodes[0]["type"], "trigger.filter_criteria")  # unwrapped from trigger.any
		self.assertEqual(nodes[0]["config"]["condition"]["kind"], "predicate")
		self.assertEqual(nodes[0]["config"]["condition"]["operator"], "is_set")
		self.assertEqual(nodes[1]["type"], "action.ai_generate")  # hallucinated type mapped
		self.assertEqual(nodes[1]["config"]["model"], "")  # guessed model scrubbed
		self.assertEqual(nodes[1]["config"]["api_key"], "")
		self.assertTrue(nodes[1]["placeholder"])
		self.assertEqual(nodes[2]["type"], "action.create_record")  # "action.create" -> create_record
		handles = {(e["source"], e["source_handle"]) for e in out["graph"]["edges"]}
		self.assertIn(("ai", "success"), handles)  # ai_generate branch edge fixed

	def test_repair_drops_broken_edges_and_reconnects_orphans(self):
		allowed = {"trigger.filter_criteria", "action.ai_generate", "action.create_todo"}
		payload = {
			"summary": "s",
			"graph": {
				"nodes": [
					{"id": "trg", "type": "trigger.filter_criteria", "config": {}},
					{"id": "ai", "type": "action.ai_generate", "config": {}},
					{"id": "task", "type": "action.create_todo", "config": {}},
				],
				"edges": [
					{"target": "ai", "source_handle": "default"},          # missing source -> dropped
					{"source": "ai", "target": "ghost"},                    # unknown target -> dropped
					{"source": "trg", "to": "ai"},                          # alias "to" -> kept
				],
			},
		}
		out = _repair_ai_payload(payload, allowed)
		edges = {(e["source"], e["source_handle"], e["target"]) for e in out["graph"]["edges"]}
		# trg->ai kept via alias; ai->task auto-reconnected (orphan) on the success handle
		self.assertIn(("trg", "default", "ai"), edges)
		self.assertIn(("ai", "success", "task"), edges)
		for edge in out["graph"]["edges"]:
			self.assertIn(edge["source"], {"trg", "ai"})
			self.assertIn(edge["target"], {"ai", "task"})

	def test_repair_blanks_dynamic_values_written_into_literal_only_keys(self):
		allowed = {"trigger.filter_criteria", "action.ai_generate", "action.create_todo", "action.send_email"}
		payload = {
			"summary": "s",
			"graph": {
				"start_node_id": "trg",
				"nodes": [
					# condition is structural - the predicate must survive
					{"id": "trg", "type": "trigger.filter_criteria",
					 "config": {"condition": {"kind": "predicate", "field": "recording_url", "operator": "is_set"}}},
					# user_prompt is templated for ai_generate - the token must survive
					{"id": "ai", "type": "action.ai_generate",
					 "config": {"user_prompt": "Summarise {{ doc.recording_url }}", "field_allowlist": ["recording_url"]}},
					# create_todo.description is literal-only - both forms must be scrubbed
					{"id": "t1", "type": "action.create_todo",
					 "config": {"allocated_to": "a@b.com", "description": "{{ node_output(ai, 'summary') }}"}},
					{"id": "t2", "type": "action.create_todo",
					 "config": {"allocated_to": "a@b.com", "description": {"kind": "node_output", "node_id": "ai", "path": "summary"}}},
					# send_email.recipient IS a value binding - it must survive
					{"id": "m", "type": "action.send_email",
					 "config": {"recipient": {"kind": "record_field", "field": "owner"}}},
				],
				"edges": [],
			},
		}
		nodes = {n["id"]: n for n in _repair_ai_payload(payload, allowed)["graph"]["nodes"]}
		self.assertEqual(nodes["trg"]["config"]["condition"]["field"], "recording_url")
		self.assertEqual(nodes["ai"]["config"]["user_prompt"], "Summarise {{ doc.recording_url }}")
		self.assertEqual(nodes["t1"]["config"]["description"], "")
		self.assertTrue(nodes["t1"]["placeholder"])
		self.assertEqual(nodes["t2"]["config"]["description"], "")
		self.assertTrue(nodes["t2"]["placeholder"])
		self.assertEqual(nodes["m"]["config"]["recipient"]["kind"], "record_field")

	def test_a_named_model_survives_but_a_guessed_one_is_blanked(self):
		"""The user can answer "use <model>" in chat; a hallucinated id must not
		reach binding validation (it used to crash on LLM lookup)."""
		real = frappe.db.get_value("LLM", {"enabled": 1}, "name")
		allowed = {"action.ai_generate"}
		payload = {
			"summary": "s",
			"graph": {
				"start_node_id": "a",
				"nodes": [
					{"id": "a", "type": "action.ai_generate", "config": {"model": "default", "user_prompt": "x"}},
					{"id": "b", "type": "action.ai_generate", "config": {"model": real, "user_prompt": "x"}},
				],
				"edges": [],
			},
		}
		nodes = {n["id"]: n for n in _repair_ai_payload(payload, allowed)["graph"]["nodes"]}
		self.assertEqual(nodes["a"]["config"]["model"], "")
		self.assertTrue(nodes["a"]["placeholder"])
		if real:
			self.assertEqual(nodes["b"]["config"]["model"], real)

	def test_a_second_trigger_is_demoted_to_a_condition(self):
		"""Models use trigger.filter_criteria as a mid-flow "check this field"
		step, which used to fail as TRIGGER_COUNT. It is a condition."""
		allowed = {"trigger.any", "trigger.filter_criteria", "condition.if_else", "action.create_todo"}
		predicate = {"kind": "predicate", "field": "recording_url", "operator": "is_set"}
		payload = {
			"summary": "s",
			"graph": {
				"start_node_id": "trg",
				"nodes": [
					{"id": "trg", "type": "trigger.any", "config": {"triggers": [
						{"id": "t1", "type": "trigger.document_insert", "config": {}}]}},
					{"id": "check", "type": "trigger.filter_criteria", "config": {"condition": predicate}},
					{"id": "todo", "type": "action.create_todo", "config": {}},
				],
				"edges": [
					{"source": "trg", "source_handle": "default", "target": "check"},
					{"source": "check", "source_handle": "default", "target": "todo"},
				],
			},
		}
		graph = _repair_ai_payload(payload, allowed)["graph"]
		nodes = {n["id"]: n for n in graph["nodes"]}
		self.assertEqual(nodes["trg"]["type"], "trigger.any")  # the real trigger stays
		self.assertEqual(nodes["check"]["type"], "condition.if_else")  # the extra is demoted
		self.assertEqual(
			nodes["check"]["config"]["branches"][0]["condition"], predicate
		)  # its criteria are preserved
		self.assertEqual(graph["start_node_id"], "trg")
		handle = next(e["source_handle"] for e in graph["edges"] if e["source"] == "check")
		self.assertEqual(handle, "branch-1")  # branch node needs a real handle

	def test_generate_retries_once_when_the_first_response_is_malformed(self):
		bad = self._payload()
		# An invented node type the repair layer cannot map -> schema rejects it
		# -> the draft is re-requested once with the error fed back.
		bad["graph"]["nodes"][1]["type"] = "totally.bogus.node"
		good = self._payload()
		result, _ = self._generate(
			"When manually enrolled, add a follow-up comment.",
			empty_graph("Lead", "trigger.manual"),
			invoke={"side_effect": [bad, good]},
		)
		self.assertEqual(result["node_count"], 2)
		self.assertEqual(result["graph"]["nodes"][1]["type"], "action.add_comment")

	def test_approval_detection_and_effective_request_rebuild(self):
		self.assertTrue(_looks_like_approval("yes, go ahead"))
		self.assertTrue(_looks_like_approval("Build it"))
		self.assertTrue(_looks_like_approval("ok proceed"))
		self.assertFalse(_looks_like_approval("no, change the trigger"))
		self.assertFalse(_looks_like_approval("what does a delay node do?"))

		history = [
			{"role": "user", "text": "When a call recording is present, transcribe it and make a task."},
			{"role": "assistant", "reply_type": "question", "text": "Plan: 1. trigger on recording present 2. transcribe 3. create task. Shall I build this?"},
		]
		# bare "yes" -> builder gets the original request + the approved plan
		rebuilt = _effective_request(history, "yes")
		self.assertIn("Original request:", rebuilt)
		self.assertIn("APPROVED", rebuilt)
		self.assertIn("create task", rebuilt)
		self.assertNotIn("adjustment", rebuilt)
		# approval carrying a tweak keeps the tweak
		rebuilt2 = _effective_request(history, "yes but set priority to High")
		self.assertIn("priority to High", rebuilt2)
		# a non-approval passes through untouched
		self.assertEqual(_effective_request(history, "change the trigger to manual"), "change the trigger to manual")
		# no prior plan -> untouched even if it looks like approval
		self.assertEqual(_effective_request([], "yes"), "yes")

	def test_chat_history_is_formatted_and_turns_are_sanitised(self):
		self.assertIn("first turn", _format_chat_history([]))
		text = _format_chat_history([
			{"role": "user", "text": "notify the owner"},
			{"role": "assistant", "text": "<b>done</b>"},
		])
		self.assertIn("USER: notify the owner", text)
		self.assertIn("ASSISTANT: done", text)
		clean = _sanitise_turn({
			"role": "assistant",
			"text": "built it",
			"reply_type": "proposal",
			"graph": {"nodes": [1, 2, 3]},
			"graph_hash": "abc123",
			"node_count": 3,
		})
		self.assertNotIn("graph", clean)
		self.assertEqual(clean["graph_hash"], "abc123")
		self.assertEqual(clean["node_count"], 3)
