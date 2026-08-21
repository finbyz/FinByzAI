import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder import webhooks
from finbyzai.workflow_builder.authoring import create_workflow_record, publish_workflow
from finbyzai.workflow_builder.webhooks import _authenticate, _payload_value, _validate_identity


class TestAutomationInboundWebhooks(IntegrationTestCase):
	def test_nested_payload_paths_are_exact(self):
		payload = {"record": {"identity": "LEAD-1"}, "event_id": "event-1"}
		self.assertEqual(_payload_value(payload, "record.identity"), "LEAD-1")
		self.assertIsNone(_payload_value(payload, "record.missing"))

	def test_hmac_authentication_uses_the_raw_request_body(self):
		raw = b'{"event_id":"event-1"}'
		secret = "test-secret"
		signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
		definition = SimpleNamespace(auth_type="HMAC SHA256", get_password=lambda *args, **kwargs: secret)
		with patch.object(frappe, "get_request_header", return_value=signature):
			_authenticate(definition, raw)
		with (
			patch.object(frappe, "get_request_header", return_value="sha256=incorrect"),
			self.assertRaisesRegex(AutomationError, "authentication failed"),
		):
			_authenticate(definition, raw)

	def test_bearer_authentication_is_constant_contract(self):
		definition = SimpleNamespace(auth_type="Bearer", get_password=lambda *args, **kwargs: "bearer-secret")
		with patch.object(frappe, "get_request_header", return_value="Bearer bearer-secret"):
			_authenticate(definition, b"{}")

	def test_name_identity_resolves_one_existing_record(self):
		definition = SimpleNamespace(record_doctype="Lead", record_identity_field="name", payload_record_path="record_id")
		with patch.object(frappe.db, "exists", return_value=True) as exists:
			self.assertEqual(_validate_identity(definition, {"record_id": "LEAD-1"}), "LEAD-1")
		exists.assert_called_once_with("Lead", "LEAD-1", cache=False)

	def test_authenticated_receipt_creates_one_durable_idempotent_outbox_event(self):
		frappe.set_user("Administrator")
		created = create_workflow_record("Inbound webhook workflow", "Lead", trigger_type="trigger.webhook")
		publish_workflow(created["workflow"], 0, reenrollment="ALWAYS")
		definition = webhooks.create_definition(created["workflow"], "Inbound lead")
		webhooks.set_enabled(definition["name"], True)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Webhook lead"}).insert()
		payload = {"record_id": lead.name, "event_id": "provider-event-1"}
		raw = json.dumps(payload, separators=(",", ":")).encode()
		signature = "sha256=" + hmac.new(definition["secret"].encode(), raw, hashlib.sha256).hexdigest()
		endpoint_key = frappe.db.get_value("Automation Inbound Webhook", definition["name"], "endpoint_key")
		request = SimpleNamespace(get_data=lambda cache=True: raw)
		with (
			patch.object(webhooks, "automation_enabled", return_value=True),
			patch.object(webhooks, "_register_dispatch_wake"),
			patch.object(frappe, "request", request, create=True),
			patch.object(frappe, "get_request_header", return_value=signature),
		):
			first = webhooks.receive(endpoint_key)
			second = webhooks.receive(endpoint_key)
		self.assertTrue(first["accepted"])
		self.assertFalse(first["deduplicated"])
		self.assertTrue(second["deduplicated"])
		self.assertEqual(frappe.db.count("Automation Outbox Event", {"event_type": "WEBHOOK", "object_name": lead.name}), 1)
