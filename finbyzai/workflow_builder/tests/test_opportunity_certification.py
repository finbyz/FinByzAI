from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from finbyzai.workflow_builder import bulk, engine, events
from finbyzai.workflow_builder.authoring import (
	create_workflow_record,
	get_workflow_draft,
	publish_workflow,
	save_workflow_draft,
)
from finbyzai.workflow_builder.schema import evaluate_expression


def _node(node_id: str, node_type: str, config: dict | None = None) -> dict:
	return {
		"id": node_id,
		"type": node_type,
		"type_version": 2 if node_type == "action.round_robin" else 1,
		"position": {"x": 0, "y": 0},
		"config": config or {},
	}


def _edge(source: str, target: str, handle: str = "default") -> dict:
	return {
		"id": f"edge-{source}-{handle}-{target}",
		"source": source,
		"source_handle": handle,
		"target": target,
	}


class TestOpportunityWorkflowCertification(IntegrationTestCase):
	"""Exercise workflow behavior against persisted ERPNext Opportunity records."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.flags.in_install = False
		frappe.flags.in_migrate = False
		self.engine_enabled = patch.object(engine, "automation_enabled", return_value=True)
		self.engine_enabled.start()
		self.addCleanup(self.engine_enabled.stop)
		self.engine_gate = patch.object(engine, "workflow_runtime_allowed", return_value=True)
		self.engine_gate.start()
		self.addCleanup(self.engine_gate.stop)
		self.bulk_gate = patch.object(bulk, "workflow_runtime_allowed", return_value=True)
		self.bulk_gate.start()
		self.addCleanup(self.bulk_gate.stop)

	def _opportunity(
		self,
		marker: str,
		*,
		amount: float = 50_000,
		probability: float = 50,
		status: str = "Open",
		capture_subscriptions: bool = False,
	):
		capture_guard = (
			nullcontext()
			if capture_subscriptions
			else patch.object(events, "_matching_subscriptions", return_value=[])
		)
		with capture_guard:
			lead = frappe.get_doc(
				{
					"doctype": "Lead",
					"first_name": marker,
					"company_name": f"{marker} Company",
				}
			).insert()
			opportunity = frappe.get_doc(
				{
					"doctype": "Opportunity",
					"opportunity_from": "Lead",
					"party_name": lead.name,
					"company": "_Test Company",
					"transaction_date": nowdate(),
					"expected_closing": nowdate(),
					"title": marker,
					"status": status,
					"probability": probability,
					"opportunity_amount": amount,
					"items": [{"item_name": f"{marker} Product", "qty": 2, "rate": amount / 2}],
				}
			).insert()
		return opportunity

	def _publish_graph(
		self,
		title: str,
		nodes: list[dict],
		edges: list[dict],
		*,
		trigger_type: str = "trigger.manual",
	):
		created = create_workflow_record(
			f"{title} {frappe.generate_hash(length=8)}",
			"Opportunity",
			trigger_type=trigger_type,
		)
		graph = created["graph"]
		graph["nodes"].extend(nodes)
		graph["edges"] = edges
		saved = save_workflow_draft(created["workflow"], 0, graph)
		self.assertTrue(saved["valid"], saved["validation"])
		published = publish_workflow(created["workflow"], saved["draft_revision"], reenrollment="ALWAYS")
		return created, published

	def _drain(self, run_name: str) -> None:
		with patch.object(events, "_matching_subscriptions", return_value=[]):
			while token_name := frappe.db.get_value(
				"Automation Run Token", {"run": run_name, "status": "READY"}, "name", order_by="creation asc"
			):
				engine.execute_token(token_name)

	def _output(self, run_name: str, node_id: str) -> dict:
		value = frappe.db.get_value(
			"Automation Run Token", {"run": run_name, "node_id": node_id}, "output_json"
		)
		return frappe.parse_json(value or "{}")

	def test_persisted_opportunity_condition_operator_matrix(self):
		reason_name = f"Certification {frappe.generate_hash(length=8)}"
		frappe.get_doc({"doctype": "Opportunity Lost Reason", "lost_reason": reason_name}).insert()
		opportunity = self._opportunity(f"Condition matrix {frappe.generate_hash(length=8)}")
		with patch.object(events, "_matching_subscriptions", return_value=[]):
			opportunity.append("lost_reasons", {"lost_reason": reason_name})
			opportunity.save()
		opportunity = frappe.get_doc("Opportunity", opportunity.name)

		predicates = [
			("eq", "status", "Open", True),
			("ne", "status", "Lost", True),
			("gt", "opportunity_amount", 49_999, True),
			("gte", "opportunity_amount", 50_000, True),
			("lt", "opportunity_amount", 50_001, True),
			("lte", "opportunity_amount", 50_000, True),
			("in", "status", ["Open", "Lost"], True),
			("not_in", "status", ["Lost", "Closed"], True),
			("contains", "title", "condition MATRIX", True),
			("not_contains", "title", "missing text", True),
			("is_set", "title", None, True),
			("is_not_set", "annual_revenue", None, True),
			("contains_any", "lost_reasons", [reason_name, "Other"], True),
			("contains_all", "lost_reasons", [reason_name], True),
			("contains_none", "lost_reasons", ["Other"], True),
		]
		for operator, field, value, expected in predicates:
			with self.subTest(operator=operator, field=field):
				actual = evaluate_expression(
					{"kind": "predicate", "field": field, "operator": operator, "value": value}, opportunity
				)
				self.assertEqual(actual, expected)

		# Frappe reloads an unset optional Currency as zero. It must still be
		# treated as unset, which is the reported annual_revenue regression.
		self.assertEqual(opportunity.annual_revenue, 0)
		self.assertFalse(
			evaluate_expression(
				{"kind": "predicate", "field": "annual_revenue", "operator": "is_set"}, opportunity
			)
		)

	def test_publish_run_edit_publish_and_rerun_real_opportunity(self):
		marker = f"Edit cycle {frappe.generate_hash(length=8)}"
		opportunity = self._opportunity(marker)
		stage = frappe.get_doc(
			{"doctype": "Sales Stage", "stage_name": f"Certified {frappe.generate_hash(length=8)}"}
		).insert()
		nodes = [
			_node(
				"amount-branch",
				"condition.if_else",
				{
					"condition": {
						"kind": "predicate",
						"field": "opportunity_amount",
						"operator": "gte",
						"value": 40_000,
					}
				},
			),
			_node(
				"compose-title",
				"transform.value",
				{
					"operation": "concat",
					"separator": "",
					"values": [
						{"kind": "record_field", "field": "title"},
						{"kind": "literal", "value": " / V1 certified"},
					],
				},
			),
			_node(
				"company-name",
				"transform.associated_record",
				{"reference_field": "company", "fetch_field": "company_name"},
			),
			_node(
				"item-names",
				"transform.child_records",
				{"child_table_field": "items", "fetch_field": "item_name"},
			),
			_node(
				"update-title",
				"action.update_record",
				{
					"assignments": [
						{
							"field": "title",
							"value": {"kind": "node_output", "node_id": "compose-title", "path": "value"},
						}
					]
				},
			),
			_node("increase-probability", "action.numeric_adjust", {"field": "probability", "operation": "add", "amount": 5}),
			_node(
				"link-stage",
				"action.manage_association",
				{
					"target_doctype": "Sales Stage",
					"target_name": stage.name,
					"link_field": "sales_stage",
					"operation": "link",
				},
			),
			_node(
				"create-note",
				"action.create_record",
				{
					"target_doctype": "Note",
					"assignments": [
						{
							"field": "title",
							"value": {"kind": "node_output", "node_id": "compose-title", "path": "value"},
						}
					],
				},
			),
			_node("v1-comment", "action.add_comment", {"content": "Opportunity certification V1 true branch"}),
			_node(
				"notify-owner",
				"action.notify_user",
				{
					"for_user": "Administrator",
					"subject": "Opportunity certification",
					"message": "V1 true branch completed",
				},
			),
			_node("false-comment", "action.add_comment", {"content": "Opportunity certification V1 false branch"}),
			_node("true-end", "end.complete"),
			_node("false-end", "end.complete"),
		]
		true_sequence = [
			"compose-title",
			"company-name",
			"item-names",
			"update-title",
			"increase-probability",
			"link-stage",
			"create-note",
			"v1-comment",
			"notify-owner",
			"true-end",
		]
		edges = [_edge("trigger-1", "amount-branch")]
		edges.append(_edge("amount-branch", true_sequence[0], "true"))
		edges.extend(_edge(source, target) for source, target in zip(true_sequence, true_sequence[1:]))
		edges.extend(
			[
				_edge("amount-branch", "false-comment", "false"),
				_edge("false-comment", "false-end"),
			]
		)
		created, first = self._publish_graph("Opportunity versioned certification", nodes, edges)

		first_run = engine.enroll(
			created["workflow"], "Opportunity", opportunity.name, source="MANUAL", occurrence_key="v1"
		)
		self._drain(first_run)
		self.assertEqual(frappe.db.get_value("Automation Run", first_run, "status"), "COMPLETED")
		self.assertEqual(self._output(first_run, "company-name")["value"], "_Test Company")
		self.assertEqual(self._output(first_run, "item-names")["values"], [f"{marker} Product"])
		opportunity.reload()
		self.assertEqual(opportunity.title, f"{marker} / V1 certified")
		self.assertEqual(opportunity.probability, 55)
		self.assertEqual(opportunity.sales_stage, stage.name)
		created_note = self._output(first_run, "create-note")["name"]
		self.assertEqual(frappe.db.get_value("Note", created_note, "title"), f"{marker} / V1 certified")
		self.assertEqual(
			frappe.db.count(
				"Notification Log",
				{
					"for_user": "Administrator",
					"subject": "Opportunity certification",
					"document_type": "Opportunity",
					"document_name": opportunity.name,
				},
			),
			1,
		)

		draft = get_workflow_draft(created["workflow"])["draft"]
		branch = next(node for node in draft["graph"]["nodes"] if node["id"] == "amount-branch")
		branch["config"]["condition"]["value"] = 100_000
		false_comment = next(node for node in draft["graph"]["nodes"] if node["id"] == "false-comment")
		false_comment["config"]["content"] = "Opportunity certification V2 false branch"
		saved = save_workflow_draft(
			created["workflow"], draft["draft_revision"], draft["graph"]
		)
		second = publish_workflow(created["workflow"], saved["draft_revision"], reenrollment="ALWAYS")
		self.assertNotEqual(first["version"], second["version"])

		second_run = engine.enroll(
			created["workflow"], "Opportunity", opportunity.name, source="MANUAL", occurrence_key="v2"
		)
		self._drain(second_run)
		self.assertEqual(frappe.db.get_value("Automation Run", second_run, "status"), "COMPLETED")
		self.assertEqual(frappe.db.get_value("Automation Run", first_run, "workflow_version"), first["version"])
		self.assertEqual(frappe.db.get_value("Automation Run", second_run, "workflow_version"), second["version"])
		self.assertFalse(frappe.db.exists("Automation Run Token", {"run": second_run, "node_id": "compose-title"}))
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Opportunity",
					"reference_name": opportunity.name,
					"content": ["like", "%Opportunity certification V2 false branch%"],
				},
			)
		)
		opportunity.reload()
		self.assertEqual(opportunity.probability, 55)
		self.assertEqual(frappe.db.count("Note", {"title": f"{marker} / V1 certified"}), 1)

	def test_switch_deduplicate_and_default_paths(self):
		marker = f"Duplicate title {frappe.generate_hash(length=8)}"
		first_opportunity = self._opportunity(marker)
		duplicate_opportunity = self._opportunity(marker)
		unique_opportunity = self._opportunity(f"Unique title {frappe.generate_hash(length=8)}")
		closed_opportunity = self._opportunity(
			f"Closed title {frappe.generate_hash(length=8)}", status="Closed"
		)
		nodes = [
			_node("status-switch", "condition.switch", {"field": "status", "cases": [{"value": "Open", "handle": "open"}]}),
			_node("title-dedupe", "condition.deduplicate", {"match_field": "title"}),
			_node("duplicate-end", "end.complete"),
			_node("unique-end", "end.complete"),
			_node("default-end", "end.complete"),
		]
		edges = [
			_edge("trigger-1", "status-switch"),
			_edge("status-switch", "title-dedupe", "open"),
			_edge("status-switch", "default-end", "default"),
			_edge("title-dedupe", "duplicate-end", "duplicate"),
			_edge("title-dedupe", "unique-end", "unique"),
		]
		created, _published = self._publish_graph("Opportunity switch dedupe", nodes, edges)
		for key, opportunity in (
			("first", first_opportunity),
			("duplicate", duplicate_opportunity),
			("unique", unique_opportunity),
			("default", closed_opportunity),
		):
			run = engine.enroll(
				created["workflow"], "Opportunity", opportunity.name, source="MANUAL", occurrence_key=key
			)
			self._drain(run)
			self.assertEqual(frappe.db.get_value("Automation Run", run, "status"), "COMPLETED")
			if key == "default":
				self.assertTrue(frappe.db.exists("Automation Run Token", {"run": run, "node_id": "default-end"}))
			elif key == "unique":
				self.assertFalse(self._output(run, "title-dedupe")["is_duplicate"])
				self.assertTrue(frappe.db.exists("Automation Run Token", {"run": run, "node_id": "unique-end"}))
			else:
				self.assertTrue(self._output(run, "title-dedupe")["is_duplicate"])

	def test_date_business_hours_event_and_timeout_combinations(self):
		opportunity = self._opportunity(f"Delay paths {frappe.generate_hash(length=8)}")
		for timezone_name in ("UTC", "Europe/Zurich", "Asia/Kolkata", "America/New_York", "Pacific/Honolulu"):
			local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_name))
			minutes = local_now.hour * 60 + local_now.minute
			if 1 <= minutes <= 1437:
				break
		start_time = f"{(minutes - 1) // 60:02d}:{(minutes - 1) % 60:02d}"
		end_time = f"{(minutes + 1) // 60:02d}:{(minutes + 1) % 60:02d}"
		nodes = [
			_node("until-closing", "delay.until_date", {"field": "expected_closing"}),
			_node(
				"business-hours",
				"delay.business_hours",
				{
					"timezone": timezone_name,
					"start_time": start_time,
					"end_time": end_time,
					"weekdays": [0, 1, 2, 3, 4, 5, 6],
				},
			),
			_node("date-end", "end.complete"),
		]
		created, _published = self._publish_graph(
			"Opportunity date and business hours",
			nodes,
			[
				_edge("trigger-1", "until-closing"),
				_edge("until-closing", "business-hours"),
				_edge("business-hours", "date-end"),
			],
		)
		run = engine.enroll(
			created["workflow"], "Opportunity", opportunity.name, source="MANUAL", occurrence_key="date-hours"
		)
		self._drain(run)
		self.assertEqual(frappe.db.get_value("Automation Run", run, "status"), "COMPLETED")
		self.assertTrue(self._output(run, "until-closing")["released"])
		self.assertTrue(self._output(run, "business-hours")["released"])

		event_nodes = [
			_node("wait-event", "delay.until_event", {"event_topic": "opportunity.certified", "timeout_seconds": 3600}),
			_node("event-end", "end.complete"),
			_node("timeout-end", "end.complete"),
		]
		event_workflow, _published = self._publish_graph(
			"Opportunity event and timeout",
			event_nodes,
			[
				_edge("trigger-1", "wait-event"),
				_edge("wait-event", "event-end", "event"),
				_edge("wait-event", "timeout-end", "timeout"),
			],
		)
		for occurrence in ("event", "timeout"):
			waiting_run = engine.enroll(
				event_workflow["workflow"],
				"Opportunity",
				opportunity.name,
				source="MANUAL",
				occurrence_key=occurrence,
			)
			self._drain(waiting_run)
			self.assertEqual(frappe.db.get_value("Automation Run", waiting_run, "status"), "WAITING")
			if occurrence == "event":
				with patch.object(engine, "_queue_token"):
					self.assertEqual(engine.release_event_waiters("opportunity.certified", {"name": opportunity.name}), 1)
			else:
				frappe.db.set_value("Automation Timer", {"run": waiting_run}, "due_at", "2000-01-01 00:00:00")
				with patch.object(engine, "_queue_token"):
					self.assertEqual(engine.release_due_timers(), 1)
			self._drain(waiting_run)
			self.assertEqual(frappe.db.get_value("Automation Run", waiting_run, "status"), "COMPLETED")
			self.assertTrue(
				frappe.db.exists(
					"Automation Run Token",
					{"run": waiting_run, "node_id": f"{occurrence}-end"},
				)
			)

	def test_insert_change_and_schedule_sources(self):
		insert_marker = f"Insert trigger {frappe.generate_hash(length=8)}"
		insert_created, _published = self._publish_graph(
			"Opportunity insert trigger",
			[
				_node("insert-comment", "action.add_comment", {"content": "Opportunity insert trigger executed"}),
				_node("insert-end", "end.complete"),
			],
			[_edge("trigger-1", "insert-comment"), _edge("insert-comment", "insert-end")],
			trigger_type="trigger.document_insert",
		)
		insert_draft = get_workflow_draft(insert_created["workflow"])["draft"]
		insert_draft["graph"]["nodes"][0]["config"] = {
			"condition": {"kind": "predicate", "field": "title", "operator": "eq", "value": insert_marker}
		}
		insert_saved = save_workflow_draft(
			insert_created["workflow"], insert_draft["draft_revision"], insert_draft["graph"]
		)
		publish_workflow(insert_created["workflow"], insert_saved["draft_revision"], reenrollment="ALWAYS")
		insert_subscriptions = [
			row
			for row in events._matching_subscriptions("Opportunity", "AFTER_INSERT")
			if row.workflow == insert_created["workflow"]
		]
		with patch.object(
			events,
			"_matching_subscriptions",
			side_effect=lambda doctype, event_type: insert_subscriptions
			if doctype == "Opportunity" and event_type == "AFTER_INSERT"
			else [],
		):
			inserted = self._opportunity(insert_marker, capture_subscriptions=True)
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Opportunity", "object_name": inserted.name, "event_type": "AFTER_INSERT"},
			"name",
		)
		self.assertTrue(event_name)
		with patch.object(events, "_matching_subscriptions", return_value=insert_subscriptions):
			self.assertEqual(events.process_outbox_event(event_name), 1)
		insert_run = frappe.db.get_value(
			"Automation Run", {"workflow": insert_created["workflow"], "record_name": inserted.name}, "name"
		)
		self._drain(insert_run)
		self.assertEqual(frappe.db.get_value("Automation Run", insert_run, "source"), "AFTER_INSERT")

		changed = self._opportunity(f"Change trigger {frappe.generate_hash(length=8)}", probability=20)
		change_created, _published = self._publish_graph(
			"Opportunity change trigger",
			[
				_node("change-comment", "action.add_comment", {"content": "Opportunity change trigger executed"}),
				_node("change-end", "end.complete"),
			],
			[_edge("trigger-1", "change-comment"), _edge("change-comment", "change-end")],
			trigger_type="trigger.document_change",
		)
		change_draft = get_workflow_draft(change_created["workflow"])["draft"]
		change_draft["graph"]["nodes"][0]["config"] = {
			"condition": {"kind": "predicate", "field": "probability", "operator": "gte", "value": 80}
		}
		change_saved = save_workflow_draft(
			change_created["workflow"], change_draft["draft_revision"], change_draft["graph"]
		)
		publish_workflow(change_created["workflow"], change_saved["draft_revision"], reenrollment="ALWAYS")
		change_subscriptions = [
			row
			for row in events._matching_subscriptions("Opportunity", "ON_UPDATE")
			if row.workflow == change_created["workflow"]
		]
		with patch.object(
			events,
			"_matching_subscriptions",
			side_effect=lambda doctype, event_type: change_subscriptions
			if doctype == "Opportunity" and event_type == "ON_UPDATE"
			else [],
		):
			changed.probability = 90
			changed.save()
		change_event = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Opportunity", "object_name": changed.name, "event_type": "ON_UPDATE"},
			"name",
			order_by="creation desc",
		)
		self.assertTrue(change_event)
		with patch.object(events, "_matching_subscriptions", return_value=change_subscriptions):
			self.assertEqual(events.process_outbox_event(change_event), 1)
		change_run = frappe.db.get_value(
			"Automation Run", {"workflow": change_created["workflow"], "record_name": changed.name}, "name"
		)
		self._drain(change_run)
		self.assertEqual(frappe.db.get_value("Automation Run", change_run, "source"), "ON_UPDATE")

		scheduled = self._opportunity(f"Schedule trigger {frappe.generate_hash(length=8)}")
		schedule_created, _published = self._publish_graph(
			"Opportunity schedule trigger",
			[
				_node("schedule-comment", "action.add_comment", {"content": "Opportunity schedule trigger executed"}),
				_node("schedule-end", "end.complete"),
			],
			[_edge("trigger-1", "schedule-comment"), _edge("schedule-comment", "schedule-end")],
			trigger_type="trigger.schedule",
		)
		with patch.object(bulk, "_queue_backfill"):
			schedule = bulk.create_schedule(
				schedule_created["workflow"],
				"DAILY",
				"2099-01-01 00:00:00",
				filters=[["name", "=", scheduled.name]],
			)
			job = bulk.create_backfill(
				schedule_created["workflow"],
				[["name", "=", scheduled.name]],
				source="SCHEDULE",
				schedule=schedule["schedule_id"],
			)
			self.assertEqual(bulk.process_backfill(job["backfill_id"]), 1)
		schedule_run = frappe.db.get_value(
			"Automation Run", {"workflow": schedule_created["workflow"], "record_name": scheduled.name}, "name"
		)
		self._drain(schedule_run)
		self.assertEqual(frappe.db.get_value("Automation Run", schedule_run, "source"), "SCHEDULE")

	def test_controlled_external_chain_and_disposable_delete(self):
		opportunity = self._opportunity(f"External chain {frappe.generate_hash(length=8)}")
		secret = frappe.get_doc(
			{
				"doctype": "Automation Integration Secret",
				"title": f"Certification {frappe.generate_hash(length=8)}",
				"enabled": 1,
				"auth_type": "None",
				"allowed_hosts": "api.example.com",
				"requests_per_minute": 60,
			}
		).insert()
		nodes = [
			_node(
				"email",
				"action.send_email",
				{
					"recipient": {"kind": "literal", "value": "certification@example.com"},
					"subject": {"kind": "literal", "value": "Opportunity certification"},
					"message": {"kind": "literal", "value": "Controlled delivery"},
					"purpose": "certification",
					"require_consent": 0,
				},
			),
			_node(
				"sms",
				"action.send_sms",
				{
					"recipient": {"kind": "literal", "value": "+41791234567"},
					"message": {"kind": "literal", "value": "Controlled delivery"},
					"purpose": "certification",
					"require_consent": 0,
				},
			),
			_node(
				"webhook",
				"action.webhook",
				{
					"integration_secret": secret.name,
					"url": "https://api.example.com/opportunity",
					"payload": {"event": "certification"},
					"purpose": "certification",
					"require_consent": 0,
				},
			),
			_node("external-end", "end.complete"),
		]
		created, _published = self._publish_graph(
			"Opportunity controlled external",
			nodes,
			[
				_edge("trigger-1", "email"),
				_edge("email", "sms"),
				_edge("sms", "webhook"),
				_edge("webhook", "external-end"),
			],
		)
		run = engine.enroll(
			created["workflow"], "Opportunity", opportunity.name, source="MANUAL", occurrence_key="external"
		)
		with patch(
			"finbyzai.workflow_builder.external.execute_external",
			side_effect=lambda node_type, *_args, **_kwargs: {
				"status": "COMPLETE",
				"output": {"transport": node_type, "controlled": True},
			},
		):
			for _index in range(10):
				self._drain(run)
				if frappe.db.get_value("Automation Run", run, "status") == "COMPLETED":
					break
				waiting_token = frappe.db.get_value(
					"Automation Run Token", {"run": run, "status": "WAITING"}, "name"
				)
				ledger = frappe.db.get_value(
					"Automation Effect Ledger", {"run": run, "status": "PENDING"}, "name"
				)
				self.assertTrue(waiting_token and ledger)
				engine.execute_external_effect(ledger, waiting_token)
		self.assertEqual(frappe.db.get_value("Automation Run", run, "status"), "COMPLETED")
		self.assertEqual(frappe.db.count("Automation Effect Ledger", {"run": run, "status": "COMPLETED"}), 3)
		self.assertEqual(frappe.db.count("Automation Action Attempt", {"run": run, "status": "COMPLETED"}), 5)

		disposable = self._opportunity(f"Disposable {frappe.generate_hash(length=8)}")
		delete_created, _published = self._publish_graph(
			"Opportunity disposable delete",
			[_node("delete", "action.delete_record")],
			[_edge("trigger-1", "delete")],
		)
		delete_run = engine.enroll(
			delete_created["workflow"],
			"Opportunity",
			disposable.name,
			source="MANUAL",
			occurrence_key="delete",
		)
		self._drain(delete_run)
		delete_state = frappe.db.get_value(
			"Automation Run", delete_run, ["status", "error_code", "error_message"], as_dict=True
		)
		self.assertEqual(delete_state.status, "COMPLETED", delete_state)
		self.assertFalse(frappe.db.exists("Opportunity", disposable.name))
