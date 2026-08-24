from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime, validate_email_address

from .configuration import automation_enabled
from .errors import AutomationError
from .events import signal_business_event
from .schema import ABANDONED_CART_DEFAULT_HOURS, abandoned_cart_threshold_hours, event_trigger_entries


AIRCALL_LINK_DOCTYPES = {"Contact", "Lead", "Opportunity", "Customer"}
TERMINAL_CALL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled"}
EMAIL_DELIVERY_TOPICS = {
	"Bounced": "email.hard_bounced",
	"Soft-Bounced": "email.soft_bounced",
	"Clicked": "email.clicked",
	"Opened": "email.opened",
	"Recipient Unsubscribed": "email.unsubscribed",
}
TRACKING_EVENT_TOPICS = {
	"Opened": "email.opened",
	"Clicked": "email.clicked",
	"Bounced": "email.hard_bounced",
	"Soft-Bounced": "email.soft_bounced",
	"Unsubscribed": "email.unsubscribed",
}

_DELIVERY_REPORT_SENDERS = ("mailer-daemon", "postmaster")
_HARD_BOUNCE_SUBJECTS = (
	"delivery status notification (failure)",
	"delivery failure",
	"mail delivery failed",
	"returned mail",
	"undeliverable",
)
_SOFT_BOUNCE_SUBJECTS = (
	"delivery status notification (delay)",
	"delivery delayed",
	"delayed delivery",
	"still trying to deliver",
)


def _runtime_ready() -> bool:
	try:
		return bool(
			automation_enabled()
			and frappe.db.table_exists("Automation Workflow")
			and frappe.db.table_exists("Automation Timer")
		)
	except Exception:
		return False


def _signal(topic: str, record_doctype: str, record_name: str, payload: dict, occurrence: str) -> None:
	try:
		if not record_doctype or not record_name or not _runtime_ready():
			return
		signal_business_event(
			topic,
			payload,
			record_doctype=record_doctype,
			record_name=record_name,
			idempotency_key=occurrence,
		)
	except Exception:
		# A CRM call, login, list subscription, or order must remain successful
		# even if automation is temporarily unavailable.
		frappe.log_error(
			title=f"Workflow business event failed: {topic}",
			message=frappe.get_traceback(),
		)


def capture_aircall_inbound_call(doc, method=None) -> None:
	"""Emit one event when an inbound Aircall Call Log becomes terminal."""
	if doc.doctype != "Call Log" or doc.get("medium") != "Aircall" or doc.get("type") != "Incoming":
		return
	if doc.get("status") not in TERMINAL_CALL_STATUSES:
		return
	previous = doc.get_doc_before_save()
	if previous and previous.get("status") in TERMINAL_CALL_STATUSES:
		return

	payload = {
		"event_id": f"aircall:{doc.get('id') or doc.name}:inbound",
		"aircall_call_id": doc.get("id"),
		"call_log": doc.name,
		"phone_number": doc.get("from"),
		"outcome": doc.get("status"),
		"duration": doc.get("duration"),
	}
	links = {
		(row.get("link_doctype"), row.get("link_name"))
		for row in (doc.get("links") or [])
		if row.get("link_doctype") in AIRCALL_LINK_DOCTYPES and row.get("link_name")
	}
	if doc.get("customer"):
		links.add(("Customer", doc.get("customer")))
	try:
		from aircall_integration.aircall_integration.call_context import get_contacts_matching_number

		for contact in get_contacts_matching_number(doc.get("from")):
			links.add(("Contact", contact))
	except ImportError:
		pass
	except Exception:
		frappe.log_error(title="Aircall Contact workflow matching failed", message=frappe.get_traceback())
	for doctype, name in sorted(links):
		_signal("crm.call.inbound", doctype, name, payload, payload["event_id"])


def capture_lead_qualified(doc, method=None) -> None:
	if doc.doctype != "Lead" or doc.get("qualification_status") != "Qualified":
		return
	previous = doc.get_doc_before_save()
	if previous and previous.get("qualification_status") == "Qualified":
		return
	payload = {
		"event_id": f"lead:{doc.name}:qualified:{doc.get('modified')}",
		"qualification_status": "Qualified",
	}
	_signal("crm.lead.qualified", "Lead", doc.name, payload, payload["event_id"])


