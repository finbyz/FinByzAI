from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.api import send_workflow_test_email
from finbyzai.workflow_builder.emailing import get_email_template, resolve_email_content
from finbyzai.workflow_builder.errors import AutomationError


class TestWorkflowEmailAuthoring(IntegrationTestCase):
	def _template(self, *, reference_doctype="Lead", enabled=1):
		name = f"Workflow email {frappe.generate_hash(length=8)}"
		values = {
			"doctype": "Email Template",
			"name": name,
			"enabled": enabled,
			"subject": "Hello {{ lead_name }}",
			"use_html": 1,
			"response_html": "<html><body>Welcome {{ lead_name }}</body></html>",
		}
		meta = frappe.get_meta("Email Template")
		if meta.has_field("custom_builder_mode"):
			values["custom_builder_mode"] = "Raw HTML"
		if meta.has_field("custom_reference_doctype"):
			values["custom_reference_doctype"] = reference_doctype
		elif meta.has_field("reference_doctype"):
			values["reference_doctype"] = reference_doctype
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def test_saved_html_template_is_personalized_for_enrolled_record(self):
		template = self._template()
		content = resolve_email_content(
			{
				"content_mode": "template",
				"email_template": template.name,
				"subject_override": {"kind": "literal", "value": ""},
			},
			record=frappe._dict(doctype="Lead", lead_name="Ada"),
			outputs={},
			primary_doctype="Lead",
		)

		self.assertEqual(content["subject"], "Hello Ada")
		self.assertIn("Welcome Ada", content["message"])
		self.assertTrue(content["raw_html"])
		self.assertEqual(content["email_template"], template.name)
		self.assertTrue(content["content_hash"])

	def test_template_reference_doctype_must_match_workflow(self):
		template = self._template(reference_doctype="Opportunity")
		with self.assertRaisesRegex(AutomationError, "designed for Opportunity, not Lead"):
			get_email_template(template.name, "Lead", check_permission=False)

	def test_workflow_test_email_is_explicit_single_recipient_and_never_adds_unsubscribe(self):
		workflow = SimpleNamespace(name="AUTO-WORKFLOW-1", primary_doctype="Lead")
		queue = SimpleNamespace(name="EMAIL-QUEUE-TEST")
		content = {
			"subject": "Welcome Ada",
			"message": "<html><body>Welcome</body></html>",
			"raw_html": True,
		}
		with (
			patch("finbyzai.workflow_builder.api.registry.require_builder"),
			patch("finbyzai.workflow_builder.api._email_workflow", return_value=workflow),
			patch("finbyzai.workflow_builder.api._email_preview_record", return_value=frappe._dict()),
			patch("finbyzai.workflow_builder.api._check_workflow_test_email_rate_limit"),
			patch("finbyzai.workflow_builder.api.emailing.resolve_email_content", return_value=content),
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe, "sendmail", return_value=queue) as sendmail,
		):
			result = send_workflow_test_email(
				workflow.name,
				config={"content_mode": "template", "email_template": "Lead welcome"},
				recipient="designer@example.com",
			)

		self.assertEqual(result["email_queue"], "EMAIL-QUEUE-TEST")
		self.assertEqual(result["status"], "queued")
		self.assertEqual(sendmail.call_args.kwargs["recipients"], ["designer@example.com"])
		self.assertEqual(sendmail.call_args.kwargs["subject"], "[TEST] Welcome Ada")
		self.assertEqual(sendmail.call_args.kwargs["add_unsubscribe_link"], 0)
		self.assertTrue(sendmail.call_args.kwargs["raw_html"])

	def test_workflow_test_email_rejects_multiple_recipients(self):
		with (
			patch("finbyzai.workflow_builder.api.registry.require_builder"),
			patch(
				"finbyzai.workflow_builder.api._email_workflow",
				return_value=SimpleNamespace(name="AUTO-WORKFLOW-1", primary_doctype="Lead"),
			),
			self.assertRaisesRegex(AutomationError, "one email address"),
		):
			send_workflow_test_email(
				"AUTO-WORKFLOW-1",
				config={},
				recipient="first@example.com,second@example.com",
			)
