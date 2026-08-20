from datetime import date, datetime
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.patches.v16_0.install_workflow_builder_schema import WORKFLOW_DOCTYPES
from finbyzai.workflow_builder.api import get_doctypes, get_fields
from finbyzai.workflow_builder.constants import MAX_CONDITION_DEPTH
from finbyzai.workflow_builder.engine import (
	_calculate_numeric_adjustment,
	_coerce_assignment_value,
	_multiselect_names,
)
from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.registry import (
	NODE_OUTPUT_PATHS,
	business_event_catalog,
	doctype_eligibility,
	field_catalog,
	field_catalog_result,
	is_eligible_doctype,
	node_catalog,
	workflow_object_profile,
)
from finbyzai.workflow_builder.schema import (
	condition_fields,
	empty_graph,
	evaluate_expression,
	execution_graph_hash,
	graph_hash,
	resolve_value,
	validate_expression,
	validate_graph,
)
from finbyzai.workflow_builder.setup import INDEXES, UNIQUES


def predicate(field, operator, value=None):
	return {"kind": "predicate", "field": field, "operator": operator, "value": value}


class TestAutomationSchema(IntegrationTestCase):
	def test_business_event_catalog_is_scoped_to_enrolled_doctype_and_usage(self):
		lead_trigger_topics = {row["topic"] for row in business_event_catalog("Lead", "trigger")}
		opportunity_trigger_topics = {row["topic"] for row in business_event_catalog("Opportunity", "trigger")}
		customer_trigger_topics = {row["topic"] for row in business_event_catalog("Customer", "trigger")}
		contact_trigger_topics = {row["topic"] for row in business_event_catalog("Contact", "trigger")}
		sales_order_trigger_topics = {row["topic"] for row in business_event_catalog("Sales Order", "trigger")}
		sales_order_wait_topics = {row["topic"] for row in business_event_catalog("Sales Order", "wait")}

		self.assertIn("crm.lead.qualified", lead_trigger_topics)
		self.assertNotIn("crm.lead.qualified", opportunity_trigger_topics)
		self.assertNotIn("crm.contact.list.joined", opportunity_trigger_topics)
		self.assertIn("crm.call.inbound", opportunity_trigger_topics)
		self.assertIn("crm.call.inbound", contact_trigger_topics)
		self.assertNotIn("commerce.store.login", lead_trigger_topics)
		self.assertNotIn("commerce.order.created", lead_trigger_topics)
		self.assertIn("commerce.store.login", customer_trigger_topics)
		self.assertIn("commerce.order.created", customer_trigger_topics)
		self.assertNotIn("commerce.order.created", sales_order_trigger_topics)
		self.assertIn("email.clicked", sales_order_wait_topics)
		self.assertEqual(workflow_object_profile("Opportunity")["primary_doctype"], "Opportunity")
		lead_list = next(row for row in business_event_catalog("Lead", "trigger") if row["topic"] == "crm.contact.list.joined")
		self.assertEqual(lead_list["label"], "Joined a list")
		self.assertEqual(lead_list["category"], "CRM events")

	def test_business_event_catalog_explains_event_producers_and_record_resolution(self):
		rows = business_event_catalog("Lead", "trigger")
		qualified = next(row for row in rows if row["topic"] == "crm.lead.qualified")
		self.assertEqual(qualified["producer_status"], "native")
		self.assertEqual(qualified["source_app"], "ERPNext Lead")
		self.assertIn("Qualification status", qualified["trigger_alternative"])
		self.assertIn("Lead", qualified["record_resolution"])

	def test_known_business_events_cannot_be_published_for_the_wrong_workflow_object(self):
		graph = empty_graph("Opportunity", "trigger.event")
		graph["nodes"][0]["config"] = {
			"events": [{"id": "lead-qualified", "event_topic": "crm.lead.qualified", "event_filter": None}],
			"condition": None,
		}
		codes = {issue["code"] for issue in validate_graph(graph, primary_doctype="Opportunity", publish=True)["issues"]}
		self.assertIn("EVENT_NOT_AVAILABLE_FOR_WORKFLOW_OBJECT", codes)

		graph["nodes"][0]["config"]["events"][0]["event_topic"] = "custom.partner.event"
		codes = {issue["code"] for issue in validate_graph(graph, primary_doctype="Opportunity", publish=True)["issues"]}
		self.assertNotIn("EVENT_NOT_AVAILABLE_FOR_WORKFLOW_OBJECT", codes)

	def test_mixed_enrollment_trigger_groups_validate_as_or_subscriptions(self):
		graph = empty_graph("Lead", "trigger.any")
		graph["nodes"][0]["config"] = {
			"triggers": [
				{"id": "created", "type": "trigger.document_insert", "config": {"condition": None}},
				{"id": "qualified", "type": "trigger.event", "config": {"event_topic": "crm.lead.qualified", "event_filter": None, "condition": None}},
			]
		}
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])
		graph["nodes"][0]["config"]["triggers"][1]["id"] = "created"
		self.assertIn("INVALID_TRIGGER_GROUP_ID", {issue["code"] for issue in validate_graph(graph)["issues"]})

	def test_drip_go_to_and_integration_nodes_have_strict_contracts(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{"id": "drip", "type": "delay.drip", "type_version": 1, "config": {"batch_size": 25, "interval_seconds": 3600}},
			{"id": "jump", "type": "action.go_to", "type_version": 1, "config": {"target_node_id": "end"}},
			{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "drip"},
			{"id": "e2", "source": "drip", "source_handle": "default", "target": "jump"},
		]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])
		graph["nodes"][1]["config"]["batch_size"] = 0
		self.assertIn("INVALID_DRIP_BATCH", {issue["code"] for issue in validate_graph(graph)["issues"]})
		graph["nodes"][1]["config"]["batch_size"] = 25
		graph["nodes"][2]["config"]["target_node_id"] = "trigger-1"
		self.assertIn("INVALID_GO_TO_TARGET", {issue["code"] for issue in validate_graph(graph)["issues"]})

		for node_type, config in (
			("action.instagram_message", {"integration_secret": "meta", "url": "https://graph.facebook.com/me/messages", "recipient_id": {"kind": "literal", "value": "123"}, "message": {"kind": "literal", "value": "Hello"}, "purpose": "workflow"}),
			("action.asana", {"operation": "create_task", "payload": {"name": {"kind": "literal", "value": "Follow up"}}}),
		):
			candidate = empty_graph("Lead")
			candidate["nodes"].append({"id": "external", "type": node_type, "type_version": 1, "config": config})
			candidate["edges"] = [{"id": "external-edge", "source": "trigger-1", "source_handle": "default", "target": "external"}]
			with self.subTest(node_type=node_type):
				self.assertTrue(validate_graph(candidate, primary_doctype="Lead")["valid"])

	def test_node_catalog_exposes_the_validation_output_contract(self):
		catalog = {item["type"]: item for item in node_catalog()}
		self.assertNotIn("action.custom_script", catalog)
		for node_type, paths in NODE_OUTPUT_PATHS.items():
			with self.subTest(node_type=node_type):
				self.assertEqual(catalog[node_type]["output_paths"], paths)
				self.assertIsInstance(catalog[node_type]["output_paths"], list)

	def test_transfer_patch_covers_every_workflow_doctype(self):
		doctype_root = Path(frappe.get_app_path("finbyzai", "workflow_builder", "doctype"))
		on_disk = {
			folder.name
			for folder in doctype_root.iterdir()
			if folder.is_dir() and (folder / f"{folder.name}.json").is_file()
		}
		self.assertSetEqual(set(WORKFLOW_DOCTYPES), on_disk)

	def test_required_database_indexes_are_installed(self):
		for definitions in (INDEXES, UNIQUES):
			for doctype, entries in definitions.items():
				for _fields, index_name in entries:
					self.assertTrue(
						frappe.db.has_index(f"tab{doctype}", index_name),
						f"Missing index {index_name} on {doctype}",
					)

	def test_empty_graph_is_canonical_and_valid(self):
		graph = empty_graph("Lead")
		result = validate_graph(graph, primary_doctype="Lead", publish=True)
		self.assertTrue(result["valid"])
		self.assertEqual(result["graph_hash"], graph_hash(graph))
		self.assertEqual(result["graph"]["start_node_id"], "trigger-1")

	def test_execution_hash_ignores_layout_and_collection_order_only(self):
		graph = empty_graph("Lead")
		graph["nodes"].append(
			{"id": "end-1", "type": "end.complete", "type_version": 1, "position": {"x": 360, "y": 160}, "config": {}}
		)
		graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "end-1"}]
		layout = frappe.parse_json(frappe.as_json(graph))
		layout["nodes"].reverse()
		layout["nodes"][1]["position"] = {"x": 900, "y": 700}

		self.assertNotEqual(graph_hash(graph), graph_hash(layout))
		self.assertEqual(execution_graph_hash(graph), execution_graph_hash(layout))

		layout["nodes"][1]["config"] = {"runtime_change": True}
		self.assertNotEqual(execution_graph_hash(graph), execution_graph_hash(layout))

	def test_round_robin_v2_is_supported_without_upgrading_legacy_nodes(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({"id": "assign", "type": "action.round_robin", "type_version": 2, "config": {"group": "Administrator"}})
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "assign"}]
		result = validate_graph(graph, primary_doctype="Lead")
		self.assertNotIn("UNKNOWN_NODE_VERSION", {issue["code"] for issue in result["issues"]})
		graph["nodes"][1]["type_version"] = 3
		result = validate_graph(graph, primary_doctype="Lead")
		self.assertIn("UNKNOWN_NODE_VERSION", {issue["code"] for issue in result["issues"]})

	def test_named_criteria_branches_have_independent_conditions_and_none_output(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend(
			[
				{
					"id": "branch",
					"type": "condition.if_else",
					"type_version": 2,
					"config": {
						"branches": [
							{"handle": "german", "name": "German", "condition": predicate("language", "eq", "de")},
							{
								"handle": "high-value",
								"name": "High value",
								"condition": {"kind": "all", "children": [predicate("status", "eq", "Open"), predicate("annual_revenue", "gte", 10000)]},
							},
						]
					},
				},
				{"id": "end-de", "type": "end.complete", "type_version": 1, "config": {}},
				{"id": "end-value", "type": "end.complete", "type_version": 1, "config": {}},
				{"id": "end-none", "type": "end.complete", "type_version": 1, "config": {}},
			]
		)
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "branch"},
			{"id": "e2", "source": "branch", "source_handle": "german", "target": "end-de"},
			{"id": "e3", "source": "branch", "source_handle": "high-value", "target": "end-value"},
			{"id": "e4", "source": "branch", "source_handle": "none", "target": "end-none"},
		]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

		graph["nodes"][1]["config"]["branches"][1]["name"] = "German"
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("DUPLICATE_CRITERIA_BRANCH", codes)

	def test_specific_datetime_wait_and_event_trigger_validate(self):
		graph = empty_graph("Lead", "trigger.event")
		graph["nodes"][0]["config"] = {
			"events": [{"id": "click", "event_topic": "email.clicked", "event_filter": None}],
			"condition": None,
		}
		graph["nodes"].append(
			{
				"id": "wait",
				"type": "delay.until_date",
				"type_version": 1,
				"config": {"mode": "literal", "datetime": "2026-12-15 14:30:00"},
			}
		)
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "wait"}]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

		graph["nodes"][1]["config"]["datetime"] = ""
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("MISSING_DELAY_DATETIME", codes)

	def test_event_trigger_v2_supports_or_groups_and_event_specific_filters(self):
		graph = empty_graph("Lead", "trigger.event")
		graph["nodes"][0].update(
			{
				"type_version": 2,
				"config": {
					"events": [
						{
							"id": "click",
							"event_topic": "email.clicked",
							"event_filter": predicate("email_type", "eq", "Marketing"),
						},
						{
							"id": "form",
							"event_topic": "crm.form.submitted",
							"event_filter": predicate("form_name", "eq", "Contact Us"),
						},
					],
					"condition": predicate("status", "eq", "Lead"),
				},
			}
		)
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

		graph["nodes"][0]["config"]["events"][0]["event_filter"] = predicate("form_name", "eq", "Wrong source")
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("UNKNOWN_EVENT_FILTER_FIELD", codes)

	def test_named_branches_allow_twenty_and_unconnected_paths_end_implicitly(self):
		graph = empty_graph("Lead")
		branches = [
			{"handle": f"branch-{index}", "name": f"Branch {index}", "condition": predicate("status", "eq", f"State {index}")}
			for index in range(20)
		]
		graph["nodes"].append(
			{"id": "branch", "type": "condition.if_else", "type_version": 2, "config": {"branches": branches}}
		)
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "branch"}]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

		graph["nodes"][1]["config"]["branches"].append(
			{"handle": "branch-20", "name": "Branch 20", "condition": predicate("status", "eq", "State 20")}
		)
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("TOO_MANY_CRITERIA_BRANCHES", codes)

	def test_random_percentage_split_requires_named_paths_totalling_one_hundred(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{"id": "split", "type": "condition.random_split", "type_version": 1, "config": {"branches": [{"handle": "group-a", "name": "Group A", "percentage": 70}, {"handle": "group-b", "name": "Group B", "percentage": 30}]}},
			{"id": "end-a", "type": "end.complete", "type_version": 1, "config": {}},
			{"id": "end-b", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "split"},
			{"id": "e2", "source": "split", "source_handle": "group-a", "target": "end-a"},
			{"id": "e3", "source": "split", "source_handle": "group-b", "target": "end-b"},
		]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])
		graph["nodes"][1]["config"]["branches"][1]["percentage"] = 20
		self.assertIn("INVALID_PERCENTAGE_TOTAL", {issue["code"] for issue in validate_graph(graph)["issues"]})

	def test_reachability_cycle_and_handle_validation(self):
		graph = {
			"schema_version": 1,
			"primary_doctype": "Lead",
			"start_node_id": "trigger",
			"nodes": [
				{"id": "trigger", "type": "trigger.manual", "type_version": 1, "config": {}},
				{"id": "branch", "type": "condition.if_else", "type_version": 1, "config": {"condition": predicate("status", "eq", "Lead")}},
				{"id": "end-a", "type": "end.complete", "type_version": 1, "config": {}},
				{"id": "end-b", "type": "end.complete", "type_version": 1, "config": {}},
			],
			"edges": [
				{"id": "e1", "source": "trigger", "source_handle": "default", "target": "branch"},
				{"id": "e2", "source": "branch", "source_handle": "true", "target": "end-a"},
				{"id": "e3", "source": "branch", "source_handle": "true", "target": "end-b"},
			],
		}
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("INVALID_BRANCH_EDGES", codes)

		graph["edges"][2]["source_handle"] = "false"
		graph["edges"].append({"id": "e4", "source": "end-a", "source_handle": "default", "target": "branch"})
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("GRAPH_CYCLE", codes)
		self.assertIn("END_HAS_EDGE", codes)

	def test_switch_deduplicate_and_event_branch_contracts(self):
		for node, handles in (
			({"id": "branch", "type": "condition.switch", "type_version": 1, "config": {"field": "status", "cases": [{"value": "Open", "handle": "case-1"}]}}, ["case-1", "default"]),
			({"id": "branch", "type": "condition.deduplicate", "type_version": 1, "config": {"match_field": "email_id"}}, ["duplicate", "unique"]),
			({"id": "branch", "type": "delay.until_event", "type_version": 1, "config": {"event_topic": "crm.lead.qualified", "timeout_seconds": 3600}}, ["event", "timeout"]),
		):
			graph = empty_graph("Lead")
			graph["nodes"].extend([node, {"id": "end-a", "type": "end.complete", "type_version": 1, "config": {}}, {"id": "end-b", "type": "end.complete", "type_version": 1, "config": {}}])
			graph["edges"] = [
				{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "branch"},
				{"id": "e2", "source": "branch", "source_handle": handles[0], "target": "end-a"},
				{"id": "e3", "source": "branch", "source_handle": handles[1], "target": "end-b"},
			]
			with self.subTest(node_type=node["type"]):
				self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])
				graph["edges"][2]["source_handle"] = handles[0]
				self.assertIn("INVALID_BRANCH_EDGES", {issue["code"] for issue in validate_graph(graph)["issues"]})

	def test_event_delay_v2_uses_one_path_by_default_and_optional_outcome_branches(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{"id": "wait", "type": "delay.until_event", "type_version": 2, "config": {"event_topic": "email.clicked", "event_filter": None, "timeout_seconds": 3600, "branch_on_timeout": 0}},
			{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "wait"},
			{"id": "e2", "source": "wait", "source_handle": "default", "target": "end"},
		]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

		graph["edges"][1]["source_handle"] = "event"
		self.assertIn("INVALID_BRANCH_EDGES", {issue["code"] for issue in validate_graph(graph)["issues"]})

		graph["nodes"][1]["config"]["branch_on_timeout"] = 1
		graph["nodes"].append({"id": "timeout-end", "type": "end.complete", "type_version": 1, "config": {}})
		graph["edges"].append({"id": "e3", "source": "wait", "source_handle": "timeout", "target": "timeout-end"})
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

	def test_email_event_delay_can_scope_to_a_guaranteed_prior_send_email_output(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{"id": "send", "type": "action.send_email", "type_version": 1, "config": {"recipient": {"kind": "literal", "value": "person@example.com"}, "subject": {"kind": "literal", "value": "Hello"}, "message": {"kind": "literal", "value": "Body"}, "purpose": "workflow"}},
			{"id": "wait", "type": "delay.until_event", "type_version": 2, "config": {"event_topic": "email.clicked", "event_filter": None, "event_source": {"kind": "node_output", "node_id": "send", "path": "email_queue"}, "timeout_seconds": 3600, "branch_on_timeout": 0}},
			{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "send"},
			{"id": "e2", "source": "send", "source_handle": "default", "target": "wait"},
			{"id": "e3", "source": "wait", "source_handle": "default", "target": "end"},
		]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])
		graph["nodes"][2]["config"]["event_source"] = {"kind": "node_output", "node_id": "trigger-1", "path": "email_queue"}
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("INVALID_EVENT_SOURCE", codes)
		graph["nodes"][2]["config"].update({
			"event_source": {"kind": "node_output", "node_id": "send", "path": "email_queue"},
			"event_source_doctype": {"kind": "literal", "value": "Lead"},
		})
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("INVALID_EMAIL_EVENT_SOURCE_DOCTYPE", codes)

	def test_hubspot_style_event_wait_sources_and_indefinite_timeout_contract(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{
				"id": "todo",
				"type": "action.create_todo",
				"type_version": 1,
				"config": {"allocated_to": "Administrator", "description": "Follow up"},
			},
			{
				"id": "wait",
				"type": "delay.until_event",
				"type_version": 2,
				"config": {
					"data_source": "action_output",
					"event_topic": "workflow.todo.completed",
					"event_source": {"kind": "node_output", "node_id": "todo", "path": "name"},
					"event_source_doctype": {"kind": "node_output", "node_id": "todo", "path": "doctype"},
					"timeout_mode": "indefinite",
					"branch_on_timeout": 0,
				},
			},
			{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "todo"},
			{"id": "e2", "source": "todo", "source_handle": "default", "target": "wait"},
			{"id": "e3", "source": "wait", "source_handle": "default", "target": "end"},
		]
		self.assertTrue(validate_graph(graph, primary_doctype="Lead", publish=True)["valid"])

		graph["nodes"][2]["config"]["branch_on_timeout"] = 1
		codes = {issue["code"] for issue in validate_graph(graph, primary_doctype="Lead", publish=True)["issues"]}
		self.assertIn("INDEFINITE_WAIT_CANNOT_BRANCH", codes)

		graph["nodes"][2]["config"].update({
			"branch_on_timeout": 0,
			"event_topic": "email.opened",
		})
		codes = {issue["code"] for issue in validate_graph(graph, primary_doctype="Lead", publish=True)["issues"]}
		self.assertIn("INVALID_EVENT_SOURCE", codes)

	def test_send_email_v2_supports_templates_and_keeps_inline_email_compatible(self):
		graph = empty_graph("Lead")
		graph["nodes"].append(
			{
				"id": "email",
				"type": "action.send_email",
				"type_version": 2,
				"config": {
					"content_mode": "template",
					"email_template": "Lead welcome",
					"recipient": {"kind": "record_field", "field": "email_id"},
					"subject_override": {"kind": "literal", "value": ""},
					"purpose": "workflow",
				},
			}
		)
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "email"}
		]

		result = validate_graph(graph, primary_doctype="Lead")
		self.assertNotIn("UNKNOWN_NODE_VERSION", {issue["code"] for issue in result["issues"]})
		self.assertNotIn("MISSING_REQUIRED_CONFIG", {issue["code"] for issue in result["issues"]})
		self.assertNotIn("MISSING_EMAIL_TEMPLATE", {issue["code"] for issue in result["issues"]})

		graph["nodes"][1]["config"]["email_template"] = ""
		self.assertIn("MISSING_EMAIL_TEMPLATE", {issue["code"] for issue in validate_graph(graph)["issues"]})

		graph["nodes"][1]["config"] = {
			"content_mode": "inline",
			"recipient": {"kind": "literal", "value": "person@example.com"},
			"subject": {"kind": "literal", "value": "Hello"},
			"message": {"kind": "literal", "value": "Body"},
			"purpose": "workflow",
		}
		self.assertTrue(validate_graph(graph, primary_doctype="Lead")["valid"])

		graph["nodes"][1]["config"]["content_mode"] = "unknown"
		self.assertIn(
			"INVALID_EMAIL_CONTENT_MODE",
			{issue["code"] for issue in validate_graph(graph)["issues"]},
		)

	def test_graph_limits(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend(
			{"id": f"end-{index}", "type": "end.complete", "type_version": 1, "config": {}}
			for index in range(250)
		)
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("TOO_MANY_NODES", codes)

	def test_typed_condition_truth_table(self):
		record = frappe._dict(
			text="Megasol Lead",
			number=12.5,
			checked=True,
			day=date(2026, 8, 5),
			moment=datetime(2026, 8, 5, 10, 30),
			status="Open",
			link="CUST-001",
			empty=None,
		)
		cases = [
			(predicate("text", "contains", "megasol"), True),
			(predicate("number", "gte", 12), True),
			(predicate("checked", "eq", 1), True),
			(predicate("day", "eq", "2026-08-05"), True),
			(predicate("moment", "gt", "2026-08-05 10:00:00"), True),
			(predicate("status", "in", ["Open", "Closed"]), True),
			(predicate("link", "ne", "CUST-002"), True),
			(predicate("empty", "is_not_set"), True),
			(predicate("text", "not_contains", "lead"), False),
		]
		for expression, expected in cases:
			with self.subTest(expression=expression):
				self.assertEqual(evaluate_expression(expression, record), expected)

	def test_blank_numeric_set_operators_are_stable_after_frappe_persistence(self):
		new_lead = frappe._dict(doctype="Lead", annual_revenue=None, unsubscribed=0)
		persisted_blank_lead = frappe._dict(doctype="Lead", annual_revenue=0.0, unsubscribed=0)
		valued_lead = frappe._dict(doctype="Lead", annual_revenue=25000.0, unsubscribed=0)

		for record in (new_lead, persisted_blank_lead):
			with self.subTest(record=record):
				self.assertTrue(evaluate_expression(predicate("annual_revenue", "is_not_set"), record))
				self.assertFalse(evaluate_expression(predicate("annual_revenue", "is_set"), record))

		self.assertTrue(evaluate_expression(predicate("annual_revenue", "is_set"), valued_lead))
		self.assertFalse(evaluate_expression(predicate("annual_revenue", "is_not_set"), valued_lead))
		# A false Check value is a real value, not an absent value.
		self.assertTrue(evaluate_expression(predicate("unsubscribed", "is_set"), persisted_blank_lead))
		self.assertFalse(evaluate_expression(predicate("unsubscribed", "is_not_set"), persisted_blank_lead))

	def test_table_multiselect_conditions_support_live_rows_and_snapshots(self):
		live = frappe._dict(
			doctype="Opportunity",
			lost_reasons=[frappe._dict(lost_reason="Price"), frappe._dict(lost_reason="Timing")],
		)
		snapshot = frappe._dict(doctype="Opportunity", lost_reasons=["Price", "Timing"])
		for record in (live, snapshot):
			self.assertTrue(evaluate_expression(predicate("lost_reasons", "contains_any", ["Price"]), record))
			self.assertTrue(evaluate_expression(predicate("lost_reasons", "contains_all", ["Price", "Timing"]), record))
			self.assertTrue(evaluate_expression(predicate("lost_reasons", "contains_none", ["Budget"]), record))
			self.assertFalse(evaluate_expression(predicate("lost_reasons", "contains_all", ["Price", "Budget"]), record))
		self.assertEqual(
			_multiselect_names("Opportunity", "lost_reasons", live.lost_reasons),
			["Price", "Timing"],
		)

	def test_collection_operators_require_nonempty_string_lists(self):
		for value in (None, [], [""], [1]):
			with self.subTest(value=value):
				self.assertTrue(validate_expression(predicate("lost_reasons", "contains_any", value)))
		self.assertEqual(validate_expression(predicate("lost_reasons", "contains_any", ["Price"])), [])

	def test_value_requiring_conditions_reject_missing_or_empty_values(self):
		invalid_cases = (
			predicate("phone", "not_contains", None),
			predicate("phone", "contains", ""),
			predicate("status", "in", []),
			predicate("status", "not_in", []),
		)
		for expression in invalid_cases:
			with self.subTest(expression=expression):
				codes = {issue["code"] for issue in validate_expression(expression)}
				self.assertIn("MISSING_CONDITION_VALUE", codes)

		self.assertEqual(validate_expression(predicate("phone", "is_set")), [])
		self.assertEqual(validate_expression(predicate("status", "eq", "Lead")), [])

	def test_nested_conditions_and_structured_values(self):
		expression = {
			"kind": "all",
			"children": [
				predicate("status", "eq", "Open"),
				{
					"kind": "any",
					"children": [
						predicate("score", "gte", 20),
						{"kind": "not", "children": [predicate("territory", "eq", "Blocked")]},
					],
				},
			],
		}
		self.assertTrue(evaluate_expression(expression, frappe._dict(status="Open", score=20, territory="Blocked")))
		self.assertTrue(evaluate_expression(expression, frappe._dict(status="Open", score=5, territory="Allowed")))
		self.assertFalse(evaluate_expression(expression, frappe._dict(status="Open", score=5, territory="Blocked")))
		self.assertEqual(condition_fields(expression), {"status", "score", "territory"})
		record = frappe._dict(company_name="Megasol")
		outputs = {"create": {"name": "TASK-1"}}
		self.assertEqual(resolve_value({"kind": "record_field", "field": "company_name"}, record=record, outputs=outputs), "Megasol")
		self.assertEqual(resolve_value({"kind": "node_output", "node_id": "create", "path": "name"}, record=record, outputs=outputs), "TASK-1")

	def test_condition_depth_is_bounded_without_crashing(self):
		expression = predicate("status", "eq", "Open")
		for _index in range(MAX_CONDITION_DEPTH + 1):
			expression = {"kind": "not", "children": [expression]}
		graph = empty_graph("Lead", "trigger.document_change")
		graph["nodes"][0]["config"]["condition"] = expression
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("CONDITION_TOO_DEEP", codes)

	def test_metadata_policy_blocks_security_and_complex_fields(self):
		frappe.set_user("Administrator")
		self.assertFalse(is_eligible_doctype("User"))
		self.assertFalse(is_eligible_doctype("Workflow"))
		self.assertTrue(is_eligible_doctype("Lead"))
		fields = field_catalog("Lead", permission_type="write")
		self.assertTrue(fields)
		self.assertFalse({"Password", "Code", "HTML", "Table", "Table MultiSelect", "Signature"}.intersection({row["fieldtype"] for row in fields}))

		blocked = doctype_eligibility("Access Log", permission_type="read")
		self.assertFalse(blocked["available"])
		self.assertEqual(blocked["reason_code"], "BLOCKED_DOCTYPE")
		result = field_catalog_result("Access Log", permission_type="write")
		self.assertFalse(result["available"])
		self.assertEqual(result["fields"], [])
		api_result = get_fields("Access Log", "read")
		self.assertFalse(api_result["available"])
		self.assertEqual(api_result["reason_code"], "BLOCKED_DOCTYPE")
		create_fields = field_catalog_result("Contact", permission_type="create")["fields"]
		fieldnames = [row["fieldname"] for row in create_fields]
		self.assertEqual(len(fieldnames), len(set(fieldnames)))
		search_result = get_doctypes(search="Lead", permission_type="read", page_length=20)
		self.assertTrue(any(row["name"] == "Lead" for row in search_result["rows"]))
		self.assertTrue(all("lead" in f"{row['name']} {row['module']}".casefold() for row in search_result["rows"]))

	def test_collection_metadata_is_capability_aware(self):
		read_result = field_catalog_result("Opportunity", permission_type="read")
		lost_reasons = next(row for row in read_result["fields"] if row["fieldname"] == "lost_reasons")
		self.assertEqual(lost_reasons["fieldtype"], "Table MultiSelect")
		self.assertEqual(lost_reasons["child_doctype"], "Opportunity Lost Reason Detail")
		self.assertEqual(lost_reasons["link_fieldname"], "lost_reason")
		self.assertEqual(lost_reasons["link_doctype"], "Opportunity Lost Reason")
		self.assertTrue(lost_reasons["capabilities"]["condition_collection"])
		self.assertTrue(lost_reasons["capabilities"]["child_collection"])
		self.assertNotIn("lost_reasons", {row["fieldname"] for row in field_catalog("Opportunity")})

		write_result = field_catalog_result("Quotation", permission_type="write")
		competitors = next(row for row in write_result["fields"] if row["fieldname"] == "competitors")
		self.assertTrue(competitors["capabilities"]["assignment_collection"])
		self.assertEqual(competitors["link_doctype"], "Competitor")

	def test_table_multiselect_assignment_normalizes_and_validates_links(self):
		competitor = frappe.get_doc(
			{"doctype": "Competitor", "competitor_name": f"Automation {frappe.generate_hash(length=8)}"}
		).insert()
		self.assertEqual(
			_coerce_assignment_value("Quotation", "competitors", [competitor.name, competitor.name]),
			[{"competitor": competitor.name}],
		)
		with self.assertRaisesRegex(AutomationError, "does not exist"):
			_coerce_assignment_value("Quotation", "competitors", ["missing-automation-competitor"])

	def test_authoring_schema_rejects_empty_required_literal(self):
		graph = empty_graph("Lead")
		graph["nodes"].append(
			{
				"id": "email",
				"type": "action.send_email",
				"type_version": 1,
				"config": {
					"recipient": {"kind": "literal", "value": ""},
					"subject": {"kind": "literal", "value": "Subject"},
					"message": {"kind": "literal", "value": "Body"},
					"purpose": "workflow",
				},
			}
		)
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "email"}]
		issues = validate_graph(graph)["issues"]
		self.assertTrue(any(issue["code"] == "MISSING_REQUIRED_CONFIG" and issue["path"].endswith("recipient") for issue in issues))

	def test_action_config_and_output_reference_contracts(self):
		graph = {
			"schema_version": 1,
			"primary_doctype": "Lead",
			"start_node_id": "trigger",
			"nodes": [
				{"id": "trigger", "type": "trigger.manual", "type_version": 1, "config": {}},
				{
					"id": "first-action",
					"type": "action.update_record",
					"type_version": 1,
					"config": {"assignments": [{"field": "company_name", "value": {"kind": "literal", "value": "A"}}]},
				},
				{
					"id": "second-action",
					"type": "action.update_record",
					"type_version": 1,
					"config": {
						"assignments": [
							{"field": "company_name", "value": {"kind": "node_output", "node_id": "first-action", "path": "name"}},
							{"field": "company_name", "value": {"kind": "literal", "value": "duplicate"}},
						]
					},
				},
			],
			"edges": [
				{"id": "e1", "source": "trigger", "source_handle": "default", "target": "first-action"},
				{"id": "e2", "source": "first-action", "source_handle": "default", "target": "second-action"},
			],
		}
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("DUPLICATE_ASSIGNMENT", codes)
		self.assertNotIn("UNSAFE_OUTPUT_REFERENCE", codes)

		graph["nodes"][2]["config"]["assignments"] = [
			{"field": "company_name", "value": {"kind": "node_output", "node_id": "second-action", "path": "name"}}
		]
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertIn("UNSAFE_OUTPUT_REFERENCE", codes)

		graph["nodes"][2]["config"]["assignments"] = [
			{"field": "company_name", "value": {"kind": "record_field", "field": "lead_name"}},
			{"field": "first_name", "value": {"kind": "literal", "value": "Megasol"}},
		]
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertNotIn("DUPLICATE_ASSIGNMENT", codes)
		self.assertNotIn("INVALID_VALUE_BINDING", codes)

		graph["nodes"][2]["config"]["assignments"] = [
			{"field": "company_name", "value": {"kind": "node_output", "node_id": "first-action", "path": "missing"}}
		]
		self.assertIn("UNKNOWN_OUTPUT_PATH", {issue["code"] for issue in validate_graph(graph)["issues"]})

		graph["nodes"][2]["type"] = "transform.value"
		graph["nodes"][2]["config"] = {
			"operation": "coalesce",
			"values": [{"kind": "node_output", "node_id": "first-action", "path": "missing"}],
		}
		self.assertIn("UNKNOWN_OUTPUT_PATH", {issue["code"] for issue in validate_graph(graph)["issues"]})

	def test_extended_node_configs_are_validated_independently(self):
		cases = [
			("condition.switch", {}, "MISSING_SWITCH_FIELD"),
			("condition.deduplicate", {}, "MISSING_DEDUPLICATE_FIELD"),
			("delay.until_date", {}, "MISSING_DELAY_FIELD"),
			("delay.until_event", {}, "MISSING_EVENT_TOPIC"),
			("transform.associated_record", {}, "MISSING_ASSOCIATED_FIELD"),
			("transform.child_records", {}, "MISSING_CHILD_FIELD"),
			("action.call_subflow", {}, "MISSING_SUBFLOW"),
			("action.numeric_adjust", {"operation": "divide", "amount": "one"}, "INVALID_NUMERIC_OPERATION"),
			("action.manage_association", {}, "MISSING_ASSOCIATION_VALUE"),
			("action.round_robin", {}, "MISSING_ROUND_ROBIN_GROUP"),
		]
		for node_type, config, expected_code in cases:
			with self.subTest(node_type=node_type):
				graph = empty_graph("Lead")
				graph["nodes"].append({"id": "subject", "type": node_type, "type_version": 1, "config": config})
				graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "subject"}]
				self.assertIn(expected_code, {issue["code"] for issue in validate_graph(graph)["issues"]})

	def test_numeric_adjustment_operations_match_frontend_contract(self):
		self.assertEqual(_calculate_numeric_adjustment(10, "add", 3), 13)
		self.assertEqual(_calculate_numeric_adjustment(10, "subtract", 3), 7)
		self.assertEqual(_calculate_numeric_adjustment(10, "multiply", 3), 30)
		self.assertEqual(_calculate_numeric_adjustment(10, "set", 3), 3)
		with self.assertRaises(AutomationError):
			_calculate_numeric_adjustment(10, "divide", 2)

	def test_non_branch_handle_and_missing_handler_config_are_rejected(self):
		graph = empty_graph("Lead")
		graph["nodes"].append(
			{"id": "todo", "type": "action.create_todo", "type_version": 1, "config": {"priority": "Urgent"}}
		)
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "true", "target": "todo"}]
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertTrue({"INVALID_SOURCE_HANDLE", "MISSING_ASSIGNEE", "MISSING_TODO_DESCRIPTION", "INVALID_TODO_PRIORITY"}.issubset(codes))

	def test_malformed_nested_shapes_return_issues_instead_of_crashing(self):
		graph = {
			"schema_version": 1,
			"primary_doctype": "Lead",
			"start_node_id": "trigger",
			"nodes": [
				{"id": "trigger", "type": "trigger.manual", "type_version": 1, "config": {}},
				{"id": "unknown", "type": {"unexpected": True}, "type_version": 1, "config": []},
				{
					"id": "assignment",
					"type": "action.update_record",
					"type_version": 1,
					"config": {"assignments": [{"field": {"unexpected": True}, "value": []}]},
				},
				{
					"id": "branch",
					"type": "condition.if_else",
					"type_version": 1,
					"config": {"condition": {"kind": "not", "children": {"unexpected": True}}},
				},
			],
			"edges": [
				{"id": "e1", "source": "trigger", "source_handle": {"unexpected": True}, "target": "unknown"},
				{"id": "e2", "source": "unknown", "source_handle": "default", "target": "assignment"},
				{"id": "e3", "source": "assignment", "source_handle": "default", "target": "branch"},
				{"id": "e4", "source": "branch", "source_handle": "true", "target": ["missing"]},
			],
		}
		codes = {issue["code"] for issue in validate_graph(graph)["issues"]}
		self.assertTrue(
			{"UNKNOWN_NODE_TYPE", "INVALID_NODE_CONFIG", "INVALID_ASSIGNMENT", "INVALID_NOT_GROUP", "BROKEN_EDGE"}.issubset(codes)
		)

	def test_call_subflow_and_sms_validation(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "subflow",
			"type": "action.call_subflow",
			"type_version": 1,
			"config": {"subflow_id": "WF-001"}
		})
		graph["nodes"].append({
			"id": "sms",
			"type": "action.send_sms",
			"type_version": 1,
			"config": {"recipient": {"kind": "literal", "value": "123"}, "message": {"kind": "literal", "value": "hello"}, "purpose": "Test", "require_consent": 0}
		})
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "subflow"},
			{"id": "e2", "source": "subflow", "source_handle": "default", "target": "sms"},
		]
		result = validate_graph(graph)
		self.assertTrue(result["valid"])

	def test_removed_custom_script_is_rejected(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "script",
			"type": "action.custom_script",
			"type_version": 1,
			"config": {"script": 'result["ok"] = True\nimport os'},
		})
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "script"}]
		self.assertIn("UNKNOWN_NODE_TYPE", {item["code"] for item in validate_graph(graph)["issues"]})

	def test_delete_record_is_terminal(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{"id": "delete", "type": "action.delete_record", "type_version": 1, "config": {}},
			{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "delete"},
			{"id": "e2", "source": "delete", "source_handle": "default", "target": "end"},
		]
		self.assertIn("DELETE_HAS_EDGE", {item["code"] for item in validate_graph(graph)["issues"]})

	def test_business_hours_configuration_is_typed(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "hours", "type": "delay.business_hours", "type_version": 1,
			"config": {"timezone": "Not/A_Timezone", "start_time": "18:00", "end_time": "09:00", "weekdays": [1, 1, 8]},
		})
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "hours"}]
		codes = {item["code"] for item in validate_graph(graph)["issues"]}
		self.assertTrue({"INVALID_TIMEZONE", "INVALID_BUSINESS_HOURS", "INVALID_BUSINESS_DAYS"}.issubset(codes))
