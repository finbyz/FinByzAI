from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder import engine, maintenance
from finbyzai.workflow_builder.api import get_canvas_metrics, simulate

from finbyzai.workflow_builder.authoring import (
	change_workflow_state,
	compare_versions,
	create_workflow_record,
	delete_workflow_record,
	get_workflow_draft,
	list_workflow_records,
	publish_workflow,
	save_workflow_draft,
	set_workflow_folder,
	validate_bindings,
	validate_published_version,
	validate_settings,
	validate_workflow_draft,
)
from finbyzai.workflow_builder.engine import (
	_SET_USER_LOCAL_FIELDS,
	_assert_worker_execution,
	_business_hours_state,
	_enabled_user_names,
	_execute_node,
	_execution_identity,
	_hold_for_execution_window,
	_reserve_drip_slot,
	_round_robin_users,
	_transform_output,
	enroll,
	execute_token,
	release_due_timers,
	release_event_waiters,
	recover_stale_external_effects,
)
from finbyzai.workflow_builder.errors import AutomationConflictError, AutomationError, AutomationPermissionError
from finbyzai.workflow_builder.registry import field_catalog_result
from finbyzai.workflow_builder.schema import empty_graph
from finbyzai.workflow_builder.setup import (
	ensure_automation_roles,
	ensure_automation_settings_defaults,
	quarantine_invalid_active_versions,
)