def capture_email_group_membership(doc, method=None) -> None:
	"""Resolve Email Group membership to the supported enrolled CRM records."""
	if doc.doctype != "Email Group Member" or cint(doc.get("unsubscribed")):
		return
	previous = doc.get_doc_before_save()
	if previous and not cint(previous.get("unsubscribed")):
		return
	email = str(doc.get("email") or "").strip()
	if not email:
		return
	payload = {
		"event_id": f"email-group:{doc.name}:{doc.get('modified')}",
		"list_name": doc.get("email_group"),
		"email": email,
	}
	try:
		for doctype in ("Contact", "Lead"):
			for name in frappe.get_all(doctype, filters={"email_id": email}, pluck="name", limit=100):
				_signal("crm.contact.list.joined", doctype, name, payload, payload["event_id"])
	except Exception:
		frappe.log_error(title="Email Group workflow matching failed", message=frappe.get_traceback())


def capture_sales_order_created(doc, method=None) -> None:
	if doc.doctype != "Sales Order" or not doc.get("customer"):
		return
	quotation_names = sorted(
		{row.get("prevdoc_docname") for row in (doc.get("items") or []) if row.get("prevdoc_docname")}
	)
	try:
		portal_order = bool(
			quotation_names
			and frappe.db.exists(
				"Quotation",
				{
					"name": ["in", quotation_names],
					"quotation_to": "Customer",
					"order_type": "Shopping Cart",
				},
			)
		)
	except Exception:
		portal_order = False
		frappe.log_error(title="Sales Order workflow source lookup failed", message=frappe.get_traceback())
	payload = {
		"event_id": f"sales-order:{doc.name}:created",
		"sales_order": doc.name,
		"order_type": doc.get("order_type"),
		"source": "Customer Portal" if portal_order else "ERPNext",
		"quotation": quotation_names[0] if len(quotation_names) == 1 else None,
	}
	_signal("commerce.order.created", "Customer", doc.get("customer"), payload, payload["event_id"])


def capture_customer_portal_login(login_manager=None) -> None:
	"""Emit a Customer event once Frappe has created a website-user session."""
	if not login_manager or getattr(login_manager, "user_type", None) != "Website User":
		return
	try:
		from customer_portal.utils.portal import get_current_customer_name

		customer = get_current_customer_name()
	except (ImportError, frappe.PermissionError, frappe.DoesNotExistError):
		return
	except Exception:
		frappe.log_error(title="Customer Portal workflow login event failed", message=frappe.get_traceback())
		return
	if not customer:
		return
	session_id = str(getattr(frappe.session, "sid", "") or frappe.generate_hash(length=20))
	payload = {
		"event_id": f"customer-portal:{session_id}",
		"portal": "Customer Portal",
	}
	_signal("commerce.store.login", "Customer", customer, payload, payload["event_id"])


def capture_web_form_submission(doc, method=None) -> None:
	"""Emit after the authoritative Web Form save created or updated its target."""
	if not getattr(frappe.flags, "in_web_form", False):
		return
	form_name = str(getattr(frappe.form_dict, "web_form", "") or "").strip()
	if not form_name:
		return
	payload = {
		"event_id": f"web-form:{form_name}:{doc.doctype}:{doc.name}:{doc.get('modified')}",
		"form_name": form_name,
		"submission_type": "updated" if doc.get_doc_before_save() else "created",
	}
	_signal("crm.form.submitted", doc.doctype, doc.name, payload, payload["event_id"])


def capture_communication_event(doc, method=None) -> None:
	"""Normalize inbound replies and provider-updated email delivery statuses."""
	if method == "after_insert":
		_prepare_outbound_communication_tracking(doc)
	if doc.doctype != "Communication":
		return
	if doc.get("sent_or_received") == "Received" and _capture_delivery_report(doc):
		return
	if not doc.get("reference_doctype") or not doc.get("reference_name"):
		return
	previous = doc.get_doc_before_save()
	if doc.get("sent_or_received") == "Received" and not (previous and previous.get("sent_or_received") == "Received"):
		payload = {
			"event_id": f"communication:{doc.name}:received",
			"communication": doc.name,
			"communication_medium": doc.get("communication_medium"),
			"sender": doc.get("sender"),
		}
		_signal("communication.responded", doc.get("reference_doctype"), doc.get("reference_name"), payload, payload["event_id"])
	delivery_status = str(doc.get("delivery_status") or "")
	topic = EMAIL_DELIVERY_TOPICS.get(delivery_status)
	if not topic:
		return
	if previous and previous.get("delivery_status") == delivery_status:
		return
	email_queue = frappe.db.get_value("Email Queue", {"communication": doc.name}, "name")
	payload = {
		"event_id": f"communication:{doc.name}:delivery:{delivery_status}",
		"communication": doc.name,
		"email_queue": email_queue,
		"email_id": doc.get("message_id") or doc.name,
		"email_type": doc.get("subject"),
		"sender": doc.get("sender"),
	}
	_signal(topic, doc.get("reference_doctype"), doc.get("reference_name"), payload, payload["event_id"])


