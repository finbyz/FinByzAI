import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.approvals import create_approval, expire_due_approvals, resolve_approval
from finbyzai.workflow_builder.engine import _finish_or_continue, simulate_graph
from finbyzai.workflow_builder.principal import execution_principal
from finbyzai.workflow_builder.schema import validate_graph


def approval_graph():
	return {
		"schema_version": 1,
		"primary_doctype": "Lead",
		"start_node_id": "start",
		"nodes": [
			{"id": "start", "type": "trigger.manual", "type_version": 1, "position": {"x": 0, "y": 0}, "config": {}},
			{"id": "review", "type": "action.human_approval", "type_version": 1, "position": {"x": 0, "y": 100}, "config": {"reviewer": "Administrator", "title": "Review", "draft_text": {"kind": "literal", "value": "Draft"}, "evidence": {"kind": "literal", "value": {}}, "ai_attempt": {"kind": "literal", "value": ""}, "allow_edit": 1, "expires_days": 7}},
			{"id": "approved", "type": "action.add_comment", "type_version": 1, "position": {"x": -100, "y": 200}, "config": {"content": "Approved"}},
			{"id": "rejected", "type": "action.add_comment", "type_version": 1, "position": {"x": 100, "y": 200}, "config": {"content": "Rejected"}},
		],
		"edges": [
			{"id": "e1", "source": "start", "source_handle": "default", "target": "review"},
			{"id": "e2", "source": "review", "source_handle": "approved", "target": "approved"},
			{"id": "e3", "source": "review", "source_handle": "rejected", "target": "rejected"},
		],
	}


class FakeDoc(frappe._dict):
	def insert(self, **_kwargs):
		if not self.get("name"):
			self.name = "APPROVAL-1" if self.doctype == "Automation Human Approval" else "TODO-1"
		return self

	def db_set(self, fieldname, value, **_kwargs):
		self[fieldname] = value