class TestAutomationAuthoring(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.enabled = patch.object(engine, "automation_enabled", return_value=True)
		self.enabled.start()
		self.addCleanup(self.enabled.stop)
		self.runtime_allowed = patch.object(engine, "workflow_runtime_allowed", return_value=True)
		self.runtime_allowed.start()
		self.addCleanup(self.runtime_allowed.stop)

	def add_terminal_step(self, workflow_name: str, draft_revision: int) -> dict:
		graph = get_workflow_draft(workflow_name)["draft"]["graph"]
		graph["nodes"].append(
			{"id": "end-1", "type": "end.complete", "type_version": 1, "position": {"x": 360, "y": 160}, "config": {}}
		)
		graph["edges"] = [
			{"id": "edge-1", "source": graph["start_node_id"], "source_handle": "default", "target": "end-1"}
		]
		return save_workflow_draft(workflow_name, draft_revision, graph)

	def test_workflow_search_and_pagination_contract(self):
		rows = [frappe._dict(name="WF-1"), frappe._dict(name="WF-2")]
		counts = [frappe._dict(status="ACTIVE", count=1), frappe._dict(status="DRAFT", count=1)]
		with patch.object(frappe, "get_list", side_effect=[rows, counts]) as get_list:
			result = list_workflow_records(start=0, page_length=1, search="invoice")

		self.assertEqual(result["rows"], [{"name": "WF-1", "trigger_type": None}])
		self.assertTrue(result["has_more"])
		self.assertEqual(result["total_count"], 2)
		self.assertEqual(result["status_counts"], {"ACTIVE": 1, "PAUSED": 0})
		kwargs = get_list.call_args_list[0].kwargs
		self.assertEqual(kwargs["limit"], 2)
		self.assertEqual(kwargs["or_filters"]["title"], ["like", "%invoice%"])
		self.assertEqual(kwargs["or_filters"]["folder"], ["like", "%invoice%"])
		self.assertEqual(kwargs["or_filters"]["primary_doctype"], ["like", "%invoice%"])
		self.assertEqual(get_list.call_count, 2)
		self.assertEqual(get_list.call_args_list[1].kwargs["group_by"], "status")

	def test_workflow_folder_is_created_moved_and_filterable(self):
		created = create_workflow_record("Folder contract", "Lead", folder="Sales/Nurture")
		self.assertEqual(frappe.db.get_value("Automation Workflow", created["workflow"], "folder"), "Sales/Nurture")
		self.assertEqual(set_workflow_folder(created["workflow"], "Marketing/Qualified")["folder"], "Marketing/Qualified")
		rows = list_workflow_records(folder="Marketing/Qualified")["rows"]
		self.assertIn(created["workflow"], {row.name for row in rows})
		with self.assertRaisesRegex(AutomationError, "dot path"):
			set_workflow_folder(created["workflow"], "../Unsafe")

	def test_workflow_list_bulk_loads_published_graphs(self):
		graph = empty_graph("Lead", "trigger.record_created")
		rows = [
			frappe._dict(name=f"WF-{index}", active_version=f"AWV-{index}")
			for index in range(1, 21)
		]
		versions = [
			frappe._dict(name=f"AWV-{index}", graph_json=frappe.as_json(graph))
			for index in range(1, 21)
		]
		with (
			patch.object(
				frappe,
				"get_list",
				side_effect=[rows, [frappe._dict(status="ACTIVE", count=20)]],
			),
			patch.object(frappe, "get_all", return_value=versions) as get_all,
		):
			result = list_workflow_records(page_length=20)

		self.assertEqual(len(result["rows"]), 20)
		self.assertEqual({row.trigger_type for row in result["rows"]}, {"trigger.record_created"})
		get_all.assert_called_once()

	def test_create_record_requires_mandatory_fields_without_defaults(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "create-order",
			"type": "action.create_record",
			"type_version": 1,
			"config": {"target_doctype": "Sales Order", "assignments": []},
		})
		graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "create-order"}]
		issues = validate_bindings(graph, "Administrator")
		mandatory = [issue for issue in issues if issue["code"] == "MISSING_MANDATORY_CREATE_FIELD"]
		self.assertTrue(any("Customer" in issue["message"] for issue in mandatory))
		unsupported = [issue for issue in issues if issue["code"] == "UNSUPPORTED_MANDATORY_CREATE_FIELD"]
		self.assertTrue(any("Items" in issue["message"] for issue in unsupported))

	def test_update_record_blocks_clearing_a_mandatory_frappe_field(self):
		for assignment in (
			{"field": "status", "operation": "clear"},
			{"field": "status", "operation": "set", "value": {"kind": "literal", "value": ""}},
		):
			graph = empty_graph("Lead")
			graph["nodes"].append({
				"id": "update",
				"type": "action.update_record",
				"type_version": 1,
				"config": {"assignments": [assignment]},
			})
			graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "update"}]
			with self.subTest(assignment=assignment):
				issues = validate_bindings(graph, "Administrator")
				self.assertIn("MANDATORY_FIELD_CLEAR", {issue["code"] for issue in issues})

		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "update",
			"type": "action.update_record",
			"type_version": 1,
			"config": {"assignments": [{"field": "company_name", "operation": "clear"}]},
		})
		graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "update"}]
		self.assertNotIn(
			"MANDATORY_FIELD_CLEAR",
			{issue["code"] for issue in validate_bindings(graph, "Administrator")},
		)

	def test_literal_date_wait_does_not_require_record_field_access(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "wait",
			"type": "delay.until_date",
			"type_version": 1,
			"config": {"mode": "literal", "datetime": "2026-12-15 14:30:00", "field": ""},
		})
		graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "wait"}]
		issues = validate_bindings(graph, "Administrator")
		self.assertNotIn("DELAY_FIELD_PERMISSION", {issue["code"] for issue in issues})

	def test_email_subscription_topic_must_be_an_enabled_reach_topic(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "email",
			"type": "action.send_email",
			"type_version": 2,
			"config": {
				"content_mode": "inline",
				"recipient": {"kind": "record_field", "field": "email_id"},
				"subject": {"kind": "literal", "value": "Hello"},
				"message": {"kind": "literal", "value": "Body"},
				"subscription_topic": "Disabled topic",
			},
		})
		graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "email"}]
		with (
			patch.object(frappe, "get_installed_apps", return_value=["finbyzreach"]),
			patch.object(frappe.db, "exists", return_value=False),
		):
			issues = validate_bindings(graph, "Administrator")

		self.assertIn("INVALID_SUBSCRIPTION_TOPIC", {issue["code"] for issue in issues})
		self.assertIn("nodes.email.config.subscription_topic", {issue["path"] for issue in issues})

	def test_branch_field_permissions_skip_blank_rows_and_point_to_the_affected_path(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "branch",
			"type": "condition.if_else",
			"type_version": 2,
			"config": {"branches": [
				{"handle": "blank", "name": "Blank", "condition": {"kind": "predicate", "field": "", "operator": "eq", "value": None}},
				{"handle": "invalid", "name": "Invalid", "condition": {"kind": "predicate", "field": "missing_private_field", "operator": "eq", "value": "x"}},
			]},
		})
		issues = [issue for issue in validate_bindings(graph, "Administrator") if issue["code"] == "FIELD_PERMISSION"]
		self.assertEqual(len(issues), 1)
		self.assertEqual(issues[0]["path"], "nodes.branch.config.branches.1.condition")

	def test_creation_idempotency_is_durable(self):
		key = frappe.generate_hash(length=20)
		first = create_workflow_record("Idempotent create", "Lead", idempotency_key=key)
		second = create_workflow_record("Ignored retry title", "Lead", idempotency_key=key)
		self.assertEqual(first["workflow"], second["workflow"])
		self.assertTrue(second["deduplicated"])
		self.assertEqual(frappe.db.count("Automation Workflow", {"creation_key": ["is", "set"], "name": first["workflow"]}), 1)

	def test_inactive_publish_preserves_active_version_and_subscription(self):
		created = create_workflow_record("Inactive publication", "Lead", trigger_type="trigger.document_insert")
		first = publish_workflow(created["workflow"], 0)
		saved = self.add_terminal_step(created["workflow"], 0)
		second = publish_workflow(created["workflow"], saved["draft_revision"], activate=False)
		workflow = frappe.get_doc("Automation Workflow", created["workflow"])
		self.assertEqual((workflow.status, workflow.active_version), ("ACTIVE", first["version"]))
		self.assertEqual(frappe.db.get_value("Automation Trigger Subscription", {"workflow_version": first["version"]}, "active"), 1)
		self.assertEqual(frappe.db.get_value("Automation Trigger Subscription", {"workflow_version": second["version"]}, "active"), 0)

	def test_replication_activates_only_latest_subscription(self):
		created = create_workflow_record("Republish trigger", "Lead", trigger_type="trigger.document_insert")
		first = publish_workflow(created["workflow"], 0)
		saved = self.add_terminal_step(created["workflow"], 0)
		second = publish_workflow(created["workflow"], saved["draft_revision"])
		self.assertEqual(
			frappe.db.count("Automation Trigger Subscription", {"workflow": created["workflow"], "active": 1}),
			1,
		)
		self.assertEqual(frappe.db.get_value("Automation Trigger Subscription", {"workflow": created["workflow"], "active": 1}, "workflow_version"), second["version"])
		self.assertNotEqual(first["version"], second["version"])

	def test_mixed_trigger_publication_creates_one_active_subscription_per_or_trigger(self):
		created = create_workflow_record("Mixed trigger publication", "Lead", trigger_type="trigger.any")
		graph = created["graph"]
		graph["nodes"][0]["config"] = {
			"triggers": [
				{"id": "created", "type": "trigger.document_insert", "config": {"condition": None}},
				{"id": "qualified", "type": "trigger.event", "config": {"event_topic": "crm.lead.qualified", "event_filter": None, "condition": None}},
			]
		}
		saved = save_workflow_draft(created["workflow"], 0, graph)
		published = publish_workflow(created["workflow"], saved["draft_revision"])
		subscriptions = frappe.get_all(
			"Automation Trigger Subscription",
			filters={"workflow_version": published["version"], "active": 1},
			fields=["event_type", "config_json"],
			order_by="event_type asc",
		)
		self.assertEqual([row.event_type for row in subscriptions], ["AFTER_INSERT", "EVENT"])
		self.assertEqual({frappe.parse_json(row.config_json)["_trigger_group_id"] for row in subscriptions}, {"created", "qualified"})

	def test_drip_cursor_batches_are_durable_and_transform_operations_are_deterministic(self):
		created = create_workflow_record("Drip cursor contract", "Lead")
		published = publish_workflow(created["workflow"], 0)
		run = SimpleNamespace(workflow_version=published["version"])
		node = {"id": frappe.generate_hash(length=10)}
		first = _reserve_drip_slot(run, node, {"batch_size": 2, "interval_seconds": 3600})
		second = _reserve_drip_slot(run, node, {"batch_size": 2, "interval_seconds": 3600})
		third = _reserve_drip_slot(run, node, {"batch_size": 2, "interval_seconds": 3600})
		self.assertTrue(first["released"])
		self.assertTrue(second["released"])
		self.assertFalse(third["released"])
		self.assertEqual((first["position"], second["position"], third["position"]), (1, 2, 1))
		self.assertEqual(_transform_output({"operation": "parse_number"}, ["1.234,50"], seed="x"), 1234.5)
		self.assertEqual(_transform_output({"operation": "format_currency", "currency": "CHF", "precision": 2}, [12.5], seed="x"), "CHF 12.50")
		self.assertEqual(
			_transform_output({"operation": "random_number", "minimum": 1, "maximum": 10, "integer": 1}, [], seed="stable"),
			_transform_output({"operation": "random_number", "minimum": 1, "maximum": 10, "integer": 1}, [], seed="stable"),
		)

	def test_execution_history_retention_has_a_six_month_floor_and_preserves_enrollment_identity(self):
		with patch.object(frappe.db, "get_single_value", return_value=30):
			self.assertEqual(maintenance.history_retention_days(), 180)

		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Retention Contract"}).insert()
		created = create_workflow_record("Retention contract", "Lead")
		publish_workflow(created["workflow"], 0)
		run_name = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="retention")
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "status": "READY"}, "name"))
		frappe.db.set_value(
			"Automation Run",
			run_name,
			"completed_at",
			frappe.utils.add_days(frappe.utils.now_datetime(), -181),
			update_modified=False,
		)
		self.assertEqual(maintenance.purge_expired_execution_history(), 1)
		self.assertFalse(frappe.db.exists("Automation Run", run_name))
		self.assertIsNone(
			frappe.db.get_value(
				"Automation Enrollment Ledger",
				{"workflow": created["workflow"], "record_name": lead.name, "occurrence_key": "retention"},
				"run",
			)
		)

	def test_log_cleanup_removes_only_expired_terminal_operational_logs(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Cleanup Log Contract"}).insert()
		created = create_workflow_record("Cleanup log contract", "Lead")
		audit_name = frappe.db.get_value(
			"Automation Audit Event", {"workflow": created["workflow"]}, "name", order_by="creation asc"
		)
		old_at = frappe.utils.add_days(frappe.utils.now_datetime(), -181)
		frappe.db.set_value("Automation Audit Event", audit_name, "occurred_at", old_at, update_modified=False)
		processed = frappe.get_doc(
			{
				"doctype": "Automation Outbox Event",
				"event_id": frappe.generate_hash(length=20),
				"event_type": "ON_UPDATE",
				"object_doctype": "Lead",
				"object_name": lead.name,
				"status": "PROCESSED",
				"processed_at": old_at,
			}
		).insert(ignore_permissions=True)
		pending = frappe.get_doc(
			{
				"doctype": "Automation Outbox Event",
				"event_id": frappe.generate_hash(length=20),
				"event_type": "ON_UPDATE",
				"object_doctype": "Lead",
				"object_name": lead.name,
				"status": "PENDING",
				"available_at": old_at,
			}
		).insert(ignore_permissions=True)

		counts = maintenance.purge_expired_automation_logs(limit=100)

		self.assertEqual(counts["audit_events"], 1)
		self.assertEqual(counts["outbox_events"], 1)
		self.assertFalse(frappe.db.exists("Automation Audit Event", audit_name))
		self.assertFalse(frappe.db.exists("Automation Outbox Event", processed.name))
		self.assertTrue(frappe.db.exists("Automation Outbox Event", pending.name))

	def test_scheduled_log_cleanup_honors_the_configured_interval(self):
		now = datetime(2026, 8, 21, 12, 0, 0)
		with (
			patch.object(maintenance, "now_datetime", return_value=now),
			patch.object(
				frappe.db,
				"get_single_value",
				side_effect=lambda _doctype, field, **_kwargs: {
					"last_log_cleanup_at": datetime(2026, 8, 21, 10, 0, 0),
					"log_cleanup_interval_hours": 4,
				}.get(field),
			),
			patch.object(maintenance, "purge_expired_automation_logs") as purge,
		):
			result = maintenance.run_scheduled_log_cleanup()
		self.assertFalse(result["ran"])
		purge.assert_not_called()

	def test_log_cleanup_settings_reject_unsafe_ranges(self):
		settings = frappe.get_single("Automation Settings")
		settings.history_retention_days = 179
		settings.log_cleanup_interval_hours = 24
		settings.log_cleanup_batch_size = 500
		with self.assertRaisesRegex(frappe.ValidationError, "retention"):
			settings.validate()
		settings.history_retention_days = 180
		settings.log_cleanup_interval_hours = 0
		with self.assertRaisesRegex(frappe.ValidationError, "interval"):
			settings.validate()
		settings.log_cleanup_interval_hours = 24
		settings.log_cleanup_batch_size = 99
		with self.assertRaisesRegex(frappe.ValidationError, "batch size"):
			settings.validate()

	def test_existing_single_settings_receive_cleanup_defaults_during_migrate(self):
		with (
			patch.object(frappe.db, "get_single_value", return_value=0),
			patch.object(frappe.db, "set_single_value") as set_value,
		):
			ensure_automation_settings_defaults()
		self.assertEqual(
			{call.args[1:3] for call in set_value.call_args_list},
			{
				("history_retention_days", 180),
				("log_cleanup_interval_hours", 24),
				("log_cleanup_batch_size", 500),
			},
		)

	def test_publication_state_ignores_layout_but_versions_execution_changes(self):
		created = create_workflow_record("Publication state", "Lead")
		before = get_workflow_draft(created["workflow"])["publication"]
		self.assertEqual((before["state"], before["next_version_no"]), ("NEVER_PUBLISHED", 1))

		first = publish_workflow(created["workflow"], 0)
		published = get_workflow_draft(created["workflow"])["publication"]
		self.assertEqual((published["state"], published["has_unpublished_changes"]), ("PUBLISHED", False))

		retry = publish_workflow(created["workflow"], 0)
		self.assertTrue(retry["unchanged"])
		self.assertEqual(retry["version"], first["version"])
		self.assertEqual(frappe.db.count("Automation Workflow Version", {"workflow": created["workflow"]}), 1)

		graph = get_workflow_draft(created["workflow"])["draft"]["graph"]
		graph["nodes"][0]["position"]["x"] += 20
		layout = save_workflow_draft(created["workflow"], 0, graph)
		self.assertEqual(layout["publication"]["state"], "PUBLISHED")
		self.assertFalse(layout["publication"]["has_unpublished_changes"])
		self.assertNotEqual(layout["graph_hash"], frappe.db.get_value("Automation Workflow Version", first["version"], "graph_hash"))
		self.assertEqual(compare_versions(created["workflow"], first["version"], "DRAFT")["nodes"]["changed"], [])

		layout_retry = publish_workflow(created["workflow"], layout["draft_revision"])
		self.assertTrue(layout_retry["unchanged"])
		self.assertEqual(layout_retry["version"], first["version"])
		self.assertEqual(frappe.db.count("Automation Workflow Version", {"workflow": created["workflow"]}), 1)

		saved = self.add_terminal_step(created["workflow"], layout["draft_revision"])
		self.assertEqual(saved["publication"]["state"], "DRAFT_CHANGES")
		self.assertEqual(saved["publication"]["next_version_no"], 2)

		second = publish_workflow(created["workflow"], saved["draft_revision"])
		self.assertEqual(second["version_no"], 2)
		self.assertFalse(second["unchanged"])
		self.assertEqual(second["publication"]["state"], "PUBLISHED")

	def test_role_provisioning_uses_real_role_names(self):
		ensure_automation_roles()
		for role_name in ("Automation Builder", "Automation Publisher", "Automation Operator"):
			self.assertTrue(frappe.db.exists("Role", role_name))

	def test_subflow_self_reference_and_dependency_cycle_are_rejected(self):
		def call_graph(created, target):
			graph = frappe.parse_json(frappe.as_json(created["graph"]))
			graph["nodes"].extend([
				{"id": "subflow", "type": "action.call_subflow", "type_version": 1, "config": {"subflow_id": target, "wait_for_completion": 1}},
				{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
			])
			graph["edges"] = [
				{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "subflow"},
				{"id": "e2", "source": "subflow", "source_handle": "default", "target": "end"},
			]
			return graph

		first = create_workflow_record("Subflow first", "Lead")
		self_result = save_workflow_draft(first["workflow"], 0, call_graph(first, first["workflow"]))
		self.assertIn("SUBFLOW_SELF_REFERENCE", {issue["code"] for issue in self_result["validation"]})

		second = create_workflow_record("Subflow second", "Lead")
		publish_workflow(second["workflow"], 0)
		first_result = save_workflow_draft(first["workflow"], 1, call_graph(first, second["workflow"]))
		self.assertTrue(first_result["valid"])
		publish_workflow(first["workflow"], 2)
		cycle_result = save_workflow_draft(second["workflow"], 0, call_graph(second, first["workflow"]))
		self.assertIn("SUBFLOW_DEPENDENCY_CYCLE", {issue["code"] for issue in cycle_result["validation"]})
		self.assertFalse(validate_workflow_draft(second["workflow"], publish=True)["valid"])

	def test_run_record_filter_and_pagination_contract(self):
		workflow = MagicMock(name="workflow")
		workflow.name = "WF-1"
		workflow.title = "Workflow"
		workflow.primary_doctype = "Lead"
		workflow.status = "ACTIVE"
		workflow.active_version = None
		rows = [{"name": "RUN-1"}, {"name": "RUN-2"}]
		with (
			patch.object(frappe, "get_doc", return_value=workflow),
			patch.object(frappe, "get_list", side_effect=[rows, [frappe._dict(count=2)]]) as get_list,
		):
			result = engine.list_run_records("WF-1", page_length=1, record_name="LEAD-7")

		workflow.check_permission.assert_called_once_with("read")
		self.assertEqual(result["rows"], [{"name": "RUN-1"}])
		self.assertTrue(result["has_more"])
		self.assertEqual(result["total_count"], 2)
		kwargs = get_list.call_args_list[0].kwargs
		self.assertEqual(kwargs["filters"]["record_name"], ["like", "%LEAD-7%"])
		self.assertEqual(kwargs["limit"], 2)

	def test_revision_conflict_publish_and_immutable_version(self):
		created = create_workflow_record("Foundation test", "Lead")
		draft = get_workflow_draft(created["workflow"])["draft"]
		result = save_workflow_draft(created["workflow"], 0, draft["graph"])
		self.assertEqual(result["draft_revision"], 1)
		with self.assertRaises(AutomationConflictError):
			save_workflow_draft(created["workflow"], 0, draft["graph"])

		published = publish_workflow(created["workflow"], 1)
		self.assertEqual(published["version_no"], 1)
		self.assertEqual(published["status"], "ACTIVE")
		version = frappe.get_doc("Automation Workflow Version", published["version"])
		version.graph_hash = "changed"
		with self.assertRaises(frappe.ValidationError):
			version.save(ignore_permissions=True)

	def test_simulation_rejects_invalid_graph_before_loading_record(self):
		created = create_workflow_record("Invalid simulation", "Lead")
		graph = created["graph"]
		graph["nodes"].append({"id": "wait", "type": "delay.until_event", "type_version": 1, "config": {}})
		graph["edges"] = [{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "wait"}]
		result = simulate(workflow_id=created["workflow"], record_name="DOES-NOT-EXIST", graph=graph)
		self.assertFalse(result["valid"])
		self.assertEqual(result["path"], [])
		self.assertIn("MISSING_EVENT_TOPIC", {issue["code"] for issue in result["issues"]})

	def test_execution_identity_restores_complete_session_and_nested_context(self):
		local_snapshot = {field: getattr(frappe.local, field, None) for field in _SET_USER_LOCAL_FIELDS}
		previous_user = frappe.session.user
		previous_sid = frappe.session.sid
		previous_data = frappe.session.data
		previous_context = {"trace_id": "outer-trace", "recursion_depth": 3}
		frappe.flags.automation_context = previous_context

		with self.assertRaisesRegex(RuntimeError, "action failure"):
			with _execution_identity(
				"Guest", {"trace_id": "inner-trace", "causation_id": "inner-cause", "recursion_depth": 4}
			):
				self.assertEqual(frappe.session.user, "Guest")
				self.assertEqual(frappe.flags.automation_context["trace_id"], "inner-trace")
				raise RuntimeError("action failure")

		self.assertEqual(frappe.session.user, previous_user)
		self.assertEqual(frappe.session.sid, previous_sid)
		self.assertIs(frappe.session.data, previous_data)
		self.assertIs(frappe.flags.automation_context, previous_context)
		for field, value in local_snapshot.items():
			self.assertIs(getattr(frappe.local, field, None), value)
		frappe.flags.pop("automation_context", None)

	def test_execution_identity_rejects_non_worker_production_calls(self):
		previous_job = getattr(frappe.local, "job", None)
		frappe.local.job = None
		try:
			with patch.object(frappe, "in_test", False), self.assertRaisesRegex(
				AutomationError, "isolated background worker"
			):
				_assert_worker_execution()
		finally:
			frappe.local.job = previous_job

	def test_blocked_doctype_is_safe_for_existing_drafts_and_rejected_for_new_workflows(self):
		with self.assertRaises(AutomationPermissionError):
			create_workflow_record("Blocked", "Access Log")

		created = create_workflow_record("Legacy invalid metadata", "Lead")
		frappe.db.set_value("Automation Workflow", created["workflow"], "primary_doctype", "Access Log", update_modified=False)
		result = get_workflow_draft(created["workflow"])
		self.assertFalse(result["metadata"]["available"])
		self.assertIn("PRIMARY_DOCTYPE_UNAVAILABLE", {issue["code"] for issue in result["draft"]["validation"]})
		self.assertEqual(field_catalog_result("Access Log", permission_type="read")["fields"], [])

	def test_primary_doctype_is_immutable(self):
		created = create_workflow_record("Immutable primary", "Lead")
		workflow = frappe.get_doc("Automation Workflow", created["workflow"])
		workflow.primary_doctype = "Contact"
		with self.assertRaises(frappe.ValidationError):
			workflow.save()

	def test_only_unpublished_never_executed_drafts_can_be_deleted(self):
		created = create_workflow_record("Disposable draft", "Lead")
		self.assertTrue(delete_workflow_record(created["workflow"])["deleted"])
		self.assertFalse(frappe.db.exists("Automation Workflow", created["workflow"]))
		self.assertFalse(frappe.db.exists("Automation Workflow Draft", {"workflow": created["workflow"]}))

		published = create_workflow_record("Permanent history", "Lead")
		publish_workflow(published["workflow"], 0)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Disposable History"}).insert()
		run_name = enroll(
			published["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="delete-history"
		)
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "status": "READY"}, "name"))
		with (
			patch.object(frappe, "get_roles", return_value=["Automation Builder"]),
			self.assertRaisesRegex(AutomationPermissionError, "System Manager"),
		):
			delete_workflow_record(published["workflow"], delete_history=True)
		with self.assertRaisesRegex(AutomationError, "Confirm permanent deletion"):
			delete_workflow_record(published["workflow"])
		with self.assertRaisesRegex(AutomationError, "Disable this workflow"):
			delete_workflow_record(published["workflow"], delete_history=True)
		change_workflow_state(published["workflow"], "DISABLED")
		result = delete_workflow_record(published["workflow"], delete_history=True)
		self.assertTrue(result["deleted"])
		self.assertTrue(result["history_deleted"])
		self.assertFalse(frappe.db.exists("Automation Workflow", published["workflow"]))
		self.assertFalse(frappe.db.exists("Automation Workflow Version", {"workflow": published["workflow"]}))
		self.assertFalse(frappe.db.exists("Automation Run", run_name))
		self.assertFalse(frappe.db.exists("Automation Enrollment Ledger", {"workflow": published["workflow"]}))

	def test_manual_enrollment_deduplication_and_terminal_run(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Automation Test"}).insert()
		created = create_workflow_record("Enrollment test", "Lead")
		published = publish_workflow(created["workflow"], 0)
		run_name = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="one")
		self.assertIsNotNone(run_name)
		self.assertIsNone(enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="two"))
		token_name = frappe.db.get_value("Automation Run Token", {"run": run_name}, "name")
		execute_token(token_name)
		frappe.get_doc({
			"doctype": "Automation Run Token",
			"run": run_name,
			"node_id": "branch-metric",
			"occurrence": 1,
			"status": "COMPLETED",
			"output_json": frappe.as_json({"selected_handle": "qualified"}),
		}).insert(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "workflow_version"), published["version"])
		metrics = get_canvas_metrics(created["workflow"], published["version"])
		self.assertEqual(metrics["total_enrollments"], 1)
		trigger_metric = next(row for row in metrics["nodes"] if row["node_id"] == "trigger-1")
		self.assertEqual(trigger_metric["reached"], 1)
		self.assertEqual(trigger_metric["completed"], 1)
		branch_metric = next(row for row in metrics["nodes"] if row["node_id"] == "branch-metric")
		self.assertEqual(branch_metric["branches"], {"qualified": 1})

	def test_enrollment_rejects_initially_ineligible_record(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Not eligible"}).insert()
		created = create_workflow_record("Initial eligibility", "Lead")
		settings = {
			"eligibility_condition": {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Eligible"},
			"unenroll_when_ineligible": False,
		}
		saved = save_workflow_draft(created["workflow"], 0, created["graph"], settings)
		publish_workflow(created["workflow"], saved["draft_revision"])
		self.assertIsNone(enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="ineligible"))
		self.assertFalse(frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name}))
		self.assertEqual(
			frappe.db.get_value("Automation Enrollment Decision", {"workflow": created["workflow"], "record_name": lead.name}, "reason_code"),
			"ELIGIBILITY_CONDITION_FALSE",
		)

	def test_waiting_subflow_resumes_parent(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Subflow completion"}).insert()
		child = create_workflow_record("Runtime child subflow", "Lead")
		publish_workflow(child["workflow"], 0)
		parent = create_workflow_record("Runtime parent subflow", "Lead")
		graph = parent["graph"]
		graph["nodes"].extend([
			{"id": "subflow", "type": "action.call_subflow", "type_version": 1, "config": {"subflow_id": child["workflow"], "wait_for_completion": 1}},
			{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "subflow"},
			{"id": "e2", "source": "subflow", "source_handle": "default", "target": "end"},
		]
		saved = save_workflow_draft(parent["workflow"], 0, graph)
		publish_workflow(parent["workflow"], saved["draft_revision"])
		parent_run = enroll(parent["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="parent")
		execute_token(frappe.db.get_value("Automation Run Token", {"run": parent_run, "node_id": "trigger-1"}, "name"))
		execute_token(frappe.db.get_value("Automation Run Token", {"run": parent_run, "node_id": "subflow"}, "name"))
		child_run = frappe.db.get_value("Automation Run", {"workflow": child["workflow"], "source": "SUBFLOW"}, "name", order_by="creation desc")
		self.assertEqual(frappe.db.get_value("Automation Run", parent_run, "status"), "WAITING")
		execute_token(frappe.db.get_value("Automation Run Token", {"run": child_run}, "name"))
		self.assertEqual(frappe.db.get_value("Automation Run Token", {"run": parent_run, "node_id": "subflow"}, "status"), "COMPLETED")
		end_token = frappe.db.get_value("Automation Run Token", {"run": parent_run, "node_id": "end"}, "name")
		self.assertTrue(end_token)
		execute_token(end_token)
		self.assertEqual(frappe.db.get_value("Automation Run", parent_run, "status"), "COMPLETED")

	def test_falsy_values_and_business_hours_keep_typed_semantics(self):
		run = MagicMock(name="run")
		record = frappe._dict(doctype="Lead", name="LEAD-TEST", score=0, annual_revenue=0.0)
		branch = _execute_node(
			run,
			MagicMock(),
			{"id": "branch", "type": "condition.if_else", "config": {"condition": {"kind": "predicate", "field": "annual_revenue", "operator": "is_not_set"}}},
			record,
			record,
			{},
		)
		self.assertEqual((branch["handle"], branch["output"]["matched"]), ("true", True))
		named_branch = _execute_node(
			run,
			MagicMock(),
			{
				"id": "named-branch",
				"type": "condition.if_else",
				"type_version": 2,
				"config": {
					"branches": [
						{"handle": "positive", "name": "Positive revenue", "condition": {"kind": "predicate", "field": "annual_revenue", "operator": "gt", "value": 0}},
						{"handle": "blank", "name": "Blank revenue", "condition": {"kind": "predicate", "field": "annual_revenue", "operator": "is_not_set"}},
					]
				},
			},
			record,
			record,
			{},
		)
		self.assertEqual(
			(named_branch["handle"], named_branch["output"]["branch_name"], named_branch["output"]["matched"]),
			("blank", "Blank revenue", True),
		)
		random_node = {"id": "experiment", "type": "condition.random_split", "config": {"branches": [{"handle": "a", "name": "A", "percentage": 50}, {"handle": "b", "name": "B", "percentage": 50}]}}
		random_run = MagicMock()
		random_run.name = "RUN-STABLE"
		random_token = MagicMock()
		random_token.occurrence = 0
		first_split = _execute_node(random_run, random_token, random_node, record, record, {})
		second_split = _execute_node(random_run, random_token, random_node, record, record, {})
		self.assertEqual(first_split, second_split)
		self.assertIn(first_split["handle"], {"a", "b"})
		switch = _execute_node(run, MagicMock(), {"id": "switch", "type": "condition.switch", "config": {"field": "score", "cases": [{"value": "0", "handle": "zero"}]}}, record, record, {})
		self.assertEqual((switch["handle"], switch["output"]["value"]), ("zero", "0"))
		with patch.object(frappe.db, "exists", return_value="OTHER") as exists:
			dedup = _execute_node(run, MagicMock(), {"id": "dedup", "type": "condition.deduplicate", "config": {"match_field": "score"}}, record, record, {})
		self.assertEqual(dedup["handle"], "duplicate")
		exists.assert_called_once()
		concat = _execute_node(run, MagicMock(), {"id": "concat", "type": "transform.value", "config": {"operation": "concat", "separator": "|", "values": [{"kind": "literal", "value": 0}, {"kind": "literal", "value": False}]}}, record, record, {})
		self.assertEqual(concat["output"]["value"], "0|False")
		date_field = next(field.fieldname for field in frappe.get_meta("Lead").fields if field.fieldtype in {"Date", "Datetime"})
		record[date_field] = None
		with self.assertRaisesRegex(AutomationError, "has no date value"):
			_execute_node(run, frappe._dict(output_json=None, name="TOKEN"), {"id": "until", "type": "delay.until_date", "config": {"field": date_field}}, record, record, {})
		with patch.object(engine, "get_system_timezone", return_value="UTC"):
			state = _business_hours_state({"timezone": "Asia/Kolkata", "start_time": "09:00", "end_time": "17:00", "weekdays": [0, 1, 2, 3, 4]}, datetime(2026, 8, 14, 18, 0))
		self.assertEqual(state, {"released": False, "due_at": "2026-08-17 03:30:00", "timezone": "Asia/Kolkata"})

	def test_workflow_action_window_settings_are_normalized_and_validated(self):
		settings, issues = validate_settings(
			{"execution_window": {"enabled": 1, "timezone": "Asia/Kolkata", "start_time": "09:00", "end_time": "17:00", "weekdays": [0, 1, 2, 3, 4]}},
			"Lead",
			"Administrator",
		)
		self.assertFalse(issues)
		self.assertTrue(settings["execution_window"]["enabled"])
		_invalid, issues = validate_settings(
			{"execution_window": {"enabled": 1, "timezone": "Not/AZone", "start_time": "18:00", "end_time": "09:00", "weekdays": []}},
			"Lead",
			"Administrator",
		)
		self.assertTrue({"INVALID_EXECUTION_WINDOW", "INVALID_EXECUTION_DAYS", "INVALID_EXECUTION_TIMEZONE"}.issubset({issue["code"] for issue in issues}))
		with patch.object(frappe.db, "exists", return_value="Outgoing"):
			communication, communication_issues = validate_settings(
				{
					"communication": {
						"default_sender_name": "Finbyz Sales",
						"default_sender_email": "sales@example.com",
						"default_sms_sender": "FINBYZ",
						"stop_on_response": 1,
						"mark_responses_read": 1,
					}
				},
				"Lead",
				"Administrator",
			)
		self.assertFalse(communication_issues)
		self.assertEqual(communication["communication"]["default_sender_email"], "sales@example.com")
		self.assertTrue(communication["communication"]["stop_on_response"])

	def test_action_window_holds_only_action_nodes_and_skips_after_timer_release(self):
		run = MagicMock(name="run")
		run.name = "RUN-WINDOW"
		token = MagicMock(name="token")
		token.name = "TOKEN-WINDOW"
		token.output_json = None
		settings = {"execution_window": {"enabled": True, "timezone": "UTC", "start_time": "09:00", "end_time": "17:00", "weekdays": [0, 1, 2, 3, 4]}}
		timer = MagicMock()
		with patch.object(engine, "_business_hours_state", return_value={"released": False, "due_at": "2026-08-21 09:00:00", "timezone": "UTC"}), patch.object(frappe, "get_doc", return_value=timer), patch.object(engine, "_finish_or_continue") as finish:
			self.assertTrue(_hold_for_execution_window(run, token, {"id": "send", "type": "action.send_email"}, {}, settings))
		timer.insert.assert_called_once_with(ignore_permissions=True)
		finish.assert_called_once()
		token.output_json = '{"execution_window": true, "released": true}'
		with patch.object(engine, "_business_hours_state") as state:
			self.assertFalse(_hold_for_execution_window(run, token, {"id": "send", "type": "action.send_email"}, {}, settings))
			state.assert_not_called()
		self.assertFalse(_hold_for_execution_window(run, token, {"id": "branch", "type": "condition.if_else"}, {}, settings))

	def test_round_robin_resolves_email_to_user_identity(self):
		email = frappe.db.get_value("User", "Administrator", "email")
		self.assertEqual(_enabled_user_names([email]), ["Administrator"])

	def test_round_robin_explicit_user_mode_never_guesses_a_user_group(self):
		with (
			patch.object(engine.frappe.db, "exists", return_value=True),
			patch.object(engine, "_enabled_user_names", return_value=["Administrator"]) as enabled_users,
			patch.object(engine.frappe, "get_list") as get_list,
		):
			users, assignment = _round_robin_users({"assignment_type": "users", "users": ["Administrator"], "group": "Administrator"})
		self.assertEqual(users, ["Administrator"])
		self.assertEqual(assignment["assignment_type"], "users")
		enabled_users.assert_called_once_with(["Administrator"])
		get_list.assert_not_called()

	def test_round_robin_validates_frappe_assignment_permissions_not_owner_field(self):
		graph = empty_graph("Lead")
		graph["nodes"].append({
			"id": "round-robin",
			"type": "action.round_robin",
			"type_version": 2,
			"config": {"group": "Administrator"},
		})
		graph["edges"] = [{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "round-robin"}]
		codes = {issue["code"] for issue in validate_bindings(graph, "Administrator")}
		self.assertNotIn("FIELD_PERMISSION", codes)
		self.assertNotIn("TODO_PERMISSION", codes)

	def test_sms_and_webhook_record_bindings_are_permission_checked(self):
		graph = empty_graph("Lead")
		graph["nodes"].extend([
			{"id": "sms", "type": "action.send_sms", "type_version": 1, "config": {"recipient": {"kind": "record_field", "field": "missing_private_field"}, "message": {"kind": "literal", "value": "Hello"}, "purpose": "workflow"}},
			{"id": "webhook", "type": "action.webhook", "type_version": 1, "config": {"integration_secret": "missing", "url": "https://example.com", "payload": {"secret": {"kind": "record_field", "field": "missing_private_field"}}}},
		])
		graph["edges"] = [
			{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "sms"},
			{"id": "e2", "source": "sms", "source_handle": "default", "target": "webhook"},
		]
		permission_paths = {issue["path"] for issue in validate_bindings(graph, "Administrator") if issue["code"] == "FIELD_PERMISSION"}
		self.assertIn("nodes.sms.config.recipient", permission_paths)
		self.assertIn("nodes.webhook.config.payload", permission_paths)

	def test_associated_record_checks_linked_document_permission(self):
		record = frappe._dict(doctype="Lead", name="LEAD-TEST", customer="CUST-TEST")
		linked_record = MagicMock()
		linked_record.get.return_value = "Acme"
		with patch.object(frappe, "get_doc", return_value=linked_record):
			result = _execute_node(
				MagicMock(), MagicMock(),
				{"id": "associated", "type": "transform.associated_record", "config": {"reference_field": "customer", "fetch_field": "customer_name"}},
				record, record, {},
			)
		linked_record.check_permission.assert_called_once_with("read")
		self.assertEqual(result["output"]["value"], "Acme")

	def test_reenrollment_policy_keeps_occurrence_idempotency(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Re-enrollment Test"}).insert()
		created = create_workflow_record("Re-enrollment policy", "Lead")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		first = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="event-one")
		second = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="event-two")
		duplicate = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="event-one")
		self.assertIsNotNone(first)
		self.assertIsNotNone(second)
		self.assertIsNone(duplicate)
		self.assertEqual(
			frappe.db.count("Automation Enrollment Ledger", {"workflow": created["workflow"], "record_name": lead.name}),
			2,
		)

	def test_pause_resume_disable_state_contract(self):
		created = create_workflow_record("State test", "Lead")
		publish_workflow(created["workflow"], 0)
		self.assertEqual(change_workflow_state(created["workflow"], "PAUSED")["status"], "PAUSED")
		self.assertEqual(change_workflow_state(created["workflow"], "ACTIVE")["status"], "ACTIVE")
		self.assertEqual(change_workflow_state(created["workflow"], "DISABLED")["status"], "DISABLED")

	def test_invalid_published_version_is_quarantined_and_cannot_reactivate(self):
		created = create_workflow_record("Invalid active version", "Lead")
		published = publish_workflow(created["workflow"], 0)
		graph = frappe.parse_json(
			frappe.db.get_value("Automation Workflow Version", published["version"], "graph_json")
		)
		graph["nodes"][0]["type"] = "trigger.document_insert"
		graph["nodes"][0]["config"] = {
			"condition": {"kind": "predicate", "field": "first_name", "operator": "not_contains", "value": None}
		}
		frappe.db.set_value(
			"Automation Workflow Version", published["version"], "graph_json", frappe.as_json(graph), update_modified=False
		)

		validation = validate_published_version(created["workflow"], published["version"])
		self.assertFalse(validation["valid"])
		self.assertIn("MISSING_CONDITION_VALUE", {issue["code"] for issue in validation["issues"]})

		quarantine_invalid_active_versions()
		self.assertEqual(frappe.db.get_value("Automation Workflow", created["workflow"], "status"), "PAUSED")
		self.assertEqual(
			frappe.db.count("Automation Trigger Subscription", {"workflow": created["workflow"], "active": 1}), 0
		)
		with self.assertRaisesRegex(AutomationError, "no longer safe to activate"):
			change_workflow_state(created["workflow"], "ACTIVE")

	def test_missing_published_version_fails_closed_without_crashing(self):
		created = create_workflow_record("Missing version", "Lead")
		validation = validate_published_version(created["workflow"], "missing-version")
		self.assertFalse(validation["valid"])
		self.assertEqual(validation["issues"][0]["code"], "VERSION_NOT_FOUND")

	def test_disable_cancels_active_durable_timers(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Disable Timer Test"}).insert()
		created = create_workflow_record("Disable timer state", "Lead")
		graph = created["graph"]
		graph["nodes"].extend(
			[
				{"id": "delay", "type": "delay.fixed", "type_version": 1, "config": {"seconds": 3600}},
				{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
			]
		)
		graph["edges"] = [
			{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "delay"},
			{"id": "edge-2", "source": "delay", "source_handle": "default", "target": "end"},
		]
		saved = save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], saved["draft_revision"])
		run_name = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="disable-timer")
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "trigger-1"}, "name"))
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "name"))
		self.assertEqual(frappe.db.get_value("Automation Timer", {"run": run_name}, "status"), "ACTIVE")

		change_workflow_state(created["workflow"], "DISABLED")

		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "CANCELLED")
		self.assertEqual(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "status"), "CANCELLED")
		self.assertEqual(frappe.db.get_value("Automation Timer", {"run": run_name}, "status"), "CANCELLED")

	def test_safe_action_chain_and_duplicate_delivery(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Action Chain"}).insert()
		created = create_workflow_record("Safe actions", "Lead")
		graph = created["graph"]
		graph["nodes"].extend(
			[
				{
					"id": "update",
					"type": "action.update_record",
					"type_version": 1,
					"config": {"assignments": [{"field": "company_name", "value": {"kind": "literal", "value": "Updated by automation"}}]},
				},
				{
					"id": "create",
					"type": "action.create_record",
					"type_version": 1,
					"config": {"target_doctype": "Contact", "assignments": [{"field": "first_name", "value": {"kind": "literal", "value": "Created by automation"}}]},
				},
				{"id": "todo", "type": "action.create_todo", "type_version": 1, "config": {"allocated_to": "Administrator", "description": "Review automated lead", "priority": "High"}},
				{"id": "comment", "type": "action.add_comment", "type_version": 1, "config": {"content": "Automation completed its safe action chain."}},
				{"id": "notify", "type": "action.notify_user", "type_version": 1, "config": {"for_user": "Administrator", "subject": "Automation test", "message": "Safe action chain completed."}},
				{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
			]
		)
		sequence = ["trigger-1", "update", "create", "todo", "comment", "notify", "end"]
		graph["edges"] = [
			{"id": f"edge-{index}", "source": source, "source_handle": "default", "target": target}
			for index, (source, target) in enumerate(zip(sequence, sequence[1:]), 1)
		]
		saved = save_workflow_draft(created["workflow"], 0, graph)
		self.assertTrue(saved["valid"])
		publish_workflow(created["workflow"], 1)
		run_name = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="safe-actions")

		while token_name := frappe.db.get_value("Automation Run Token", {"run": run_name, "status": "READY"}, "name"):
			execute_token(token_name)
		notification_filters = {
			"for_user": "Administrator",
			"type": "Alert",
			"subject": "Automation test",
			"document_type": "Lead",
			"document_name": lead.name,
		}
		self.assertEqual(frappe.db.count("Notification Log", notification_filters), 1)
		self.assertEqual(
			frappe.db.get_value("Notification Log", notification_filters, "email_content"),
			"Safe action chain completed.",
		)

		# Re-delivering completed tokens is a no-op and cannot duplicate effects.
		first_action = frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "update"}, "name")
		execute_token(first_action)
		notify_action = frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "notify"}, "name")
		execute_token(notify_action)

		self.assertEqual(frappe.db.get_value("Lead", lead.name, "company_name"), "Updated by automation")
		self.assertTrue(frappe.db.exists("Contact", {"first_name": "Created by automation"}))
		self.assertTrue(frappe.db.exists("ToDo", {"reference_type": "Lead", "reference_name": lead.name}))
		self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Lead", "reference_name": lead.name, "comment_type": "Comment"}))
		self.assertEqual(frappe.db.count("Notification Log", notification_filters), 1)
		self.assertEqual(frappe.db.count("Automation Effect Ledger", {"run": run_name, "status": "COMPLETED"}), 5)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")

	def test_stale_external_effect_requires_operator_reconciliation_without_resending(self):
		lead = frappe.get_doc(
			{"doctype": "Lead", "first_name": "External Recovery", "email_id": "recovery@example.com"}
		).insert()
		created = create_workflow_record("External recovery", "Lead")
		graph = created["graph"]
		graph["nodes"].append(
			{
				"id": "email",
				"type": "action.send_email",
				"type_version": 2,
				"config": {
					"content_mode": "inline",
					"recipient": {"kind": "record_field", "field": "email_id"},
					"subject": {"kind": "literal", "value": "Recovery check"},
					"message": {
						"kind": "literal",
						"value": "This job must not be resent automatically.",
					},
				},
			}
		)
		graph["edges"] = [
			{"id": "edge-email", "source": "trigger-1", "source_handle": "default", "target": "email"}
		]
		save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], 1)
		with patch.object(frappe, "enqueue") as enqueue:
			run_name = enroll(
				created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="external-recovery"
			)
			while token_name := frappe.db.get_value(
				"Automation Run Token", {"run": run_name, "status": "READY"}, "name"
			):
				execute_token(token_name)
			enqueue.reset_mock()
			ledger_name = frappe.db.get_value(
				"Automation Effect Ledger", {"run": run_name, "status": "STARTED"}, "name"
			)
			frappe.db.set_value(
				"Automation Effect Ledger",
				ledger_name,
				"modified",
				frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-60),
				update_modified=False,
			)
			self.assertGreaterEqual(recover_stale_external_effects(), 1)
			enqueue.assert_not_called()

		self.assertEqual(
			frappe.db.get_value("Automation Effect Ledger", ledger_name, "status"), "UNKNOWN_COMMIT"
		)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "WAITING")
		self.assertEqual(
			frappe.db.get_value(
				"Automation Action Attempt", {"run": run_name, "node_id": "email"}, "status"
			),
			"UNKNOWN_COMMIT",
		)
		self.assertTrue(
			frappe.db.exists(
				"Automation Dead Letter",
				{"source_type": "EXTERNAL", "source_name": ledger_name, "status": "OPEN"},
			)
		)
		self.assertEqual(recover_stale_external_effects(), 0)

	def test_fixed_delay_uses_durable_timer(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Timer Test"}).insert()
		created = create_workflow_record("Timer workflow", "Lead")
		graph = created["graph"]
		graph["nodes"].extend(
			[
				{"id": "delay", "type": "delay.fixed", "type_version": 1, "config": {"seconds": 60}},
				{"id": "end", "type": "end.complete", "type_version": 1, "config": {}},
			]
		)
		graph["edges"] = [
			{"id": "edge-1", "source": "trigger-1", "source_handle": "default", "target": "delay"},
			{"id": "edge-2", "source": "delay", "source_handle": "default", "target": "end"},
		]
		save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], 1)
		run_name = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key="timer")
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "trigger-1"}, "name"))
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "name"))
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "WAITING")
		timer_name = frappe.db.get_value("Automation Timer", {"run": run_name, "status": "ACTIVE"}, "name")
		self.assertIsNotNone(timer_name)
		frappe.db.set_value("Automation Timer", timer_name, "due_at", "2000-01-01 00:00:00")
		self.assertEqual(release_due_timers(), 1)
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "name"))
		execute_token(frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "end"}, "name"))
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")

	def test_event_wait_releases_to_event_branch_and_timeout_branch(self):
		def create_run(key: str):
			lead = frappe.get_doc({"doctype": "Lead", "first_name": f"Event Wait {key}"}).insert()
			created = create_workflow_record(f"Event wait {key}", "Lead")
			graph = created["graph"]
			graph["nodes"].extend([
				{"id": "wait", "type": "delay.until_event", "type_version": 1, "config": {"event_topic": "crm.lead.qualified", "event_filter": {"kind": "predicate", "field": "score", "operator": "gte", "value": 10}, "timeout_seconds": 3600}},
				{"id": "event-end", "type": "end.complete", "type_version": 1, "config": {}},
				{"id": "timeout-end", "type": "end.complete", "type_version": 1, "config": {}},
			])
			graph["edges"] = [
				{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "wait"},
				{"id": "e2", "source": "wait", "source_handle": "event", "target": "event-end"},
				{"id": "e3", "source": "wait", "source_handle": "timeout", "target": "timeout-end"},
			]
			self.assertTrue(save_workflow_draft(created["workflow"], 0, graph)["valid"])
			publish_workflow(created["workflow"], 1)
			run = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key=key)
			execute_token(frappe.db.get_value("Automation Run Token", {"run": run, "node_id": "trigger-1"}, "name"))
			execute_token(frappe.db.get_value("Automation Run Token", {"run": run, "node_id": "wait"}, "name"))
			return run

		event_run = create_run("event")
		timer_state = frappe.db.get_value("Automation Timer", {"run": event_run}, ["name", "timer_type", "status", "token"], as_dict=True)
		self.assertEqual((timer_state.timer_type, timer_state.status, frappe.db.get_value("Automation Run Token", timer_state.token, "status")), ("TIMEOUT", "ACTIVE", "WAITING"))
		event_lead = frappe.db.get_value("Automation Run", event_run, "record_name")
		with patch.object(engine, "_queue_token"):
			self.assertEqual(release_event_waiters("crm.lead.qualified", {"score": 9}, record_doctype="Lead", record_name=event_lead), 0)
			self.assertEqual(release_event_waiters("crm.lead.qualified", {"score": 10}, record_doctype="Lead", record_name="OTHER"), 0)
			self.assertEqual(release_event_waiters("crm.lead.qualified", {"score": 10}, record_doctype="Lead", record_name=event_lead), 1)
		wait_token = frappe.get_doc("Automation Run Token", {"run": event_run, "node_id": "wait"})
		self.assertEqual(frappe.parse_json(wait_token.output_json)["event_payload"], {"score": 10})
		execute_token(wait_token.name)
		self.assertTrue(frappe.db.exists("Automation Run Token", {"run": event_run, "node_id": "event-end"}))

		timeout_run = create_run("timeout")
		frappe.db.set_value("Automation Timer", {"run": timeout_run}, "due_at", "2000-01-01 00:00:00")
		with patch.object(engine, "_queue_token"):
			self.assertEqual(release_due_timers(), 1)
		wait_token = frappe.get_doc("Automation Run Token", {"run": timeout_run, "node_id": "wait"})
		self.assertTrue(frappe.parse_json(wait_token.output_json)["timed_out"])
		execute_token(wait_token.name)
		self.assertTrue(frappe.db.exists("Automation Run Token", {"run": timeout_run, "node_id": "timeout-end"}))

	def test_event_wait_can_be_indefinite_and_a_late_event_cannot_beat_timeout(self):
		def create_run(key: str, *, indefinite: bool):
			lead = frappe.get_doc({"doctype": "Lead", "first_name": f"Modern event wait {key}"}).insert()
			created = create_workflow_record(f"Modern event wait {key}", "Lead")
			graph = created["graph"]
			config = {
				"data_source": "enrolled_record",
				"event_topic": "record.updated",
				"event_filter": None,
				"timeout_mode": "indefinite" if indefinite else "duration",
				"branch_on_timeout": 0 if indefinite else 1,
			}
			if not indefinite:
				config["timeout_seconds"] = 60
			graph["nodes"].extend([
				{"id": "wait", "type": "delay.until_event", "type_version": 2, "config": config},
				{"id": "event-end", "type": "end.complete", "type_version": 1, "config": {}},
			])
			graph["edges"] = [
				{"id": "e1", "source": "trigger-1", "source_handle": "default", "target": "wait"},
				{"id": "e2", "source": "wait", "source_handle": "default" if indefinite else "event", "target": "event-end"},
			]
			if not indefinite:
				graph["nodes"].append({"id": "timeout-end", "type": "end.complete", "type_version": 1, "config": {}})
				graph["edges"].append({"id": "e3", "source": "wait", "source_handle": "timeout", "target": "timeout-end"})
			self.assertTrue(save_workflow_draft(created["workflow"], 0, graph)["valid"])
			publish_workflow(created["workflow"], 1)
			run = enroll(created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key=key)
			execute_token(frappe.db.get_value("Automation Run Token", {"run": run, "node_id": "trigger-1"}, "name"))
			execute_token(frappe.db.get_value("Automation Run Token", {"run": run, "node_id": "wait"}, "name"))
			return lead, run

		lead, indefinite_run = create_run("indefinite", indefinite=True)
		timer = frappe.db.get_value("Automation Timer", {"run": indefinite_run}, ["timer_type", "due_at"], as_dict=True)
		self.assertEqual(timer.timer_type, "EVENT_WAIT")
		self.assertIsNone(timer.due_at)
		self.assertEqual(release_due_timers(), 0)
		with patch.object(engine, "_queue_token"):
			self.assertEqual(release_event_waiters("record.updated", {"changed_fields": ["status"]}, record_doctype="Lead", record_name=lead.name), 1)

		late_lead, late_run = create_run("late", indefinite=False)
		frappe.db.set_value("Automation Timer", {"run": late_run}, "due_at", "2000-01-01 00:00:00")
		with patch.object(engine, "_queue_token"):
			self.assertEqual(release_event_waiters("record.updated", {"changed_fields": ["status"]}, record_doctype="Lead", record_name=late_lead.name), 1)
		late_state = frappe.parse_json(frappe.db.get_value("Automation Run Token", {"run": late_run, "node_id": "wait"}, "output_json"))
		self.assertTrue(late_state["timed_out"])
		self.assertEqual(late_state["matched_handle"], "timeout")

		queued_lead, queued_run = create_run("queued-before-deadline", indefinite=False)
		queued_timer = frappe.db.get_value("Automation Timer", {"run": queued_run}, "name")
		created_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-3)
		occurred_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-2)
		due_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-1)
		frappe.db.set_value("Automation Timer", queued_timer, {"creation": created_at, "due_at": due_at}, update_modified=False)
		with patch.object(engine, "_queue_token"):
			self.assertEqual(release_event_waiters(
				"record.updated",
				{"changed_fields": ["status"], "occurred_at": str(occurred_at)},
				record_doctype="Lead",
				record_name=queued_lead.name,
			), 1)
		queued_state = frappe.parse_json(frappe.db.get_value("Automation Run Token", {"run": queued_run, "node_id": "wait"}, "output_json"))
		self.assertFalse(queued_state["timed_out"])
		self.assertEqual(queued_state["matched_handle"], "event")