def _delivery_report_status(doc) -> str | None:
	"""Classify provider-generated delivery reports without treating them as replies."""
	sender = str(doc.get("sender") or "").strip().lower()
	subject = str(doc.get("subject") or "").strip().lower()
	if not any(marker in sender for marker in _DELIVERY_REPORT_SENDERS):
		return None
	if any(marker in subject for marker in _SOFT_BOUNCE_SUBJECTS):
		return "Soft-Bounced"
	if any(marker in subject for marker in _HARD_BOUNCE_SUBJECTS):
		return "Bounced"
	return None


def _capture_delivery_report(doc) -> bool:
	"""Apply an inbound DSN to its exact outbound Communication and emit once.

	Frappe imports Gmail/SMTP delivery reports as received Communications. On
	this site their ``in_reply_to`` value is the original Communication name.
	Only that exact correlation (or an exact Message-ID fallback) is accepted;
	the enrolled record is never guessed from an email address.
	"""
	delivery_status = _delivery_report_status(doc)
	if not delivery_status:
		return False
	correlation = str(doc.get("in_reply_to") or "").strip().strip("<>")
	if not correlation:
		return True
	fields = [
		"name",
		"sent_or_received",
		"reference_doctype",
		"reference_name",
		"delivery_status",
		"message_id",
		"subject",
		"sender",
	]
	outbound = frappe.db.get_value("Communication", correlation, fields, as_dict=True)
	if not outbound:
		outbound = frappe.db.get_value(
			"Communication",
			{"message_id": ["in", [correlation, f"<{correlation}>"]]},
			fields,
			as_dict=True,
		)
	if not outbound or outbound.sent_or_received != "Sent":
		return True
	if outbound.delivery_status == delivery_status:
		return True

	frappe.db.set_value(
		"Communication",
		outbound.name,
		"delivery_status",
		delivery_status,
		update_modified=False,
	)
	email_queue = frappe.db.get_value("Email Queue", {"communication": outbound.name}, "name")
	payload = {
		"event_id": f"communication:{outbound.name}:delivery:{delivery_status}",
		"communication": outbound.name,
		"delivery_report": doc.name,
		"email_queue": email_queue,
		"email_id": outbound.message_id or outbound.name,
		"email_type": outbound.subject,
		"sender": outbound.sender,
	}
	_signal(
		EMAIL_DELIVERY_TOPICS[delivery_status],
		outbound.reference_doctype,
		outbound.reference_name,
		payload,
		payload["event_id"],
	)
	return True


def _prepare_outbound_communication_tracking(doc) -> None:
	"""Track links in ordinary Frappe emails as well as workflow email actions.

	Frappe creates and inserts the Communication before ``Communication.send_email``
	builds its Email Queue row. Updating both this in-memory document and the stored
	content here therefore makes the signed links authoritative for the actual MIME
	body without overriding Frappe's email API or guessing a related CRM record.
	"""
	if (
		doc.doctype != "Communication"
		or doc.get("communication_medium") != "Email"
		or doc.get("sent_or_received") != "Sent"
		or not doc.get("reference_doctype")
		or not doc.get("reference_name")
	):
		return
	from .tracking import decorate_workflow_email_links, ensure_workflow_open_tracking

	original = str(doc.get("content") or "")
	content, _tracked = decorate_workflow_email_links(original, doc.name)
	# A visual Email Template is sent with raw_html=True. Frappe intentionally
	# skips its standard footer in that mode, so the Communication hook must put
	# the recipient-specific pixel placeholder into manually composed visual
	# emails as well as the workflow action path.
	if doc.get("email_template") and bool(
		frappe.get_cached_value("Email Template", doc.get("email_template"), "use_html")
	):
		content = ensure_workflow_open_tracking(content)
	if content == original:
		return
	doc.content = content
	frappe.db.set_value("Communication", doc.name, "content", content, update_modified=False)


