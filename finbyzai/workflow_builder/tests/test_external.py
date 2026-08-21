from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder.errors import AutomationError, AutomationTransientError
from finbyzai.workflow_builder.external import (
	AutomationUnknownCommitError,
	MAX_WEBHOOK_RESPONSE_BYTES,
	_consume_rate_limit,
	_native_email_recipient_exclusion,
	_normalise_sms_recipient,
	_post_pinned,
	_safe_webhook_url,
	_send_sms_via_frappe_gateway,
	execute_asana,
	execute_external,
	queue_email,
	send_instagram_message,
	send_frappe_sms,
	transport_readiness,
)
from finbyzai.workflow_builder.engine import _claim_effect
from finbyzai.workflow_builder.schema import canonical_json


class TestAutomationExternalSafety(IntegrationTestCase):
	def test_native_email_suppression_honours_global_frappe_unsubscribe(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		with (
			patch.object(frappe.db, "exists", return_value=True) as exists,
			patch.object(frappe, "get_all") as get_all,
		):
			reason = _native_email_recipient_exclusion(run, "PERSON@EXAMPLE.COM")

		self.assertEqual(reason, "Globally unsubscribed")
		exists.assert_called_once_with(
			"Email Unsubscribe", {"email": "person@example.com", "global_unsubscribe": 1}
		)
		get_all.assert_not_called()

	def test_native_email_suppression_honours_standard_lead_opt_out(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")

		def get_all(doctype, **kwargs):
			if doctype == "Lead":
				return [SimpleNamespace(disabled=0, unsubscribed=1, status="Open")]
			return []

		with (
			patch.object(frappe.db, "exists", return_value=False),
			patch.object(frappe, "get_all", side_effect=get_all),
		):
			reason = _native_email_recipient_exclusion(run, "person@example.com")

		self.assertEqual(reason, "Lead unsubscribed")

	def test_email_action_uses_rendered_template_and_returns_traceable_outputs(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		config = {
			"content_mode": "template",
			"email_template": "Lead welcome",
			"recipient": {"kind": "literal", "value": "person@example.com"},
			"sender_name": "Megasol",
			"sender_email": "hello@example.com",
			"reply_to": "reply@example.com",
		}
		content = {
			"subject": "Hello Ada",
			"message": "<html><body>Welcome Ada</body></html>",
			"raw_html": True,
			"email_template": "Lead welcome",
			"content_hash": "content-123",
		}
		queue = SimpleNamespace(name="EMAIL-QUEUE-1")
		with (
			patch("finbyzai.workflow_builder.external.external_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.external._require_consent") as require_consent,
			patch("finbyzai.workflow_builder.external._native_email_recipient_exclusion", return_value=None),
			patch("finbyzai.workflow_builder.external._reach_recipient_exclusion", return_value=None),
			patch("finbyzai.workflow_builder.external.resolve_email_content", return_value=content),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "sendmail", return_value=queue) as sendmail,
		):
			result = queue_email(run, config, record={"lead_name": "Ada"}, outputs={})

		require_consent.assert_not_called()
		sendmail.assert_called_once_with(
			recipients=["person@example.com"],
			sender="Megasol <hello@example.com>",
			reply_to="reply@example.com",
			subject="Hello Ada",
			content="<html><body>Welcome Ada</body></html>",
			delayed=True,
			reference_doctype="Lead",
			reference_name="LEAD-1",
			add_unsubscribe_link=1,
			unsubscribe_message="Unsubscribe",
			raw_html=True,
			add_css=False,
		)
		self.assertEqual(
			result,
			{
				"email_queue": "EMAIL-QUEUE-1",
				"recipient": "person@example.com",
				"sender": "Megasol <hello@example.com>",
				"reply_to": "reply@example.com",
				"email_template": "Lead welcome",
				"content_hash": "content-123",
				"subscription_topic": None,
				"suppressed": False,
				"suppression_reason": None,
			},
		)

	def test_email_action_completes_as_suppressed_for_reach_topic_opt_out(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		config = {
			"content_mode": "inline",
			"recipient": {"kind": "literal", "value": "person@example.com"},
			"subscription_topic": "Product Updates",
		}
		content = {"subject": "Hello", "message": "Body", "raw_html": False, "email_template": None, "content_hash": "hash"}
		with (
			patch("finbyzai.workflow_builder.external.external_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.external.resolve_email_content", return_value=content),
			patch.object(frappe, "get_installed_apps", return_value=["finbyzreach"]),
			patch.object(frappe.db, "exists", return_value=True),
			patch("finbyzai.workflow_builder.external._native_email_recipient_exclusion", return_value=None),
			patch("finbyzai.workflow_builder.external._reach_recipient_exclusion", return_value="Unsubscribed from topic Product Updates"),
			patch.object(frappe, "sendmail") as sendmail,
		):
			result = queue_email(run, config, record={}, outputs={})

		self.assertTrue(result["suppressed"])
		self.assertEqual(result["suppression_reason"], "Unsubscribed from topic Product Updates")
		self.assertEqual(result["subscription_topic"], "Product Updates")
		self.assertIsNone(result["email_queue"])
		sendmail.assert_not_called()

	def test_email_action_enforces_native_global_opt_out_before_reach_topic_check(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		config = {
			"content_mode": "inline",
			"recipient": {"kind": "literal", "value": "person@example.com"},
			"subscription_topic": "Product Updates",
		}
		content = {"subject": "Hello", "message": "Body", "raw_html": False, "email_template": None, "content_hash": "hash"}
		with (
			patch("finbyzai.workflow_builder.external.external_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.external.resolve_email_content", return_value=content),
			patch.object(frappe, "get_installed_apps", return_value=["finbyzreach"]),
			patch.object(frappe.db, "exists", return_value=True),
			patch("finbyzai.workflow_builder.external._native_email_recipient_exclusion", return_value="Globally unsubscribed"),
			patch("finbyzai.workflow_builder.external._reach_recipient_exclusion") as reach_check,
			patch.object(frappe, "sendmail") as sendmail,
		):
			result = queue_email(run, config, record={}, outputs={})

		self.assertTrue(result["suppressed"])
		self.assertEqual(result["suppression_reason"], "Globally unsubscribed")
		reach_check.assert_not_called()
		sendmail.assert_not_called()

	def test_email_action_rechecks_outgoing_sender_at_execution_time(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		config = {
			"content_mode": "inline",
			"recipient": {"kind": "literal", "value": "person@example.com"},
			"sender_email": "disabled@example.com",
		}
		content = {
			"subject": "Hello",
			"message": "Body",
			"raw_html": False,
			"email_template": None,
			"content_hash": None,
		}
		with (
			patch("finbyzai.workflow_builder.external.external_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.external.resolve_email_content", return_value=content),
			patch.object(frappe.db, "exists", return_value=False),
			patch.object(frappe, "sendmail") as sendmail,
			self.assertRaisesRegex(AutomationError, "enabled outgoing Email Account"),
		):
			queue_email(run, config, record={}, outputs={})
		sendmail.assert_not_called()

	def test_webhook_rate_limit_uses_one_atomic_redis_operation(self):
		with patch.object(frappe.cache, "eval", return_value=3) as evaluate:
			_consume_rate_limit("Provider A", 3)

		args = evaluate.call_args.args
		self.assertIn("redis.call('INCR', KEYS[1])", args[0])
		self.assertIn("redis.call('EXPIRE', KEYS[1], ARGV[1])", args[0])
		self.assertEqual(args[1:], (1, frappe.cache.make_key("automation:webhook-rate:Provider A"), 60))

		with patch.object(frappe.cache, "eval", return_value=4), self.assertRaisesRegex(
			AutomationTransientError, "rate limit reached"
		):
			_consume_rate_limit("Provider A", 3)

	def test_transport_readiness_is_configuration_aware_and_never_claims_live_delivery(self):
		def get_all(doctype, **kwargs):
			if doctype == "Email Account":
				return [SimpleNamespace(name="Default Outgoing", default_outgoing=1)]
			if doctype == "Automation Integration Secret":
				return [SimpleNamespace(name="UAT Webhook")]
			return []

		sms = SimpleNamespace(sms_gateway_url="https://sms.example.test", message_parameter="message", receiver_parameter="to")
		with (
			patch.object(frappe, "get_all", side_effect=get_all),
			patch.object(frappe, "get_hooks", return_value=[]),
			patch.object(frappe, "get_single", return_value=sms),
		):
			readiness = transport_readiness()

		self.assertTrue(all(readiness[name]["configured"] for name in ("email", "sms", "webhook")))
		self.assertFalse(any(readiness[name]["live_verified"] for name in ("email", "sms", "webhook")))

	def test_consent_doctype_supports_every_external_channel(self):
		options = set((frappe.get_meta("Automation Consent Record").get_field("channel").options or "").splitlines())
		self.assertEqual(options, {"EMAIL", "SMS", "WEBHOOK", "INSTAGRAM"})

	def test_instagram_action_builds_meta_payload_and_uses_instagram_consent(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		config = {
			"integration_secret": "Meta",
			"url": "https://graph.facebook.com/v23.0/me/messages",
			"recipient_id": {"kind": "record_field", "field": "instagram_id"},
			"message": {"kind": "literal", "value": "Hello from the workflow"},
			"purpose": "support",
			"require_consent": 1,
		}
		with patch("finbyzai.workflow_builder.external.send_webhook", return_value={"status_code": 200}) as webhook:
			result = send_instagram_message(
				run,
				config,
				record={"instagram_id": "17841400000000000"},
				outputs={},
				effect_key="instagram-effect",
			)

		forwarded = webhook.call_args.args[1]
		self.assertEqual(forwarded["_consent_channel"], "INSTAGRAM")
		self.assertEqual(forwarded["consent_recipient"], "17841400000000000")
		self.assertEqual(
			forwarded["payload"],
			{"recipient": {"id": "17841400000000000"}, "message": {"text": "Hello from the workflow"}},
		)
		self.assertEqual(result["recipient_id"], "17841400000000000")

	def test_asana_action_uses_installed_integration_and_returns_stable_output(self):
		settings = SimpleNamespace(enabled=1, workspace_gid="workspace-1")
		payload = {"name": "Follow up", "notes": "Created from FinbyzAI"}
		response = {"gid": "task-1", "name": "Follow up", "permalink_url": "https://app.asana.com/0/task-1"}
		with (
			patch.object(frappe, "get_installed_apps", return_value=["asana_integration"]),
			patch.object(frappe, "get_cached_doc", return_value=settings),
			patch("asana_integration.client.create_task", return_value=response) as create_task,
		):
			result = execute_asana(
				{"operation": "create_task", "payload": payload},
				record={},
				outputs={},
			)

		create_task.assert_called_once_with({**payload, "workspace": "workspace-1"})
		self.assertEqual(result, {**response, "operation": "create_task"})

	def test_webhook_response_is_bounded_and_connection_is_released(self):
		response = Mock(status=200)
		response.read.return_value = b"x" * (MAX_WEBHOOK_RESPONSE_BYTES + 1)
		pool = Mock()
		pool.request.return_value = response
		with patch("finbyzai.workflow_builder.external.urllib3.HTTPSConnectionPool", return_value=pool), self.assertRaisesRegex(AutomationError, "1 MiB"):
			_post_pinned("https://api.example.com/events", "api.example.com", ("93.184.216.34",), b"{}", {})
		response.read.assert_called_once_with(MAX_WEBHOOK_RESPONSE_BYTES + 1, cache_content=False)
		response.release_conn.assert_called_once()
		pool.close.assert_called_once()

	def test_webhook_requires_exact_allowlisted_https_host(self):
		with self.assertRaises(AutomationError):
			_safe_webhook_url("http://api.example.com/events", {"api.example.com"})
		with self.assertRaises(AutomationError):
			_safe_webhook_url("https://127.0.0.1/events", {"127.0.0.1"})
		with self.assertRaises(AutomationError):
			_safe_webhook_url("https://other.example.com/events", {"api.example.com"})

	def test_webhook_rejects_private_dns_and_accepts_public_dns(self):
		with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]), self.assertRaises(AutomationError):
			_safe_webhook_url("https://api.example.com/events", {"api.example.com"})
		with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
			url, host, addresses = _safe_webhook_url("https://api.example.com/events", {"api.example.com"})
		self.assertEqual(url, "https://api.example.com/events")
		self.assertEqual(host, "api.example.com")
		self.assertEqual(addresses, ("93.184.216.34",))

	def test_sms_recipient_is_normalised_and_validated(self):
		self.assertEqual(_normalise_sms_recipient(" +41 (79) 123-45-67 "), "+41791234567")
		for invalid in ("", "not-a-number", "+12"):
			with self.assertRaises(AutomationError):
				_normalise_sms_recipient(invalid)

	def test_sms_requires_complete_gateway_settings(self):
		settings = SimpleNamespace(sms_gateway_url="", message_parameter="message", receiver_parameter="to")
		with patch("frappe.get_doc", return_value=settings), self.assertRaises(AutomationError):
			_send_sms_via_frappe_gateway("+41791234567", "Hello")

	def test_sms_gateway_has_timeout_and_reports_success(self):
		settings = SimpleNamespace(
			sms_gateway_url="https://sms.example.test/send",
			message_parameter="message",
			receiver_parameter="to",
			use_post=1,
			get=Mock(return_value=[]),
		)
		response = SimpleNamespace(status_code=202)
		with (
			patch("frappe.get_doc", return_value=settings),
			patch("frappe.core.doctype.sms_settings.sms_settings.get_headers", return_value={"Accept": "*/*"}),
			patch("requests.request", return_value=response) as request,
			patch("frappe.core.doctype.sms_settings.sms_settings.create_sms_log") as create_log,
		):
			self.assertEqual(_send_sms_via_frappe_gateway("+41791234567", "Hello"), 202)
		request.assert_called_once_with(
			"POST",
			"https://sms.example.test/send",
			headers={"Accept": "*/*"},
			timeout=(5, 20),
			data={"message": "Hello", "to": "+41791234567"},
		)
		create_log.assert_called_once()

	def test_sms_transport_errors_have_safe_retry_semantics(self):
		settings = SimpleNamespace(
			sms_gateway_url="https://sms.example.test/send",
			message_parameter="message",
			receiver_parameter="to",
			use_post=0,
			get=Mock(return_value=[]),
		)
		common = (
			patch("frappe.get_doc", return_value=settings),
			patch("frappe.core.doctype.sms_settings.sms_settings.get_headers", return_value={}),
		)
		with common[0], common[1], patch("requests.request", side_effect=requests.ConnectTimeout()):
			with self.assertRaises(AutomationTransientError):
				_send_sms_via_frappe_gateway("+41791234567", "Hello")
		with (
			patch("frappe.get_doc", return_value=settings),
			patch("frappe.core.doctype.sms_settings.sms_settings.get_headers", return_value={}),
			patch("requests.request", side_effect=requests.ReadTimeout()),
			self.assertRaises(AutomationUnknownCommitError),
		):
			_send_sms_via_frappe_gateway("+41791234567", "Hello")

	def test_sms_defaults_to_consent_and_returns_truthful_status(self):
		run = SimpleNamespace(record_doctype="Lead", record_name="LEAD-1")
		config = {
			"recipient": {"kind": "literal", "value": "+41 79 123 45 67"},
			"message": {"kind": "literal", "value": "Hello"},
		}
		with (
			patch("finbyzai.workflow_builder.external.external_actions_enabled", return_value=True),
			patch("finbyzai.workflow_builder.external._require_consent") as require_consent,
			patch("frappe.get_hooks", return_value=[]),
			patch("finbyzai.workflow_builder.external._send_sms_via_frappe_gateway", return_value=202),
		):
			result = send_frappe_sms(run, config, record={}, outputs={})
		require_consent.assert_called_once_with(
			run,
			channel="SMS",
			purpose="workflow",
			recipient="+41791234567",
			required=True,
		)
		self.assertEqual(result, {"recipient": "+41791234567", "status": "SENT", "status_code": 202, "consent_check": True})

	def test_external_dispatcher_returns_engine_result_contract(self):
		provider_output = {"status_code": 204, "response_hash": "abc"}
		with patch("finbyzai.workflow_builder.external.send_webhook", return_value=provider_output):
			result = execute_external(
				"action.webhook",
				object(),
				{},
				record={},
				outputs={},
				effect_key="effect-1",
			)
		self.assertEqual(result, {"status": "COMPLETE", "output": provider_output})

	def test_failed_effect_is_rearmed_for_safe_retry(self):
		payload = {"type": "action.send_email", "config": {"subject": "Retry"}}
		ledger = SimpleNamespace(
			status="FAILED",
			request_hash=frappe.utils.sha256_hash(canonical_json(payload)),
			result_json='{"error": "consent missing"}',
			completed_at=frappe.utils.now_datetime(),
			save=Mock(),
		)
		run = SimpleNamespace(name="ARUN-RETRY")
		token = SimpleNamespace(occurrence=1)
		node = {"id": "email"}
		with (
			patch("frappe.db.get_value", return_value="AEF-RETRY"),
			patch("frappe.get_doc", return_value=ledger),
		):
			result, completed = _claim_effect(run, token, node, payload)
		self.assertIs(result, ledger)
		self.assertFalse(completed)
		self.assertEqual(ledger.status, "STARTED")
		self.assertIsNone(ledger.result_json)
		self.assertIsNone(ledger.completed_at)
		ledger.save.assert_called_once_with(ignore_permissions=True)