class TestHumanApproval(IntegrationTestCase):
	def test_publish_requires_both_explicit_approval_paths(self):
		valid = validate_graph(approval_graph(), primary_doctype="Lead", publish=True)
		self.assertTrue(valid["valid"], valid["issues"])
		missing = approval_graph()
		missing["edges"] = missing["edges"][:-1]
		result = validate_graph(missing, primary_doctype="Lead", publish=True)
		self.assertIn("APPROVAL_PATHS_INCOMPLETE", {row["code"] for row in result["issues"]})

	def test_simulation_stops_at_human_approval(self):
		result = simulate_graph(approval_graph(), frappe._dict(doctype="Lead", name="LEAD-1"))
		self.assertFalse(result["completed"])
		self.assertTrue(result["requires_human_approval"])
		self.assertEqual(result["path"][-1]["status"], "REQUIRES_HUMAN_APPROVAL")

	def test_create_approval_is_durable_and_assigns_reviewer(self):
		run = SimpleNamespace(name="RUN-1", workflow="WF-1", record_doctype="Lead", record_name="LEAD-1")
		token = SimpleNamespace(name="TOKEN-1", occurrence=1)
		node = approval_graph()["nodes"][1]
		created = []

		def get_doc(value, *args, **kwargs):
			if isinstance(value, dict):
				doc = FakeDoc(value)
				created.append(doc)
				return doc
			raise AssertionError((value, args, kwargs))

		def get_value(doctype, name, fields=None, **_kwargs):
			if doctype == "User":
				return frappe._dict(enabled=1, user_type="System User")
			if doctype == "Automation Human Approval":
				return None
			return None

		with (
			patch("finbyzai.workflow_builder.approvals.now_datetime", return_value=datetime(2026, 8, 25, 12, 0, 0)),
			patch.object(frappe, "get_doc", side_effect=get_doc),
			patch.object(frappe.db, "get_value", side_effect=get_value),
			patch.object(frappe, "publish_realtime"),
		):
			with execution_principal("Administrator", {"trace_id": "test"}):
				result = create_approval(run, token, node, record={"name": "LEAD-1"}, outputs={})
		self.assertEqual(result["status"], "WAIT_APPROVAL")
		self.assertEqual(result["output"]["approval_id"], "APPROVAL-1")
		todo = next(row for row in created if row.doctype == "ToDo")
		self.assertEqual(todo.allocated_to, "Administrator")
		self.assertEqual(todo.reference_name, "LEAD-1")

	def test_expiry_scheduler_uses_narrow_system_path_without_impersonation(self):
		with (
			patch.object(frappe.db, "table_exists", return_value=True),
			patch.object(frappe, "get_all", return_value=["APPROVAL-1"]),
			patch.object(frappe, "set_user", side_effect=AssertionError("set_user must not be called")),
			patch("finbyzai.workflow_builder.approvals._resolve_approval", return_value={}) as resolve,
		):
			self.assertEqual(expire_due_approvals(), 1)
		resolve.assert_called_once_with(
			"APPROVAL-1",
			"REJECT",
			comment="Approval expired without a decision.",
			reviewer_user=None,
			automatic_expiry=True,
		)

	def test_wait_approval_uses_dedicated_event_not_external_effect_event(self):
		run = Mock(name="run")
		run.name = "RUN-1"
		token = Mock(name="token")
		token.node_id = "review"
		with patch("finbyzai.workflow_builder.engine._append_event") as append:
			_finish_or_continue(run, token, approval_graph(), {"status": "WAIT_APPROVAL", "output": {"approval_id": "APPROVAL-1"}})
		self.assertEqual(token.status, "WAITING")
		self.assertEqual(run.status, "WAITING")
		append.assert_called_once_with("RUN-1", "APPROVAL_REQUESTED", node_id="review", payload={"approval_id": "APPROVAL-1"})

	def test_resolution_resumes_exact_selected_branch(self):
		user = frappe.session.user
		approval = SimpleNamespace(
			name="APPROVAL-1", run="RUN-1", token="TOKEN-1", workflow="WF-1", node_id="review",
			reviewer=user, status="PENDING", allow_edit=1, draft_text="Draft", final_text=None,
			decision_comment=None, reviewed_by=None, reviewed_at=None, expires_at=None, todo="TODO-1",
			save=Mock(),
		)
		run = SimpleNamespace(name="RUN-1", status="WAITING", workflow_version="WV-1")
		token = SimpleNamespace(name="TOKEN-1", status="WAITING", node_id="review")
		version = SimpleNamespace(graph_json=json.dumps(approval_graph()))

		def get_doc(doctype, name=None, **_kwargs):
			return {"Automation Run": run, "Automation Run Token": token, "Automation Human Approval": approval, "Automation Workflow Version": version}[doctype]

		def get_value(doctype, name, fields=None, **_kwargs):
			if doctype == "Automation Human Approval" and isinstance(fields, list):
				return frappe._dict(name="APPROVAL-1", run="RUN-1", token="TOKEN-1")
			if doctype == "Automation Human Approval":
				return "APPROVAL-1"
			if doctype == "Automation Action Attempt":
				return "ACTION-1"
			return None

		with (
			patch("finbyzai.workflow_builder.approvals.now_datetime", return_value=datetime(2026, 8, 25, 12, 0, 0)),
			patch.object(frappe, "get_doc", side_effect=get_doc),
			patch.object(frappe.db, "get_value", side_effect=get_value),
			patch.object(frappe.db, "set_value"),
			patch.object(frappe, "publish_realtime"),
			patch("finbyzai.workflow_builder.engine._append_event"),
			patch("finbyzai.workflow_builder.engine._finish_or_continue") as finish,
		):
			output = resolve_approval("APPROVAL-1", "EDIT_AND_APPROVE", final_text="Edited")
		self.assertEqual(output["status"], "EDITED")
		self.assertEqual(output["final_text"], "Edited")
		self.assertEqual(finish.call_args.args[3]["handle"], "approved")