def capture_email_tracking_event(doc, method=None) -> None:
	"""Bridge installed Reach/outreach tracking rows into workflow events."""
	if doc.doctype != "Email Tracking Event":
		return
	topic = TRACKING_EVENT_TOPICS.get(str(doc.get("event_type") or ""))
	if not topic:
		return
	# The signed FinbyzReach preference adapter emits the authoritative
	# topic-aware event after it has compared the saved preferences. The generic
	# tracking row is evidence for campaign history, not a second workflow event.
	if topic == "email.unsubscribed" and doc.get("marketing_campaign_recipient"):
		return
	communication_name = str(doc.get("communication") or "").strip()
	communication = (
		frappe.db.get_value(
			"Communication",
			communication_name,
			[
				"reference_doctype",
				"reference_name",
				"subject",
				"sender",
				"message_id",
			],
			as_dict=True,
		)
		if communication_name
		else None
	)
	record_doctype = communication.reference_doctype if communication else None
	record_name = communication.reference_name if communication else None
	if not record_name and doc.get("lead"):
		record_doctype, record_name = "Lead", doc.get("lead")
	if not record_name and doc.get("contact"):
		record_doctype, record_name = "Contact", doc.get("contact")
	if not record_doctype or not record_name:
		return
	email_queue = (
		frappe.db.get_value("Email Queue", {"communication": communication_name}, "name")
		if communication_name
		else None
	)
	event_label = str(doc.get("event_type") or "")
	payload = {
		"event_id": f"communication:{communication_name}:delivery:{event_label}"
		if communication_name
		else f"email-tracking:{doc.name}",
		"tracking_event": doc.name,
		"communication": communication_name or None,
		"email_queue": email_queue,
		"email_id": communication.message_id if communication else doc.name,
		"email_type": communication.subject if communication else None,
		"sender": communication.sender if communication else None,
		"link_url": doc.get("url"),
	}
	_signal(topic, record_doctype, record_name, payload, payload["event_id"])


def capture_email_unsubscribe(doc, method=None) -> None:
	if doc.doctype != "Email Unsubscribe" or not doc.get("reference_doctype") or not doc.get("reference_name"):
		return
	payload = {
		"event_id": f"email-unsubscribe:{doc.name}",
		"email_id": doc.get("email"),
		"email_type": "global" if cint(doc.get("global_unsubscribe")) else "record",
	}
	_signal("email.unsubscribed", doc.get("reference_doctype"), doc.get("reference_name"), payload, payload["event_id"])


