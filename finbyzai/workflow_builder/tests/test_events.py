from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from finbyzai.workflow_builder import engine, events, integrations
from finbyzai.workflow_builder.api import signal_event
from finbyzai.workflow_builder.authoring import (
	create_workflow_record,
	get_workflow_draft,
	publish_workflow,
	save_workflow_draft,
)
from finbyzai.workflow_builder.errors import AutomationTransientError


class TestAutomationEvents(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.flags.in_install = False
		frappe.flags.in_migrate = False
		frappe.flags.automation_dispatch_wake_registered = False
		self.enabled = patch.object(events, "automation_enabled", return_value=True)
		self.enabled.start()
		self.addCleanup(self.enabled.stop)
		self.runtime_allowed = patch.object(engine, "workflow_runtime_allowed", return_value=True)
		self.runtime_allowed.start()
		self.addCleanup(self.runtime_allowed.stop)

	def _publish(self, title: str, trigger_type: str = "trigger.document_insert") -> dict:
		created = create_workflow_record(title, "Lead", trigger_type=trigger_type)
		publish_workflow(created["workflow"], 0)
		return created

	def _waiting_policy_run(self, title: str, first_name: str, settings: dict) -> tuple[object, dict, str, str]:
		lead = frappe.get_doc({"doctype": "Lead", "first_name": first_name}).insert()
		created = create_workflow_record(title, "Lead")
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
		saved = save_workflow_draft(created["workflow"], 0, graph, settings)
		self.assertTrue(saved["valid"])
		published = publish_workflow(created["workflow"], 1, reenrollment="ALWAYS")
		run_name = engine.enroll(
			created["workflow"], "Lead", lead.name, source="MANUAL", occurrence_key=frappe.generate_hash(length=12)
		)
		engine.execute_token(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "trigger-1"}, "name")
		)
		engine.execute_token(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "name")
		)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "WAITING")
		timer_name = frappe.db.get_value("Automation Timer", {"run": run_name, "status": "ACTIVE"}, "name")
		self.assertTrue(timer_name)
		return lead, published, run_name, timer_name

	def _policy_event(self, lead_name: str) -> str:
		return frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead_name, "event_type": "ON_UPDATE", "status": "PENDING"},
			"name",
			order_by="creation desc",
		)

	def _save_policy_only(self, doc) -> None:
		"""Keep assertions independent from unrelated active subscriptions on the test site."""
		with patch.object(events, "_matching_subscriptions", return_value=[]):
			doc.save()

	def test_business_event_signal_releases_waits_and_enrolls_matching_workflows(self):
		record = frappe._dict(doctype="Lead", name="LEAD-EVENT")
		record.status = "Lead"
		record.check_permission = MagicMock()
		subscription = frappe._dict(
			workflow="WF-EVENT",
			workflow_version="WFV-EVENT",
			config_json=frappe.as_json(
				{
					"events": [
						{
							"id": "marketing-click",
							"event_topic": "email.clicked",
							"event_filter": {
								"kind": "predicate",
								"field": "email_type",
								"operator": "eq",
								"value": "Marketing",
							},
						}
					],
					"condition": {"kind": "predicate", "field": "status", "operator": "eq", "value": "Lead"},
				}
			),
		)
		with (
			patch.object(engine, "release_event_waiters", return_value=2) as release,
			patch.object(frappe, "get_doc", return_value=record),
			patch.object(frappe, "get_list", return_value=[subscription]),
			patch.object(
				frappe.db,
				"get_value",
				return_value=frappe._dict(status="ACTIVE", active_version="WFV-EVENT"),
			),
			patch.object(engine, "enroll", return_value="RUN-EVENT") as enroll_event,
		):
			result = signal_event(
				"email.clicked",
				{"event_id": "provider-event-1", "email_type": "Marketing"},
				record_doctype="Lead",
				record_name="LEAD-EVENT",
			)

		self.assertEqual(result["released"], 2)
		self.assertEqual(result["enrolled"], [{"workflow": "WF-EVENT", "run_id": "RUN-EVENT"}])
		release.assert_called_once_with(
			"email.clicked",
			{"event_id": "provider-event-1", "email_type": "Marketing"},
			record_doctype="Lead",
			record_name="LEAD-EVENT",
			source_doctype=None,
			source_name=None,
		)
		enroll_event.assert_called_once_with(
			"WF-EVENT",
			"Lead",
			"LEAD-EVENT",
			source="EVENT:email.clicked",
			occurrence_key="email.clicked:provider-event-1",
		)

	def test_business_event_signal_skips_nonmatching_event_criteria(self):
		record = frappe._dict(doctype="Lead", name="LEAD-EVENT", status="Lead")
		record.check_permission = MagicMock()
		subscription = frappe._dict(
			workflow="WF-EVENT",
			workflow_version="WFV-EVENT",
			config_json=frappe.as_json(
				{
					"events": [
						{
							"id": "marketing-click",
							"event_topic": "email.clicked",
							"event_filter": {"kind": "predicate", "field": "email_type", "operator": "eq", "value": "Marketing"},
						}
					]
				}
			),
		)
		with (
			patch.object(engine, "release_event_waiters", return_value=0),
			patch.object(frappe, "get_doc", return_value=record),
			patch.object(frappe, "get_list", return_value=[subscription]),
			patch.object(engine, "enroll") as enroll_event,
		):
			result = signal_event(
				"email.clicked",
				{"event_id": "provider-event-2", "email_type": "Transactional"},
				record_doctype="Lead",
				record_name="LEAD-EVENT",
			)

		self.assertEqual(result["enrolled"], [])
		enroll_event.assert_not_called()

	def test_abandoned_cart_event_enrolls_only_the_matching_configured_threshold(self):
		record = frappe._dict(doctype="Customer", name="CUSTOMER-1")
		subscriptions = [
			frappe._dict(
				workflow=f"WF-{hours}",
				workflow_version=f"WFV-{hours}",
				config_json=frappe.as_json(
					{
						"event_topic": "commerce.order.abandoned",
						"abandoned_after_value": hours,
						"abandoned_after_unit": "hours",
					}
				),
			)
			for hours in (6, 24)
		]
		with (
			patch.object(engine, "release_event_waiters") as release,
			patch.object(frappe, "get_doc", return_value=record),
			patch.object(frappe, "get_list", return_value=subscriptions),
			patch.object(
				frappe.db,
				"get_value",
				side_effect=lambda _doctype, workflow, *_args, **_kwargs: frappe._dict(
					status="ACTIVE", active_version=f"WFV-{workflow.removeprefix('WF-')}"
				),
			),
			patch.object(engine, "enroll", return_value="RUN-6") as enroll_event,
		):
			result = events.signal_business_event(
				"commerce.order.abandoned",
				{"event_id": "shopping-cart:CART-1:abandoned:6h", "abandoned_after_hours": 6},
				record_doctype="Customer",
				record_name="CUSTOMER-1",
			)

		self.assertEqual(result["released"], 0)
		self.assertEqual(result["enrolled"], [{"workflow": "WF-6", "run_id": "RUN-6"}])
		release.assert_not_called()
		enroll_event.assert_called_once_with(
			"WF-6",
			"Customer",
			"CUSTOMER-1",
			source="EVENT:commerce.order.abandoned",
			occurrence_key="commerce.order.abandoned:shopping-cart:CART-1:abandoned:6h",
		)

	def test_business_event_signal_indexes_the_exact_workflow_email_message(self):
		with patch.object(engine, "release_event_waiters", return_value=1) as release:
			result = events.signal_business_event(
				"email.opened",
				{"event_id": "open-1", "email_queue": "EMAIL-QUEUE-1"},
			)
		self.assertEqual(result["released"], 1)
		release.assert_called_once_with(
			"email.opened",
			{"event_id": "open-1", "email_queue": "EMAIL-QUEUE-1"},
			record_doctype=None,
			record_name=None,
			source_doctype="Email Queue",
			source_name="EMAIL-QUEUE-1",
		)

	def test_public_event_source_permission_is_checked_before_wait_release(self):
		source = MagicMock()
		source.check_permission.side_effect = frappe.PermissionError
		with (
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(engine, "release_event_waiters") as release,
			self.assertRaises(frappe.PermissionError),
		):
			events.signal_business_event(
				"record.updated",
				{"event_id": "source-event-1"},
				source_doctype="ToDo",
				source_name="TODO-PRIVATE",
				check_record_permission=True,
			)
		release.assert_not_called()

	def test_native_record_and_todo_wait_events_use_the_durable_outbox(self):
		lead = frappe._dict(doctype="Lead", name="LEAD-NATIVE", status="Open", docstatus=0, modified="2026-08-20 10:00:00")
		lead.flags = frappe._dict()
		lead.meta = SimpleNamespace(istable=False, issingle=False, is_virtual=False)
		lead.get_doc_before_save = lambda: frappe._dict(status="New")
		with (
			patch.object(events, "_native_wait_sources", return_value=(True, False)),
			patch.object(events.frappe.db, "table_exists", return_value=True),
		):
			lead_occurrences = events._native_wait_occurrences(lead, "ON_UPDATE", ["status"])
		self.assertEqual(lead_occurrences[0]["topic"], "record.updated")
		self.assertTrue(lead_occurrences[0]["enrolled"])

		outbox = SimpleNamespace(
			name="OUTBOX-NATIVE",
			event_id="outbox-native",
			event_type="ON_UPDATE",
			object_doctype="Lead",
			object_name="LEAD-NATIVE",
			changed_fields_json='["status"]',
			decision_json=frappe.as_json({"native_wait_events": lead_occurrences}),
			trace_id="TRACE-NATIVE",
			causation_id=None,
			recursion_depth=0,
		)
		with (
			patch.object(events.frappe, "get_doc", return_value=lead),
			patch.object(events, "_matching_subscriptions", return_value=[]),
			patch.object(events, "reevaluate_active_run_policies", return_value=[]),
			patch.object(events.engine, "release_event_waiters", return_value=1) as release,
			patch.object(events, "_complete_event") as complete,
		):
			events._process_event(outbox)
		release.assert_called_once_with(
			"record.updated",
			lead_occurrences[0]["payload"],
			record_doctype="Lead",
			record_name="LEAD-NATIVE",
			source_doctype=None,
			source_name=None,
		)
		self.assertEqual(complete.call_args.kwargs["decisions"][0]["kind"], "WAIT_EVENT")

		todo = frappe._dict(doctype="ToDo", name="TODO-NATIVE", status="Closed", allocated_to="Administrator", docstatus=0, modified="2026-08-20 10:05:00")
		todo.flags = frappe._dict()
		todo.meta = SimpleNamespace(istable=False, issingle=False, is_virtual=False)
		todo.get_doc_before_save = lambda: frappe._dict(status="Open")
		with (
			patch.object(events, "_native_wait_sources", side_effect=[(False, False), (False, True)]),
			patch.object(events.frappe.db, "table_exists", return_value=True),
		):
			todo_occurrences = events._native_wait_occurrences(todo, "ON_UPDATE", ["status"])
		self.assertEqual(todo_occurrences[0]["topic"], "workflow.todo.completed")
		self.assertTrue(todo_occurrences[0]["action_output"])

	def test_aircall_adapter_emits_only_terminal_inbound_links(self):
		doc = frappe._dict(
			doctype="Call Log",
			name="CALL-1",
			id="aircall-1",
			medium="Aircall",
			type="Incoming",
			status="Completed",
			duration=42,
			customer="CUSTOMER-1",
			links=[
				frappe._dict(link_doctype="Lead", link_name="LEAD-1"),
				frappe._dict(link_doctype="Opportunity", link_name="OPP-1"),
			],
		)
		doc["from"] = "+4912345"
		doc.get_doc_before_save = lambda: frappe._dict(status="In Progress")
		with patch(
			"aircall_integration.aircall_integration.call_context.get_contacts_matching_number",
			return_value=["CONTACT-1"],
		), patch.object(integrations, "_signal") as emit:
			integrations.capture_aircall_inbound_call(doc)
		self.assertEqual(emit.call_count, 4)
		self.assertEqual(
			{(call.args[1], call.args[2]) for call in emit.call_args_list},
			{
				("Contact", "CONTACT-1"),
				("Lead", "LEAD-1"),
				("Opportunity", "OPP-1"),
				("Customer", "CUSTOMER-1"),
			},
		)

	def test_customer_portal_login_adapter_targets_customer_only(self):
		login_manager = SimpleNamespace(user_type="Website User")
		with (
			patch("customer_portal.utils.portal.get_current_customer_name", return_value="CUSTOMER-1"),
			patch.object(integrations.frappe, "session", frappe._dict(sid="SESSION-1", user="portal@example.com")),
			patch.object(integrations, "_signal") as emit,
		):
			integrations.capture_customer_portal_login(login_manager)
		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[:3], ("commerce.store.login", "Customer", "CUSTOMER-1"))

	def test_lead_qualification_adapter_emits_only_on_transition(self):
		doc = frappe._dict(
			doctype="Lead",
			name="LEAD-1",
			qualification_status="Qualified",
			modified="2026-08-20 10:00:00",
		)
		doc.get_doc_before_save = lambda: frappe._dict(qualification_status="In Process")
		with patch.object(integrations, "_signal") as emit:
			integrations.capture_lead_qualified(doc)
		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[:3], ("crm.lead.qualified", "Lead", "LEAD-1"))

	def test_email_group_adapter_resolves_supported_crm_objects(self):
		doc = frappe._dict(
			doctype="Email Group Member",
			name="MEMBER-1",
			email_group="Newsletter",
			email="person@example.com",
			unsubscribed=0,
			modified="2026-08-20 10:00:00",
		)
		doc.get_doc_before_save = lambda: None
		with patch.object(
			integrations.frappe,
			"get_all",
			side_effect=[["CONTACT-1"], ["LEAD-1"]],
		), patch.object(integrations, "_signal") as emit:
			integrations.capture_email_group_membership(doc)
		self.assertEqual(
			{(call.args[1], call.args[2]) for call in emit.call_args_list},
			{("Contact", "CONTACT-1"), ("Lead", "LEAD-1")},
		)

	def test_sales_order_adapter_marks_customer_portal_checkout(self):
		doc = frappe._dict(
			doctype="Sales Order",
			name="SO-1",
			customer="CUSTOMER-1",
			order_type="Shopping Cart",
			items=[frappe._dict(prevdoc_docname="QTN-CART-1")],
		)
		with patch.object(integrations.frappe.db, "exists", return_value="QTN-CART-1"), patch.object(
			integrations, "_signal"
		) as emit:
			integrations.capture_sales_order_created(doc)
		payload = emit.call_args.args[3]
		self.assertEqual(emit.call_args.args[:3], ("commerce.order.created", "Customer", "CUSTOMER-1"))
		self.assertEqual(payload["source"], "Customer Portal")

	def test_web_form_adapter_emits_for_the_exact_saved_target(self):
		doc = frappe._dict(doctype="Lead", name="LEAD-WEB-FORM", modified="2026-08-20 10:00:00")
		doc.get_doc_before_save = lambda: None
		frappe.flags.in_web_form = True
		frappe.form_dict.web_form = "Public lead form"
		try:
			with patch.object(integrations, "_signal") as emit:
				integrations.capture_web_form_submission(doc)
		finally:
			frappe.flags.pop("in_web_form", None)
			frappe.form_dict.pop("web_form", None)
		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[:3], ("crm.form.submitted", "Lead", "LEAD-WEB-FORM"))
		self.assertEqual(emit.call_args.args[3]["form_name"], "Public lead form")

	def test_communication_adapter_normalizes_reply_and_email_delivery_status(self):
		doc = frappe._dict(
			doctype="Communication",
			name="COMM-1",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			sent_or_received="Received",
			communication_medium="Email",
			sender="person@example.com",
			delivery_status="Clicked",
			message_id="provider-message-1",
			subject="Nurture",
		)
		doc.get_doc_before_save = lambda: frappe._dict(sent_or_received="Sent", delivery_status="Sent")
		with patch.object(integrations.frappe.db, "get_value", return_value="EMAIL-QUEUE-1"), patch.object(
			integrations, "_signal"
		) as emit:
			integrations.capture_communication_event(doc)
		self.assertEqual([call.args[0] for call in emit.call_args_list], ["communication.responded", "email.clicked"])
		self.assertEqual(emit.call_args_list[1].args[3]["email_queue"], "EMAIL-QUEUE-1")

	def test_communication_adapter_emits_hard_and_soft_bounce_transitions(self):
		for delivery_status, expected_topic in (
			("Bounced", "email.hard_bounced"),
			("Soft-Bounced", "email.soft_bounced"),
		):
			with self.subTest(delivery_status=delivery_status):
				doc = frappe._dict(
					doctype="Communication",
					name=f"COMM-{delivery_status}",
					reference_doctype="Lead",
					reference_name="LEAD-1",
					sent_or_received="Sent",
					communication_medium="Email",
					sender="sender@example.com",
					delivery_status=delivery_status,
					message_id="provider-message-1",
					subject="Nurture",
				)
				doc.get_doc_before_save = lambda: frappe._dict(delivery_status="Sent")
				with (
					patch.object(integrations.frappe.db, "get_value", return_value="EMAIL-QUEUE-1"),
					patch.object(integrations, "_signal") as emit,
				):
					integrations.capture_communication_event(doc, "on_update")

				emit.assert_called_once()
				self.assertEqual(emit.call_args.args[:3], (expected_topic, "Lead", "LEAD-1"))
				self.assertEqual(emit.call_args.args[3]["email_queue"], "EMAIL-QUEUE-1")
				self.assertEqual(
					emit.call_args.args[4],
					f"communication:COMM-{delivery_status}:delivery:{delivery_status}",
				)

	def test_communication_adapter_does_not_repeat_unchanged_delivery_status(self):
		doc = frappe._dict(
			doctype="Communication",
			name="COMM-BOUNCE",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			sent_or_received="Sent",
			delivery_status="Bounced",
		)
		doc.get_doc_before_save = lambda: frappe._dict(delivery_status="Bounced")
		with patch.object(integrations, "_signal") as emit:
			integrations.capture_communication_event(doc, "on_update")

		emit.assert_not_called()

	def test_inbound_delivery_reports_update_the_exact_sent_message_and_emit_bounces(self):
		for subject, delivery_status, expected_topic in (
			("Delivery Status Notification (Failure)", "Bounced", "email.hard_bounced"),
			("Delivery Status Notification (Delay)", "Soft-Bounced", "email.soft_bounced"),
		):
			with self.subTest(subject=subject):
				incoming = frappe._dict(
					doctype="Communication",
					name=f"REPORT-{delivery_status}",
					sent_or_received="Received",
					sender="mailer-daemon@googlemail.com",
					subject=subject,
					in_reply_to="COMM-SENT-1",
				)
				incoming.get_doc_before_save = lambda: None
				outbound = frappe._dict(
					name="COMM-SENT-1",
					sent_or_received="Sent",
					reference_doctype="Lead",
					reference_name="LEAD-1",
					delivery_status="Sent",
					message_id="message-1",
					subject="Offer",
					sender="sender@example.com",
				)
				with (
					patch.object(
						integrations.frappe.db,
						"get_value",
						side_effect=[outbound, "EMAIL-QUEUE-1"],
					),
					patch.object(integrations.frappe.db, "set_value") as set_value,
					patch.object(integrations, "_signal") as emit,
				):
					integrations.capture_communication_event(incoming, "after_insert")

				set_value.assert_called_once_with(
					"Communication",
					"COMM-SENT-1",
					"delivery_status",
					delivery_status,
					update_modified=False,
				)
				emit.assert_called_once()
				self.assertEqual(emit.call_args.args[:3], (expected_topic, "Lead", "LEAD-1"))
				self.assertEqual(emit.call_args.args[3]["delivery_report"], f"REPORT-{delivery_status}")

	def test_delivery_report_is_never_treated_as_a_customer_reply(self):
		doc = frappe._dict(
			doctype="Communication",
			name="REPORT-UNMATCHED",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			sent_or_received="Received",
			sender="postmaster@example.com",
			subject="Undeliverable: Offer",
			in_reply_to="UNKNOWN-COMMUNICATION",
		)
		doc.get_doc_before_save = lambda: None
		with (
			patch.object(integrations.frappe.db, "get_value", side_effect=[None, None]),
			patch.object(integrations, "_signal") as emit,
		):
			integrations.capture_communication_event(doc, "after_insert")

		emit.assert_not_called()

	def test_outbound_frappe_communication_links_are_tracked_before_queueing(self):
		original = '<p><a href="https://example.com/offer">Offer</a></p>'
		doc = frappe._dict(
			doctype="Communication",
			name="COMM-MANUAL-1",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			sent_or_received="Sent",
			communication_medium="Email",
			content=original,
			delivery_status="",
		)
		doc.get_doc_before_save = lambda: None
		tracked = '<p><a href="https://site.test/tracked">Offer</a></p>'
		with (
			patch(
				"finbyzai.workflow_builder.tracking.decorate_workflow_email_links",
				return_value=(tracked, 1),
			) as decorate,
			patch.object(integrations.frappe.db, "set_value") as set_value,
		):
			integrations.capture_communication_event(doc, "after_insert")

		decorate.assert_called_once_with(original, "COMM-MANUAL-1")
		self.assertEqual(doc.content, tracked)
		set_value.assert_called_once_with(
			"Communication", "COMM-MANUAL-1", "content", tracked, update_modified=False
		)

	def test_visual_template_communication_adds_open_pixel_before_raw_queueing(self):
		doc = frappe._dict(
			doctype="Communication",
			name="COMM-VISUAL-1",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			sent_or_received="Sent",
			communication_medium="Email",
			email_template="Visual campaign",
			content="<html><body><p>No links required</p></body></html>",
			delivery_status="",
		)
		doc.get_doc_before_save = lambda: None
		with (
			patch.object(integrations.frappe, "get_cached_value", return_value=1),
			patch.object(integrations.frappe.db, "set_value") as set_value,
		):
			integrations.capture_communication_event(doc, "after_insert")

		self.assertEqual(doc.content.count("<!--email_open_check-->"), 1)
		self.assertLess(doc.content.index("<!--email_open_check-->"), doc.content.lower().index("</body>"))
		set_value.assert_called_once_with(
			"Communication", "COMM-VISUAL-1", "content", doc.content, update_modified=False
		)

	def test_installed_email_tracking_event_emits_for_its_exact_linked_record(self):
		doc = frappe._dict(
			doctype="Email Tracking Event",
			name="TRACK-1",
			event_type="Clicked",
			communication="COMM-1",
			lead="LEAD-FALLBACK",
			contact=None,
			url="https://example.com/offer",
		)
		communication = frappe._dict(
			reference_doctype="Lead",
			reference_name="LEAD-1",
			subject="Offer",
			sender="sender@example.com",
			message_id="provider-message-1",
		)
		with (
			patch.object(
				integrations.frappe.db,
				"get_value",
				side_effect=[communication, "EMAIL-QUEUE-1"],
			),
			patch.object(integrations, "_signal") as emit,
		):
			integrations.capture_email_tracking_event(doc)

		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[:3], ("email.clicked", "Lead", "LEAD-1"))
		self.assertEqual(emit.call_args.args[3]["email_queue"], "EMAIL-QUEUE-1")
		self.assertEqual(emit.call_args.args[3]["link_url"], "https://example.com/offer")

	def test_installed_email_tracking_maps_open_and_bounce_events(self):
		communication = frappe._dict(
			reference_doctype="Lead",
			reference_name="LEAD-1",
			subject="Offer",
			sender="sender@example.com",
			message_id="provider-message-1",
		)
		for event_type, expected_topic in (
			("Opened", "email.opened"),
			("Bounced", "email.hard_bounced"),
			("Soft-Bounced", "email.soft_bounced"),
		):
			with self.subTest(event_type=event_type):
				doc = frappe._dict(
					doctype="Email Tracking Event",
					name=f"TRACK-{event_type}",
					event_type=event_type,
					communication="COMM-1",
					lead=None,
					contact=None,
					url=None,
				)
				with (
					patch.object(
						integrations.frappe.db,
						"get_value",
						side_effect=[communication, "EMAIL-QUEUE-1"],
					),
					patch.object(integrations, "_signal") as emit,
				):
					integrations.capture_email_tracking_event(doc)

				emit.assert_called_once()
				self.assertEqual(emit.call_args.args[:3], (expected_topic, "Lead", "LEAD-1"))
				self.assertEqual(emit.call_args.args[3]["email_queue"], "EMAIL-QUEUE-1")

	def test_complaint_event_is_not_produced(self):
		self.assertNotIn("Marked As Spam", integrations.EMAIL_DELIVERY_TOPICS)
		doc = frappe._dict(
			doctype="Communication",
			name="COMM-SPAM",
			reference_doctype="Lead",
			reference_name="LEAD-1",
			sent_or_received="Sent",
			delivery_status="Marked As Spam",
		)
		doc.get_doc_before_save = lambda: frappe._dict(delivery_status="Sent")
		with patch.object(integrations, "_signal") as emit:
			integrations.capture_communication_event(doc, "on_update")

		emit.assert_not_called()

	def test_reach_campaign_tracking_row_does_not_duplicate_topic_unsubscribe(self):
		doc = frappe._dict(
			doctype="Email Tracking Event",
			name="TRACK-UNSUBSCRIBE-1",
			event_type="Unsubscribed",
			marketing_campaign_recipient="RECIPIENT-1",
			communication="COMM-1",
			lead="LEAD-1",
			contact=None,
			url=None,
		)
		with patch.object(integrations, "_signal") as emit:
			integrations.capture_email_tracking_event(doc)

		emit.assert_not_called()

	def test_global_email_unsubscribe_emits_for_its_exact_non_lead_record(self):
		doc = frappe._dict(
			doctype="Email Unsubscribe",
			name="UNSUBSCRIBE-1",
			email="customer@example.com",
			reference_doctype="Customer",
			reference_name="CUSTOMER-1",
			global_unsubscribe=1,
		)
		with patch.object(integrations, "_signal") as emit:
			integrations.capture_email_unsubscribe(doc)

		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[:3], ("email.unsubscribed", "Customer", "CUSTOMER-1"))
		self.assertEqual(emit.call_args.args[3]["email_type"], "global")

	def test_non_lead_workflow_unsubscribe_creates_global_opt_out_with_exact_reference(self):
		unsubscribe_doc = MagicMock()
		with (
			patch.object(integrations.frappe.db, "exists", return_value=True),
			patch.object(integrations.frappe, "get_doc", return_value=unsubscribe_doc) as get_doc,
			patch.object(integrations.frappe.db, "commit") as commit,
			patch.object(integrations.frappe, "respond_as_web_page") as respond,
		):
			integrations.unsubscribe_workflow_email(
				"Customer", "CUSTOMER-1", "CUSTOMER@EXAMPLE.COM"
			)

		get_doc.assert_called_once_with({
			"doctype": "Email Unsubscribe",
			"email": "customer@example.com",
			"reference_doctype": "Customer",
			"reference_name": "CUSTOMER-1",
			"global_unsubscribe": 1,
		})
		unsubscribe_doc.insert.assert_called_once_with(ignore_permissions=True)
		commit.assert_called_once()
		respond.assert_called_once()

	def test_abandoned_cart_adapter_emits_only_unconverted_customer_portal_carts(self):
		rows = [
			frappe._dict(name="QTN-LEAD", quotation_to="Lead", party_name="LEAD-1", contact_person="CONTACT-1", modified=now_datetime()),
			frappe._dict(name="QTN-ABANDONED", quotation_to="Customer", party_name="CUSTOMER-1", contact_person="CONTACT-1", modified=now_datetime()),
			frappe._dict(name="QTN-CONVERTED", quotation_to="Customer", party_name="CUSTOMER-1", contact_person=None, modified=now_datetime()),
		]
		with (
			patch.object(integrations, "_runtime_ready", return_value=True),
			patch.object(integrations.frappe, "get_all", return_value=rows) as get_all,
			patch.object(integrations.frappe.db, "exists", side_effect=lambda doctype, filters=None: filters.get("prevdoc_docname") == "QTN-CONVERTED"),
			patch.object(integrations, "_signal") as emit,
		):
			count = integrations.capture_abandoned_shopping_carts()
		self.assertEqual(count, 1)
		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[1:3], ("Customer", "CUSTOMER-1"))
		self.assertEqual(emit.call_args.args[3]["abandoned_after_hours"], 24)
		query = get_all.call_args.kwargs
		self.assertEqual(query["order_by"], "modified desc, name desc")
		self.assertEqual(query["filters"]["quotation_to"], "Customer")
		self.assertEqual(query["filters"]["modified"][0], "between")

	def test_abandoned_cart_adapter_pages_through_the_entire_transition_window(self):
		full_page = [frappe._dict(name=f"QTN-{index}", quotation_to="", party_name="", contact_person=None) for index in range(500)]
		last_page = [frappe._dict(name="QTN-500", quotation_to="", party_name="", contact_person=None)]

		def page(_doctype, **kwargs):
			return full_page if kwargs.get("start") == 0 else last_page

		with (
			patch.object(integrations, "_runtime_ready", return_value=True),
			patch.object(integrations.frappe, "get_all", side_effect=page) as get_all,
			patch.object(integrations.frappe.db, "exists", return_value=False),
		):
			self.assertEqual(integrations.capture_abandoned_shopping_carts(), 0)
		quotation_pages = [call for call in get_all.call_args_list if "start" in call.kwargs]
		self.assertEqual([call.kwargs["start"] for call in quotation_pages], [0, 500])

	def test_abandoned_cart_adapter_uses_all_active_workflow_thresholds(self):
		subscriptions = [
			frappe._dict(config_json=frappe.as_json({"event_topic": "commerce.order.abandoned", "abandoned_after_value": 6, "abandoned_after_unit": "hours"})),
			frappe._dict(config_json=frappe.as_json({"event_topic": "commerce.order.abandoned", "abandoned_after_value": 2, "abandoned_after_unit": "days"})),
		]

		def rows(doctype, **_kwargs):
			return subscriptions if doctype == "Automation Trigger Subscription" else []

		with (
			patch.object(integrations, "_runtime_ready", return_value=True),
			patch.object(integrations.frappe, "get_all", side_effect=rows) as get_all,
		):
			self.assertEqual(integrations.capture_abandoned_shopping_carts(), 0)

		quotation_queries = [call for call in get_all.call_args_list if call.args[0] == "Quotation"]
		self.assertEqual(len(quotation_queries), 3)

	def test_reach_topic_preference_bridge_emits_only_new_lead_opt_outs(self):
		before = {"Product news": "ROW-OLD"}
		after = {"Product news": "ROW-OLD", "Events": "ROW-NEW"}
		with (
			patch("finbyzreach.email_marketing.update_subscription_preferences", return_value="success") as update,
			patch.object(integrations.frappe.db, "get_value", return_value="LEAD-1"),
			patch.object(integrations, "_reach_unsubscribe_topic_rows", side_effect=[before, after]),
			patch.object(integrations, "_signal") as emit,
		):
			result = integrations.update_reach_subscription_preferences(
				campaign_recipient="RECIPIENT-1",
				email="lead@example.com",
				unsubscribed_topics='["Product news", "Events"]',
				signed_query_string="signed",
			)
		self.assertEqual(result, "success")
		update.assert_called_once()
		emit.assert_called_once()
		self.assertEqual(emit.call_args.args[0:3], ("email.unsubscribed", "Lead", "LEAD-1"))
		self.assertEqual(emit.call_args.args[3]["email_type"], "topic")
		self.assertEqual(emit.call_args.args[3]["subscription_topic"], "Events")
		self.assertEqual(emit.call_args.args[4], "reach-topic-unsubscribe:ROW-NEW")

	def test_stop_on_response_cancels_the_run_and_its_timer(self):
		lead, _published, run_name, timer_name = self._waiting_policy_run(
			"Stop on response",
			"Response Policy",
			{"communication": {"stop_on_response": True}},
		)
		stopped = engine.apply_response_policy(
			"Lead",
			lead.name,
			{"communication": "COMM-RESPONSE"},
		)
		self.assertEqual(stopped, 1)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "CANCELLED")
		self.assertEqual(frappe.db.get_value("Automation Timer", timer_name, "status"), "CANCELLED")
		self.assertIsNotNone(frappe.db.get_value("Automation Timer", timer_name, "released_at"))
		waiting_token = frappe.db.get_value(
			"Automation Run Token",
			{"run": run_name, "node_id": "delay"},
			["status", "completed_at"],
			as_dict=True,
		)
		self.assertEqual(waiting_token.status, "CANCELLED")
		self.assertIsNotNone(waiting_token.completed_at)

	def test_no_subscription_creates_no_outbox_or_queue_wake(self):
		with patch.object(events, "_matching_subscriptions", return_value=[]), patch.object(
			events, "_register_dispatch_wake"
		) as wake:
			lead = frappe.get_doc({"doctype": "Lead", "first_name": "No Subscription"}).insert()
		self.assertFalse(
			frappe.db.exists("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		)
		wake.assert_not_called()

	def test_filter_criteria_subscription_listens_to_insert_and_update_events(self):
		created = create_workflow_record("Filter criteria trigger", "Lead", trigger_type="trigger.filter_criteria")
		graph = created["graph"]
		graph["nodes"][0]["config"] = {
			"condition": {"kind": "predicate", "field": "status", "operator": "eq", "value": "Lead"}
		}
		self.assertTrue(save_workflow_draft(created["workflow"], 0, graph)["valid"])
		published = publish_workflow(created["workflow"], 1)
		subscription = frappe.db.get_value(
			"Automation Trigger Subscription",
			{"workflow_version": published["version"]},
			["name", "event_type"],
			as_dict=True,
		)
		self.assertEqual(subscription.event_type, "ON_UPDATE")
		self.assertIn(subscription.name, {row.name for row in events._matching_subscriptions("Lead", "AFTER_INSERT")})
		self.assertIn(subscription.name, {row.name for row in events._matching_subscriptions("Lead", "ON_UPDATE")})

	def test_internal_framework_doctypes_are_rejected_before_subscription_lookup(self):
		doc = frappe.get_doc({"doctype": "Error Log", "method": "automation test", "error": "test"})
		with patch.object(events, "_matching_subscriptions") as subscriptions:
			events._capture(doc, "AFTER_INSERT")
		subscriptions.assert_not_called()

	def test_source_rollback_removes_outbox_event(self):
		self._publish("Rollback event")
		frappe.db.savepoint("before_source")
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Rolled Back Source"}).insert()
		self.assertTrue(
			frappe.db.exists("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		)
		frappe.db.rollback(save_point="before_source")
		self.assertFalse(frappe.db.exists("Lead", lead.name))
		self.assertFalse(
			frappe.db.exists("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		)

	def test_many_source_documents_register_one_dispatch_wake(self):
		self._publish("Singleton wake")
		frappe.flags.automation_dispatch_wake_registered = False
		before = frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "event_type": "AFTER_INSERT"})
		callbacks_before = list(frappe.db.after_commit._functions).count(events._enqueue_dispatcher_safely)
		for index in range(20):
			frappe.get_doc({"doctype": "Lead", "first_name": f"Batch {index}"}).insert()
		callbacks_after = list(frappe.db.after_commit._functions).count(events._enqueue_dispatcher_safely)
		self.assertEqual(callbacks_after, callbacks_before + 1)
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "event_type": "AFTER_INSERT"}),
			before + 20,
		)

	def test_queue_overload_defers_to_scheduler_without_raising(self):
		with patch.object(frappe, "enqueue", side_effect=frappe.QueueOverloaded("full")):
			self.assertFalse(events._enqueue_dispatcher_safely())

	def test_runtime_health_includes_failed_stale_orphaned_and_operator_work(self):
		def count(doctype, filters=None):
			if doctype == "Automation Trigger Subscription":
				return 1
			if doctype == "Automation Run" and (filters or {}).get("status") == "FAILED":
				return 2
			if doctype == "Automation Effect Ledger":
				return 5
			if doctype == "Automation Incident":
				return 3
			if doctype == "Automation Dead Letter":
				return 4
			return 0

		active_run = SimpleNamespace(
			name="RUN-ORPHAN",
			workflow="MISSING-WORKFLOW",
			workflow_version="MISSING-VERSION",
			modified=frappe.utils.add_to_date(now_datetime(), minutes=-30),
		)
		with (
			patch.object(frappe, "get_list", return_value=[]),
			patch.object(frappe, "get_all", return_value=[active_run]),
			patch.object(frappe.db, "count", side_effect=count),
			patch.object(frappe.db, "get_value", return_value=None),
			patch.object(frappe.db, "exists", return_value=False),
			patch.object(events, "int_setting", side_effect=lambda name, default: 24 if name == "health_failure_window_hours" else 15),
			patch("frappe.utils.background_jobs.get_queue", return_value=SimpleNamespace(count=0)),
			patch("frappe.utils.background_jobs.get_job_status", return_value=None),
		):
			health = events.runtime_health("WF-1")

		self.assertFalse(health["healthy"])
		self.assertEqual(health["runs"]["recent_failed"], 2)
		self.assertEqual(health["runs"]["stale_active"], 1)
		self.assertEqual(health["runs"]["orphaned_active"], 1)
		self.assertEqual(health["stale_external_effects"], 5)
		self.assertEqual(health["open_incidents"], 3)
		self.assertEqual(health["open_dead_letters"], 4)
		self.assertTrue(
			{
				"RECENT_FAILED_RUNS",
				"STALE_ACTIVE_RUNS",
				"ORPHANED_ACTIVE_RUNS",
				"STALE_EXTERNAL_EFFECTS",
				"OPEN_INCIDENTS",
				"OPEN_DEAD_LETTERS",
			}.issubset(health["reasons"])
		)

	def test_after_insert_subscription_enrolls_without_runtime_versions(self):
		created = self._publish("Insert event")
		test_subscriptions = [
			row for row in events._matching_subscriptions("Lead", "AFTER_INSERT")
			if row.workflow == created["workflow"]
		]
		self.assertEqual(len(test_subscriptions), 1)
		with patch.object(events, "_matching_subscriptions", return_value=test_subscriptions):
			lead = frappe.get_doc({"doctype": "Lead", "first_name": "Inserted Event"}).insert()
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "AFTER_INSERT"},
			"name",
		)
		before_versions = frappe.db.count("Version", {"ref_doctype": "Automation Outbox Event"})
		with patch.object(events, "_matching_subscriptions", return_value=test_subscriptions), patch.object(frappe.db, "commit") as commit:
			self.assertEqual(events.process_outbox_event(event_name), 1)
		commit.assert_not_called()
		self.assertEqual(
			frappe.db.count("Version", {"ref_doctype": "Automation Outbox Event"}), before_versions
		)
		self.assertTrue(
			frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name})
		)

	def test_stale_subscription_is_rejected_during_dispatch_not_source_save(self):
		created = self._publish("Stale subscription boundary")
		subscriptions = [
			row for row in events._matching_subscriptions("Lead", "AFTER_INSERT")
			if row.workflow == created["workflow"]
		]
		with patch.object(events, "_matching_subscriptions", return_value=subscriptions):
			lead = frappe.get_doc({"doctype": "Lead", "first_name": "Stale subscription"}).insert()
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "AFTER_INSERT"},
			"name",
		)
		frappe.db.set_value("Automation Workflow", created["workflow"], "status", "PAUSED")
		with patch.object(events, "_matching_subscriptions", return_value=subscriptions):
			self.assertEqual(events.process_outbox_event(event_name), 0)
		self.assertFalse(frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name}))
		self.assertTrue(
			frappe.db.exists(
				"Automation Enrollment Decision",
				{"workflow": created["workflow"], "record_name": lead.name, "reason_code": "STALE_SUBSCRIPTION"},
			)
		)

	def test_irrelevant_update_is_filtered_before_outbox_insert(self):
		created = create_workflow_record("Relevant update", "Lead", trigger_type="trigger.document_change")
		graph = created["graph"]
		graph["nodes"][0]["config"] = {
			"condition": {
				"kind": "predicate",
				"field": "company_name",
				"operator": "eq",
				"value": "Qualified Company",
			}
		}
		save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], 1)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Updated Event"}).insert()
		before = frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		lead.first_name = "Unrelated Name"
		lead.save()
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name}),
			before,
		)
		lead.company_name = "Qualified Company"
		lead.save()
		# A relevant update coalesces into an already-pending ON_UPDATE event instead
		# of creating duplicate queue work for the same record.
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name}),
			before,
		)
		pending_fields = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "ON_UPDATE", "status": "PENDING"},
			"changed_fields_json",
		)
		self.assertIn("company_name", frappe.parse_json(pending_fields))
		self.assertEqual(frappe.get_meta("Workflow").module, "Workflow")

	def test_document_change_can_require_an_explicit_watched_field(self):
		created = create_workflow_record("Watched field update", "Lead", trigger_type="trigger.document_change")
		graph = created["graph"]
		graph["nodes"][0]["config"] = {
			"watch_fields": ["company_name"],
			"condition": {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Watcher"},
		}
		save_workflow_draft(created["workflow"], 0, graph)
		publish_workflow(created["workflow"], 1)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Watcher"}).insert()

		lead.first_name = "Unwatched change"
		lead.save()
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "ON_UPDATE", "status": "PENDING"},
			"name",
		)
		self.assertTrue(event_name)
		self.assertEqual(events.process_outbox_event(event_name), 0)
		self.assertFalse(frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name}))

		lead.first_name = "Watcher"
		lead.company_name = "Watched Company"
		lead.save()
		event_name = frappe.db.get_value(
			"Automation Outbox Event",
			{"object_doctype": "Lead", "object_name": lead.name, "event_type": "ON_UPDATE", "status": "PENDING"},
			"name",
		)
		self.assertEqual(events.process_outbox_event(event_name), 1)
		self.assertTrue(frappe.db.exists("Automation Run", {"workflow": created["workflow"], "record_name": lead.name}))

	def test_relevant_change_completes_live_run_and_cancels_durable_wait(self):
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Goal reached"}
		lead, _published, run_name, timer_name = self._waiting_policy_run(
			"Event-driven goal", "Working", {"goal_condition": goal}
		)
		lead.first_name = "Goal reached"
		self._save_policy_only(lead)
		event_name = self._policy_event(lead.name)
		self.assertTrue(event_name)
		before_goal_checks = events.operation_snapshot()["policy_evaluations"]["counts"]["GOAL_MET"]

		self.assertEqual(events.process_outbox_event(event_name), 0)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")
		self.assertEqual(frappe.db.get_value("Automation Timer", timer_name, "status"), "CANCELLED")
		self.assertIsNotNone(frappe.db.get_value("Automation Timer", timer_name, "released_at"))
		self.assertEqual(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "status"),
			"CANCELLED",
		)
		self.assertIsNotNone(
			frappe.db.get_value("Automation Run Token", {"run": run_name, "node_id": "delay"}, "completed_at")
		)
		evaluation = frappe.get_doc("Automation Policy Evaluation", {"run": run_name, "outbox_event": event_name})
		self.assertEqual(evaluation.outcome, "GOAL_MET")
		self.assertEqual(evaluation.reason_code, "GOAL_CONDITION_TRUE")
		self.assertEqual(
			frappe.db.count("Automation Run Event", {"run": run_name, "event_type": "RUN_GOAL_MET"}), 1
		)
		policy_snapshot = events.operation_snapshot()["policy_evaluations"]
		self.assertEqual(policy_snapshot["counts"]["GOAL_MET"], before_goal_checks + 1)
		self.assertTrue(any(row.run == run_name for row in policy_snapshot["recent"]))

	def test_relevant_change_cancels_run_that_loses_eligibility(self):
		eligibility = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Eligible"}
		lead, _published, run_name, timer_name = self._waiting_policy_run(
			"Event-driven eligibility",
			"Eligible",
			{"unenroll_when_ineligible": True, "eligibility_condition": eligibility},
		)
		lead.first_name = "No longer eligible"
		self._save_policy_only(lead)
		event_name = self._policy_event(lead.name)
		self.assertTrue(event_name)

		events.process_outbox_event(event_name)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "CANCELLED")
		self.assertEqual(frappe.db.get_value("Automation Timer", timer_name, "status"), "CANCELLED")
		self.assertIsNotNone(frappe.db.get_value("Automation Timer", timer_name, "released_at"))
		self.assertEqual(
			frappe.db.get_value("Automation Policy Evaluation", {"run": run_name}, "outcome"),
			"ELIGIBILITY_LOST",
		)
		self.assertEqual(
			frappe.db.count("Automation Run Event", {"run": run_name, "event_type": "RUN_ELIGIBILITY_LOST"}), 1
		)

	def test_policy_capture_filters_unrelated_changes_and_snapshot_runs(self):
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Done"}
		lead, _published, run_name, _timer_name = self._waiting_policy_run(
			"Relevant policy fields", "Working", {"goal_condition": goal}
		)
		before = frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name})
		lead.company_name = "Unrelated"
		self._save_policy_only(lead)
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": lead.name}), before
		)
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "WAITING")

		snapshot_lead, _published, snapshot_run, _timer_name = self._waiting_policy_run(
			"Snapshot policy", "Snapshot working", {"read_mode": "ENROLLMENT_SNAPSHOT", "goal_condition": goal}
		)
		snapshot_before = frappe.db.count(
			"Automation Outbox Event", {"object_doctype": "Lead", "object_name": snapshot_lead.name}
		)
		snapshot_lead.first_name = "Done"
		self._save_policy_only(snapshot_lead)
		self.assertEqual(
			frappe.db.count("Automation Outbox Event", {"object_doctype": "Lead", "object_name": snapshot_lead.name}),
			snapshot_before,
		)
		self.assertEqual(frappe.db.get_value("Automation Run", snapshot_run, "status"), "WAITING")

	def test_policy_evaluation_is_deduplicated_per_run_and_event(self):
		goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Done"}
		lead, _published, run_name, _timer_name = self._waiting_policy_run(
			"Policy evaluation dedupe", "Working", {"goal_condition": goal}
		)
		lead.first_name = "Still working"
		self._save_policy_only(lead)
		event_name = self._policy_event(lead.name)
		event = frappe.get_doc("Automation Outbox Event", event_name)
		arguments = {
			"outbox_event": event.name,
			"event_id": event.event_id,
			"record_doctype": event.object_doctype,
			"record_name": event.object_name,
			"changed_fields": set(frappe.parse_json(event.changed_fields_json)),
		}
		first = engine.reevaluate_active_run_policies(**arguments)
		second = engine.reevaluate_active_run_policies(**arguments)
		self.assertEqual(first[0]["outcome"], "NO_CHANGE")
		self.assertTrue(second[0]["deduplicated"])
		self.assertEqual(
			frappe.db.count("Automation Policy Evaluation", {"run": run_name, "event_id": event.event_id}), 1
		)

	def test_active_run_uses_its_pinned_policy_after_new_publish(self):
		old_goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "Old goal"}
		lead, first_version, run_name, _timer_name = self._waiting_policy_run(
			"Pinned lifecycle policy", "Working", {"goal_condition": old_goal}
		)
		draft = get_workflow_draft(first_version["workflow_id"])["draft"]
		new_goal = {"kind": "predicate", "field": "first_name", "operator": "eq", "value": "New goal"}
		saved = save_workflow_draft(
			first_version["workflow_id"], draft["draft_revision"], draft["graph"], {"goal_condition": new_goal}
		)
		second_version = publish_workflow(first_version["workflow_id"], saved["draft_revision"], reenrollment="ALWAYS")
		self.assertNotEqual(first_version["version"], second_version["version"])
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "workflow_version"), first_version["version"])

		lead.first_name = "Old goal"
		self._save_policy_only(lead)
		events.process_outbox_event(self._policy_event(lead.name))
		self.assertEqual(frappe.db.get_value("Automation Run", run_name, "status"), "COMPLETED")
		self.assertEqual(
			frappe.db.get_value("Automation Policy Evaluation", {"run": run_name}, "workflow_version"),
			first_version["version"],
		)

	def test_transient_failure_retries_and_expired_lease_recovers(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "Retry Source"}).insert()
		event = frappe.get_doc(
			{
				"doctype": "Automation Outbox Event",
				"event_id": frappe.generate_hash(length=32),
				"event_type": "AFTER_INSERT",
				"object_doctype": "Lead",
				"object_name": lead.name,
				"status": "PROCESSING",
				"attempts": 1,
				"available_at": now_datetime(),
				"lease_owner": "dead-worker",
				"lease_until": now_datetime() - timedelta(minutes=1),
			}
		).insert(ignore_permissions=True)
		events._fail_event(event.name, 1, AutomationTransientError("temporary"))
		row = frappe.db.get_value(
			"Automation Outbox Event", event.name, ["status", "error_code", "available_at"], as_dict=True
		)
		self.assertEqual(row.status, "FAILED")
		self.assertEqual(row.error_code, "WF_TRANSIENT")
		frappe.db.set_value(
			"Automation Outbox Event",
			event.name,
			{"status": "PROCESSING", "lease_until": now_datetime() - timedelta(seconds=1)},
			update_modified=False,
		)
		self.assertEqual(events._recover_expired_leases(), 1)
		self.assertEqual(frappe.db.get_value("Automation Outbox Event", event.name, "status"), "FAILED")
