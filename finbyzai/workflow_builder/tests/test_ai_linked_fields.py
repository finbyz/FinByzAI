import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.ai_support import (
	_context_manifest,
	_provider_prompt,
	build_ai_context,
	resolve_ai_field_path,
	validate_ai_node_binding,
)
from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.principal import execution_principal


class TestAILinkedFields(IntegrationTestCase):
	def setUp(self):
		self.customer = frappe.db.get_value("Customer", {}, ["name", "customer_group", "territory"], as_dict=True)
		if not self.customer:
			self.skipTest("No Customer on this site to link to.")
		self.run_doc = frappe._dict(record_doctype="Sales Order", record_name="SO-LINKED-TEST")

	def _context(self, config, record):
		with (
			patch("finbyzai.workflow_builder.ai_support.int_setting", side_effect=lambda _field, default: default),
			execution_principal("Administrator", {"trace_id": "ai-linked-test"}),
		):
			return build_ai_context(self.run_doc, config, record=record)[0]

	def test_resolves_plain_and_linked_paths(self):
		self.assertEqual(resolve_ai_field_path("Sales Order", "customer", user="Administrator"), ("customer", None, None))
		self.assertEqual(
			resolve_ai_field_path("Sales Order", "customer.customer_group", user="Administrator"),
			("customer", "Customer", "customer_group"),
		)

	def test_rejects_invalid_linked_paths(self):
		for path in ("customer.customer_group.name", "customer.", "transaction_date.customer_group", "customer.api_key"):
			with self.subTest(path=path), self.assertRaises(AutomationError):
				resolve_ai_field_path("Sales Order", path, user="Administrator")

	def test_explicit_allowlist_loads_linked_document_fields(self):
		context = self._context(
			{"field_allowlist": ["customer.customer_group", "customer.territory"]},
			{"customer": self.customer.name},
		)
		self.assertEqual(context["record"]["fields"], {"customer": self.customer.name})
		self.assertEqual(
			context["record"]["linked"],
			{
				"customer": {
					"doctype": "Customer",
					"fields": {"customer_group": self.customer.customer_group, "territory": self.customer.territory},
				}
			},
		)
		manifest = json.loads(_context_manifest(context))
		self.assertEqual(manifest["fieldnames"], ["customer", "customer.customer_group", "customer.territory"])

	def test_inline_prompt_references_are_loaded_and_rendered(self):
		config = {
			"prompt_mode": "inline",
			"model": "any",
			"system_prompt": "Group: {{ doc.customer.customer_group }}",
			"user_prompt": "Customer {{ doc.customer }} on {{ doc.transaction_date }}. {{ doc.no_such_field }} {{ doc.owner.full_name }}",
		}
		context = self._context(config, {"customer": self.customer.name, "transaction_date": "2026-11-27", "owner": "Administrator"})
		fields = context["record"]["fields"]
		self.assertEqual(fields["customer"], self.customer.name)
		self.assertEqual(fields["transaction_date"], "2026-11-27")
		self.assertNotIn("no_such_field", fields)
		# User is not readable by automations, so only the owner ID is sent, never User data.
		self.assertEqual(fields["owner"], "Administrator")
		self.assertNotIn("owner", context["record"]["linked"])
		self.assertEqual(context["record"]["linked"]["customer"]["fields"], {"customer_group": self.customer.customer_group})

		system, human = _provider_prompt({"prompt_mode": "inline", **config}, config, context, [], {})
		self.assertIn(f"Group: {self.customer.customer_group}", system)
		self.assertIn(f"Customer {self.customer.name} on 2026-11-27.", human)

	def test_unreadable_linked_record_sends_no_values(self):
		real_has_permission = frappe.has_permission

		def deny_linked_customer(doctype, ptype="read", doc=None, *args, **kwargs):
			if doctype == "Customer" and doc:
				return False
			return real_has_permission(doctype, ptype, doc, *args, **kwargs)

		with patch("finbyzai.workflow_builder.ai_support.frappe.has_permission", side_effect=deny_linked_customer):
			context = self._context({"field_allowlist": ["customer.customer_group"]}, {"customer": self.customer.name})
		self.assertEqual(context["record"]["linked"]["customer"]["fields"], {"customer_group": None})

	def test_empty_link_sends_no_values(self):
		context = self._context({"field_allowlist": ["customer.customer_group"]}, {"customer": None})
		self.assertEqual(context["record"]["linked"]["customer"]["fields"], {"customer_group": None})

	def test_context_without_links_is_unchanged(self):
		context = self._context({"field_allowlist": ["customer"]}, {"customer": self.customer.name})
		self.assertNotIn("linked", context["record"])

	def test_publish_validation_checks_linked_paths(self):
		base = {"prompt_mode": "inline", "model": "any", "user_prompt": "x", "timeout_seconds": 60}
		with (
			patch("finbyzai.workflow_builder.ai_support.build_profile_snapshot", return_value={"model": "any"}),
			patch("finbyzai.workflow_builder.ai_support.validate_generation"),
		):
			validate_ai_node_binding(
				{**base, "field_allowlist": ["customer.customer_group"]},
				node_type="action.ai_generate",
				primary_doctype="Sales Order",
				execution_user="Administrator",
			)
		with self.assertRaises(AutomationError):
			validate_ai_node_binding(
				{**base, "field_allowlist": ["transaction_date.customer_group"]},
				node_type="action.ai_generate",
				primary_doctype="Sales Order",
				execution_user="Administrator",
			)


class TestAIStepOutputs(IntegrationTestCase):
	NODE = "3f2a1c9e-0b7d-4e6a-9c1f-2d8b7a6e5c4d"

	def _context(self, config, outputs):
		run = frappe._dict(record_doctype="Sales Order", record_name="SO-STEP-TEST")
		with (
			patch("finbyzai.workflow_builder.ai_support.int_setting", side_effect=lambda _field, default: default),
			execution_principal("Administrator", {"trace_id": "ai-step-test"}),
		):
			return build_ai_context(run, config, record={}, outputs=outputs)[0]

	def test_prompt_reads_only_referenced_step_outputs(self):
		config = {
			"prompt_mode": "inline",
			"model": "any",
			"user_prompt": f'Group: {{{{ steps["{self.NODE}"].value }}}}. Count: {{{{ steps.lookup.count }}}}',
		}
		outputs = {
			self.NODE: {"value": "Commercial", "linked_name": "CUST-1"},
			"lookup": {"values": ["a"], "count": 3},
			"unreferenced": {"value": "secret-ish"},
		}
		context = self._context(config, outputs)
		self.assertEqual(context["steps"], {self.NODE: {"value": "Commercial"}, "lookup": {"count": 3}})
		self.assertEqual(json.loads(_context_manifest(context))["step_outputs"], sorted([self.NODE, "lookup"]))

		_system, human = _provider_prompt({"prompt_mode": "inline", **config}, config, context, [], {})
		self.assertIn("Group: Commercial. Count: 3", human)

	def test_missing_step_output_is_left_out(self):
		config = {"prompt_mode": "inline", "model": "any", "user_prompt": f'{{{{ steps["{self.NODE}"].value }}}}'}
		self.assertNotIn("steps", self._context(config, {}))
		self.assertNotIn("steps", self._context(config, None))

	def test_profile_mode_does_not_send_step_outputs(self):
		config = {"ai_profile": "Agent", "field_allowlist": [], "user_prompt": f'{{{{ steps["{self.NODE}"].value }}}}'}
		self.assertNotIn("steps", self._context(config, {self.NODE: {"value": "x"}}))
