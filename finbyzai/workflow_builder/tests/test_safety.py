from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder import api, configuration, engine, observability
from finbyzai.workflow_builder.api import create_workflow_from_template
from finbyzai.workflow_builder.authoring import (
	clone_workflow_record,
	compare_versions,
	create_workflow_record,
	publish_workflow,
	save_suppression_rule,
	save_workflow_draft,
)
from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.schema import empty_graph
from finbyzai.workflow_builder.template import parse_template_package


class TestAutomationProductionSafety(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.runtime_gate = patch.object(engine, "workflow_runtime_allowed", return_value=True)
		self.runtime_gate.start()
		self.addCleanup(self.runtime_gate.stop)

	def _lead(self, title: str) -> str:
		return frappe.get_doc({"doctype": "Lead", "first_name": title}).insert().name

	def _workflow(self, title: str = "Safety workflow", settings: dict | None = None):
		created = create_workflow_record(title, "Lead")
		if settings is not None:
			save_workflow_draft(created["workflow"], 0, created["graph"], settings)
			revision = 1
		else:
			revision = 0
		published = publish_workflow(created["workflow"], revision, reenrollment="ALWAYS")
		return created, published

	def test_template_package_is_strict_and_fully_validated(self):
		package = {
			"package_version": 1,
			"type": "Automation Workflow Template",
			"metadata": {"title": "Safe template", "category": "Sales", "description": "", "primary_doctype": "Lead"},
			"graph": empty_graph("Lead"),
			"settings": {},
		}
		self.assertEqual(parse_template_package(package)["primary_doctype"], "Lead")
		with self.assertRaises(AutomationError):
			parse_template_package({**package, "signature": "not-supported"})
		with self.assertRaises(AutomationError):
			parse_template_package({**package, "graph": empty_graph("Contact")})

	def test_runtime_preflight_fails_for_invalid_active_version_and_unhealthy_runtime(self):
		created, published = self._workflow("Preflight safety")
		frappe.db.set_value(
			"Automation Workflow Version", published["version"], "graph_hash", "tampered", update_modified=False
		)
		health = {"healthy": False, "reasons": ["OPEN_INCIDENTS"], "runs": {}, "outbox": {}}
		transports = {
			name: {"configured": True, "provider_count": 1, "live_verified": False, "message": "Configured"}
			for name in ("email", "sms", "webhook")
		}
		with (
			patch.object(api, "automation_enabled", return_value=True),
			patch.object(api, "external_actions_enabled", return_value=True),
			patch.object(api.events, "runtime_health", return_value=health),
			patch.object(api.external, "transport_readiness", return_value=transports),
			patch("frappe.utils.background_jobs.get_workers", return_value=[object()]),
		):
			result = api.runtime_preflight(created["workflow"])

		self.assertFalse(result["ready"])
		codes = {issue["code"] for issue in result["issues"]}
		self.assertIn("ACTIVE_VERSION_INVALID", codes)
		self.assertIn("RUNTIME_UNHEALTHY", codes)

	def test_first_publish_preflight_validates_candidate_without_requiring_active_version(self):
		created = create_workflow_record("First publish preflight", "Lead")
		health = {"healthy": True, "reasons": [], "runs": {}, "outbox": {}}
		transports = {
			name: {"configured": True, "provider_count": 1, "live_verified": False, "message": "Configured"}
			for name in ("email", "sms", "webhook")
		}
		with (
			patch.object(api, "automation_enabled", return_value=True),
			patch.object(api, "external_actions_enabled", return_value=True),
			patch.object(api.events, "runtime_health", return_value=health),
			patch.object(api.external, "transport_readiness", return_value=transports),
			patch("frappe.utils.background_jobs.get_workers", return_value=[object()]),
		):
			result = api.runtime_preflight(created["workflow"])
		self.assertNotIn("NO_ACTIVE_VERSION", {issue["code"] for issue in result["issues"]})
		self.assertTrue(result["ready"])

	def test_manual_endpoint_rejects_non_manual_published_trigger(self):
		lead = self._lead("Not manually triggered")
		created = create_workflow_record(
			"Document-triggered workflow", "Lead", trigger_type="trigger.document_insert"
		)
		publish_workflow(created["workflow"], 0)
		with self.assertRaisesRegex(AutomationError, "manual trigger"):
			api.enroll_manual(
				workflow_id=created["workflow"],
				record_name=lead,
				idempotency_key=frappe.generate_hash(length=20),
			)

	def test_runtime_gate_only_uses_the_global_switch(self):
		with patch.object(configuration, "automation_enabled", return_value=True):
			self.assertTrue(configuration.workflow_runtime_allowed("ANY-WORKFLOW"))
		with patch.object(configuration, "automation_enabled", return_value=False):
			self.assertFalse(configuration.workflow_runtime_allowed("ANY-WORKFLOW"))

	def test_template_instantiation_uses_validated_draft_and_idempotency(self):
		template = frappe.get_doc({
			"doctype": "Automation Workflow Template",
			"title": f"Template {frappe.generate_hash(length=8)}",
			"category": "Sales",
			"primary_doctype": "Lead",
			"description": "Validated template",
			"graph_json": frappe.as_json(empty_graph("Lead")),
			"settings_json": "{}",
		}).insert()
		envelope = {"idempotency_key": frappe.generate_hash(length=20), "payload": {"id": template.name}}
		first = create_workflow_from_template(envelope)
		second = create_workflow_from_template(envelope)
		self.assertEqual(first["workflow"], second["workflow"])
		self.assertEqual(first["draft_revision"], 1)
		self.assertTrue(second["deduplicated"])
		draft = frappe.get_doc("Automation Workflow Draft", {"workflow": first["workflow"]})
		self.assertTrue(draft.graph_hash)
		self.assertEqual(draft.validation_json, "[]")

	def test_csv_export_neutralizes_spreadsheet_formulas(self):
		row = SimpleNamespace(
			name="DEC-1", workflow="WF-1", workflow_version="WFV-1", record_doctype="Lead",
			record_name="=HYPERLINK(\"bad\")", source="MANUAL", decision="REJECTED",
			reason_code="+FORMULA", run="-RUN", decided_at="2026-08-10 00:00:00",
		)
		with patch.object(frappe, "get_list", return_value=[row]):
			observability.export_enrollment_decisions()
		csv_data = frappe.response["result"]
		self.assertIn("'=HYPERLINK", csv_data)
		self.assertIn("'+FORMULA", csv_data)
		self.assertIn("'-RUN", csv_data)

	def test_run_trace_pagination_does_not_silently_truncate(self):
		run = MagicMock()
		run.name = "RUN-1"
		rows = [{"name": "EVENT-1"}, {"name": "EVENT-2"}]
		with patch.object(frappe, "get_doc", return_value=run), patch.object(frappe.db, "table_exists", return_value=True), patch.object(frappe, "get_list", return_value=rows) as get_list:
			page = engine.get_run_trace("RUN-1", "events", start=5, page_length=1)
		run.check_permission.assert_called_once_with("read")
		self.assertEqual(page, {"rows": [{"name": "EVENT-1"}], "has_more": True})
		self.assertEqual(get_list.call_args.kwargs["start"], 5)
		self.assertEqual(get_list.call_args.kwargs["limit"], 2)

	def test_absent_goal_does_not_block_enrollment_and_writes_safe_evidence(self):
		lead = self._lead("No implicit goal")
		created, published = self._workflow()
		run = engine.enroll(created["workflow"], "Lead", lead, source="MANUAL", occurrence_key="safety-1")
		self.assertTrue(run)
		decision_name = frappe.db.get_value(
			"Automation Enrollment Decision", {"run": run}, "name", order_by="creation desc"
		)
		decision = frappe.get_doc("Automation Enrollment Decision", decision_name)
		self.assertEqual(decision.decision, "ENROLLED")
		self.assertEqual(decision.workflow_version, published["version"])
		self.assertNotIn("first_name", decision.evidence_json or "")

	def test_goal_and_suppression_prevent_runs_with_distinct_reasons(self):
		lead = self._lead("Blocked record")
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Blocked record"}
		created, _published = self._workflow("Goal workflow", {"goal_condition": goal})
		self.assertIsNone(engine.enroll(created["workflow"], "Lead", lead, source="MANUAL", occurrence_key="goal-1"))
		self.assertEqual(
			frappe.db.get_value("Automation Enrollment Decision", {"workflow": created["workflow"]}, "decision"),
			"GOAL_ALREADY_MET",
		)

		created2, _published2 = self._workflow("Suppression workflow")
		save_suppression_rule(created2["workflow"], {"title": "Block this lead", "condition": goal})
		self.assertIsNone(engine.enroll(created2["workflow"], "Lead", lead, source="MANUAL", occurrence_key="suppress-1"))
		decision_name = frappe.db.get_value(
			"Automation Enrollment Decision", {"workflow": created2["workflow"]}, "name", order_by="creation desc"
		)
		decision = frappe.get_doc("Automation Enrollment Decision", decision_name)
		self.assertEqual(decision.decision, "SUPPRESSED")
		self.assertEqual(decision.reason_code, "SUPPRESSION_RULE")

	def test_snapshot_mode_pins_only_referenced_fields(self):
		lead = self._lead("Snapshot original")
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Never"}
		created, _published = self._workflow(
			"Snapshot workflow", {"read_mode": "ENROLLMENT_SNAPSHOT", "goal_condition": goal}
		)
		run_name = engine.enroll(created["workflow"], "Lead", lead, source="MANUAL", occurrence_key="snapshot-1")
		run = frappe.get_doc("Automation Run", run_name)
		snapshot = frappe.parse_json(run.enrollment_snapshot_json)
		self.assertEqual(run.read_mode, "ENROLLMENT_SNAPSHOT")
		self.assertEqual(snapshot["first_name"], "Snapshot original")
		self.assertEqual(set(snapshot), {"doctype", "name", "first_name"})
		frappe.db.set_value("Lead", lead, "first_name", "Snapshot changed")
		self.assertEqual(engine._read_record(run, frappe.get_doc("Lead", lead)).first_name, "Snapshot original")

	def test_incidents_group_and_resolve_their_dead_letters(self):
		first = observability.record_incident(
			source_type="OUTBOX", source_name="outbox-a", error_code="TIMEOUT", message="first", attempts=3
		)
		second = observability.record_incident(
			source_type="OUTBOX", source_name="outbox-b", error_code="TIMEOUT", message="second", attempts=3
		)
		self.assertEqual(first["incident"], second["incident"])
		incident = frappe.get_doc("Automation Incident", first["incident"])
		self.assertEqual(incident.occurrence_count, 2)
		self.assertEqual(frappe.db.count("Automation Dead Letter", {"incident": incident.name}), 2)
		observability.resolve_incident(incident.name, "Provider recovered")
		incident.reload()
		self.assertEqual(incident.status, "RESOLVED")
		self.assertEqual(frappe.db.count("Automation Dead Letter", {"incident": incident.name, "status": "RESOLVED"}), 2)

	def test_clone_remaps_identity_and_version_diff_reports_changes(self):
		created, published = self._workflow("Clone source")
		clone = clone_workflow_record(created["workflow"], "Clone target", published["version"])
		source_graph = frappe.parse_json(frappe.db.get_value("Automation Workflow Version", published["version"], "graph_json"))
		clone_graph = frappe.parse_json(frappe.db.get_value("Automation Workflow Draft", {"workflow": clone["workflow"]}, "graph_json"))
		self.assertNotEqual(source_graph["start_node_id"], clone_graph["start_node_id"])
		diff = compare_versions(created["workflow"], published["version"], "DRAFT")
		self.assertFalse(diff["settings_changed"])
		self.assertEqual(diff["nodes"]["changed"], [])