@frappe.whitelist(allow_guest=True, methods=["GET"])
def unsubscribe_workflow_email(doctype: str, name: str, email: str) -> None:
	"""Globally opt out a non-Lead workflow recipient and retain exact record identity.

	Frappe's signed unsubscribe URL supplies the originating DocType, record and
	recipient. Keeping that reference on the global Email Unsubscribe row lets the
	doc_event hook emit one exact workflow event without guessing relationships
	from an email address.
	"""
	from frappe.utils.verified_command import verify_request

	if not frappe.in_test and not verify_request():
		return
	record_doctype = str(doctype or "").strip()
	record_name = str(name or "").strip()
	recipient = str(email or "").strip().lower()
	if record_doctype == "Lead":
		frappe.throw(_("Lead workflow emails use Lead-specific unsubscribe handling."), frappe.ValidationError)
	if not record_doctype or not record_name or not frappe.db.exists(record_doctype, record_name):
		frappe.throw(_("The email reference is no longer available."), frappe.DoesNotExistError)
	if not validate_email_address(recipient, throw=False):
		frappe.throw(_("The unsubscribe email address is invalid."), frappe.ValidationError)

	try:
		frappe.get_doc(
			{
				"doctype": "Email Unsubscribe",
				"email": recipient,
				"reference_doctype": record_doctype,
				"reference_name": record_name,
				"global_unsubscribe": 1,
			}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		frappe.db.rollback()
	else:
		frappe.db.commit()

	frappe.respond_as_web_page(
		_("Unsubscribed"),
		_("Global email unsubscribe saved for {0}.").format(recipient),
		indicator_color="green",
	)


def _reach_unsubscribe_topic_rows(lead_name: str | None) -> dict[str, str]:
	"""Return topic -> child-row identity without depending on Reach internals."""
	if not lead_name or not frappe.db.table_exists("Unsubscribe Topic Multi Select"):
		return {}
	return {
		str(row.subscription_topic): str(row.name)
		for row in frappe.get_all(
			"Unsubscribe Topic Multi Select",
			filters={
				"parent": lead_name,
				"parenttype": "Lead",
				"parentfield": "custom_unsubscribe_topics",
			},
			fields=["name", "subscription_topic"],
			limit_page_length=0,
		)
		if row.subscription_topic
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def update_reach_subscription_preferences(*args, **kwargs):
	"""Keep Reach authoritative and emit only newly saved topic opt-outs."""
	try:
		from finbyzreach.email_marketing import update_subscription_preferences
	except ImportError:
		frappe.throw("Finbyz Reach is not installed.", frappe.ValidationError)

	campaign_recipient = kwargs.get("campaign_recipient") or (args[0] if args else None)
	email = kwargs.get("email") or (args[1] if len(args) > 1 else None)
	lead_name = (
		frappe.db.get_value("Email Campaign", campaign_recipient, "recipient")
		if campaign_recipient
		else None
	)
	before = _reach_unsubscribe_topic_rows(lead_name)
	result = update_subscription_preferences(*args, **kwargs)
	after = _reach_unsubscribe_topic_rows(lead_name)
	for topic in sorted(set(after) - set(before)):
		row_name = after[topic]
		payload = {
			"event_id": f"reach-topic-unsubscribe:{row_name}",
			"email_id": email,
			"email_type": "topic",
			"subscription_topic": topic,
		}
		_signal("email.unsubscribed", "Lead", lead_name, payload, payload["event_id"])
	return result


def _active_abandoned_cart_thresholds() -> list[int]:
	"""Return every published idle threshold while retaining the legacy default."""
	thresholds = {ABANDONED_CART_DEFAULT_HOURS}
	rows = frappe.get_all(
		"Automation Trigger Subscription",
		filters={"primary_doctype": "Customer", "event_type": "EVENT", "active": 1},
		fields=["config_json"],
		limit_page_length=0,
	)
	for row in rows:
		try:
			config = json.loads(row.config_json or "{}")
		except (TypeError, ValueError):
			continue
		if not isinstance(config, dict):
			continue
		entries = event_trigger_entries(config, 2 if isinstance(config.get("events"), list) else 1)
		for entry in entries:
			if str(entry.get("event_topic") or "") != "commerce.order.abandoned":
				continue
			try:
				thresholds.add(abandoned_cart_threshold_hours(entry))
			except AutomationError:
				# Published validation prevents this for new versions. Ignore malformed
				# legacy rows here so one bad subscription cannot stop the scheduler.
				continue
	return sorted(thresholds)


def capture_abandoned_shopping_carts() -> int:
	"""Emit Customer Portal carts at each active workflow's configured idle threshold."""
	if not _runtime_ready() or not frappe.db.table_exists("Quotation"):
		return 0
	emitted = 0
	page_size = 500
	for threshold_hours in _active_abandoned_cart_thresholds():
		cutoff = add_to_date(now_datetime(), hours=-threshold_hours)
		window_start = add_to_date(cutoff, hours=-24)
		start = 0
		while True:
			rows = frappe.get_all(
				"Quotation",
				# Keep a bounded recovery window so a short scheduler outage does not
				# lose the transition, while old historical carts are never replayed.
				filters={
					"docstatus": 0,
					"order_type": "Shopping Cart",
					"quotation_to": "Customer",
					"modified": ["between", [window_start, cutoff]],
				},
				fields=["name", "quotation_to", "party_name", "contact_person", "modified"],
				order_by="modified desc, name desc",
				start=start,
				limit=page_size,
			)
			for row in rows:
				if frappe.db.exists("Sales Order Item", {"prevdoc_docname": row.name}):
					continue
				legacy_default = threshold_hours == ABANDONED_CART_DEFAULT_HOURS
				payload = {
					"event_id": f"shopping-cart:{row.name}:abandoned" if legacy_default else f"shopping-cart:{row.name}:abandoned:{threshold_hours}h",
					"store_id": "ERPNext Shopping Cart",
					"cart_id": row.name,
					"abandoned_after_hours": threshold_hours,
				}
				# The installed Customer Portal resolves carts through a Customer.
				# Never guess a Lead/Contact relationship from an ERP quotation.
				if row.quotation_to == "Customer" and row.party_name:
					_signal("commerce.order.abandoned", "Customer", row.party_name, payload, payload["event_id"])
					emitted += 1
			if len(rows) < page_size:
				break
			start += page_size
	return emitted
