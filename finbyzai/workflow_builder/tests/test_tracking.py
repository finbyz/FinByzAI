from unittest.mock import patch

import frappe
from bs4 import BeautifulSoup
from frappe.tests import IntegrationTestCase

from finbyzai.workflow_builder import tracking


class TestWorkflowEmailTracking(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.local.response = frappe._dict()

	def test_decorator_wraps_only_normal_http_links(self):
		html = (
			'<a href="https://example.com/page?a=1">Page</a>'
			'<a href="mailto:person@example.com">Email</a>'
			'<a href="https://example.com/manage_subscriptions">Preferences</a>'
			'<a href="https://site.test/api/method/finbyzreach.email_marketing.track_marketing_click?x=1">Reach</a>'
			'<a href="https://site.test/api/method/megasol_customisation.megasol_customisation.ai_outreach.track_click?x=1">Outreach</a>'
		)
		with (
			patch.object(tracking, "get_signed_params", return_value="signed=params") as signed,
			patch.object(tracking, "get_url", side_effect=lambda value: f"https://site.test{value}"),
		):
			result, count = tracking.decorate_workflow_email_links(html, "COMM-1")

		links = BeautifulSoup(result, "html.parser").find_all("a")
		self.assertEqual(count, 1)
		self.assertIn(tracking.WORKFLOW_CLICK_METHOD, links[0]["href"])
		self.assertEqual(links[1]["href"], "mailto:person@example.com")
		self.assertEqual(links[2]["href"], "https://example.com/manage_subscriptions")
		self.assertIn("track_marketing_click", links[3]["href"])
		self.assertIn("ai_outreach.track_click", links[4]["href"])
		signed.assert_called_once_with(
			{
				"communication": "COMM-1",
				"link_id": "1",
				"url": "https://example.com/page?a=1",
			}
		)

	def test_raw_visual_email_receives_one_open_tracking_placeholder(self):
		html = "<html><body><p>Visual email</p></body></html>"
		result = tracking.ensure_workflow_open_tracking(html)

		self.assertEqual(result.count(tracking.EMAIL_OPEN_PLACEHOLDER), 1)
		self.assertLess(result.index(tracking.EMAIL_OPEN_PLACEHOLDER), result.lower().index("</body>"))
		self.assertEqual(tracking.ensure_workflow_open_tracking(result), result)

	def test_open_tracking_placeholder_supports_html_fragments(self):
		result = tracking.ensure_workflow_open_tracking("<p>Visual fragment</p>")
		self.assertTrue(result.startswith("<p>Visual fragment</p>"))
		self.assertEqual(result.count(tracking.EMAIL_OPEN_PLACEHOLDER), 1)

	def test_click_marks_communication_and_emits_open_then_click(self):
		context = frappe._dict(
			name="COMM-1",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			subject="Welcome",
			sender="sender@example.com",
			message_id="message-1",
			sent_or_received="Sent",
			read_by_recipient=0,
			read_by_recipient_on=None,
		)
		with (
			patch.object(tracking, "_communication_context", return_value=context),
			patch.object(tracking, "_queue_for_communication", return_value="QUEUE-1"),
			patch.object(tracking, "_emit_tracking_event") as emit,
			patch.object(frappe.db, "set_value") as set_value,
			patch.object(frappe.db, "commit") as commit,
		):
			tracking.track_workflow_email_click(
				communication="COMM-1",
				link_id="2",
				url="https://example.com/page",
			)

		set_value.assert_called_once()
		self.assertEqual([call.args[0] for call in emit.call_args_list], ["email.opened", "email.clicked"])
		self.assertEqual(emit.call_args_list[1].kwargs["url"], "https://example.com/page")
		self.assertEqual(emit.call_args_list[1].kwargs["link_id"], "2")
		commit.assert_called_once_with()
		self.assertEqual(frappe.local.response["type"], "redirect")
		self.assertEqual(frappe.local.response["location"], "https://example.com/page")

	def test_open_pixel_updates_once_and_emits_correlated_event(self):
		context = frappe._dict(
			name="COMM-1",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			subject="Welcome",
			sender="sender@example.com",
			message_id="message-1",
			sent_or_received="Sent",
			read_by_recipient=0,
			read_by_recipient_on=None,
		)
		with (
			patch.object(tracking, "_communication_context", return_value=context),
			patch.object(tracking, "_queue_for_communication", return_value="QUEUE-1"),
			patch(
				"frappe.core.doctype.communication.email.update_communication_as_read"
			) as update_read,
			patch.object(tracking, "_emit_tracking_event") as emit,
			patch.object(tracking, "commit_after_response", side_effect=lambda callback: callback()),
		):
			tracking.mark_workflow_email_as_seen("COMM-1")

		update_read.assert_called_once_with("COMM-1")
		emit.assert_called_once_with(
			"email.opened",
			context,
			queue_name="QUEUE-1",
			event_id="communication:COMM-1:delivery:Opened",
		)

	def test_frappe_outbound_communication_insert_persists_click_tracking(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Tracked manual email"}).insert()
		communication = frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": "Tracked offer",
				"content": '<p><a href="https://example.com/offer">Offer</a></p>',
				"sender": "sender@example.com",
				"recipients": "recipient@example.com",
				"reference_doctype": "Lead",
				"reference_name": lead.name,
			}
		).insert(ignore_permissions=True)

		communication.reload()
		link = BeautifulSoup(communication.content, "html.parser").find("a")
		self.assertIsNotNone(link)
		self.assertIn(tracking.WORKFLOW_CLICK_METHOD, link["href"])
		self.assertIn(f"communication={communication.name}", link["href"])
