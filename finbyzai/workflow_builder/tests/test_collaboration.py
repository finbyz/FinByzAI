import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder import collaboration
from finbyzai.workflow_builder.authoring import create_workflow_record, save_workflow_draft


class TestAutomationCollaboration(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

	def test_step_comment_lifecycle_is_outside_the_runtime_graph(self):
		created = create_workflow_record("Commented workflow", "Lead")
		before = frappe.db.get_value("Automation Workflow Draft", {"workflow": created["workflow"]}, "graph_hash")
		comment = collaboration.create_comment(created["workflow"], "Please review this trigger", step_id="trigger-1")
		rows = collaboration.list_comments(created["workflow"])["rows"]
		self.assertEqual(rows[0].name, comment["name"])
		self.assertFalse(rows[0].resolved)
		collaboration.set_comment_resolved(comment["name"], True)
		self.assertTrue(collaboration.list_comments(created["workflow"])["rows"][0].resolved)
		self.assertEqual(frappe.db.get_value("Automation Workflow Draft", {"workflow": created["workflow"]}, "graph_hash"), before)

	def test_connections_are_derived_from_the_current_draft(self):
		created = create_workflow_record("Connected workflow", "Lead")
		graph = created["graph"]
		graph["nodes"].append({
			"id": "email",
			"type": "action.send_email",
			"type_version": 2,
			"config": {"content_mode": "template", "email_template": "Welcome", "sender_email": "sales@example.com", "subscription_topic": "Product Updates", "recipient": {"kind": "literal", "value": "test@example.com"}},
		})
		graph["nodes"].append({
			"id": "asana",
			"type": "action.asana",
			"type_version": 1,
			"config": {"operation": "create_task", "payload": {"name": {"kind": "literal", "value": "Follow up"}}},
		})
		graph["edges"] = [
			{"id": "edge", "source": "trigger-1", "source_handle": "default", "target": "email"},
			{"id": "edge-asana", "source": "email", "source_handle": "default", "target": "asana"},
		]
		save_workflow_draft(created["workflow"], 0, graph)
		rows = collaboration.workflow_connections(created["workflow"])["rows"]
		self.assertIn(("ERP DocType", "Lead"), {(row["kind"], row["name"]) for row in rows})
		self.assertIn(("Email Template", "Welcome"), {(row["kind"], row["name"]) for row in rows})
		self.assertIn(("Email Account", "sales@example.com"), {(row["kind"], row["name"]) for row in rows})
		self.assertIn(("Subscription Topic", "Product Updates"), {(row["kind"], row["name"]) for row in rows})
		self.assertIn(("Installed integration", "Asana Integration"), {(row["kind"], row["name"]) for row in rows})

	def test_workflow_can_be_shared_per_user_as_read_or_edit(self):
		email = f"zztest-share-{frappe.generate_hash(length=6)}@example.com"
		frappe.get_doc({
			"doctype": "User", "email": email, "first_name": "ZZTest", "send_welcome_email": 0,
			"roles": [{"role": "Automation Builder"}],
		}).insert(ignore_permissions=True)
		created = create_workflow_record("Shared workflow", "Lead")
		workflow = frappe.get_doc("Automation Workflow", created["workflow"])

		access = collaboration.set_workflow_access(workflow.name, email, "read")
		self.assertEqual([(r.user, r.read, r.write) for r in access["rows"]], [(email, 1, 0)])
		access = collaboration.set_workflow_access(workflow.name, email, "edit")
		self.assertEqual([(r.user, r.read, r.write) for r in access["rows"]], [(email, 1, 1)])
		self.assertTrue(frappe.has_permission("Automation Workflow", "write", doc=workflow, user=email))

		self.assertEqual(collaboration.set_workflow_access(workflow.name, email, "none")["rows"], [])
		with self.assertRaises(frappe.ValidationError):
			collaboration.set_workflow_access(workflow.name, email, "owner")
		with self.assertRaises(frappe.ValidationError):
			collaboration.set_workflow_access(workflow.name, workflow.owner, "read")
