from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from .configuration import automation_enabled
from .events import signal_business_event


AIRCALL_LINK_DOCTYPES = {"Contact", "Lead", "Opportunity", "Customer"}
TERMINAL_CALL_STATUSES = {"Completed", "Failed", "Busy", "No Answer", "Cancelled"}
EMAIL_DELIVERY_TOPICS = {
	"Bounced": "email.hard_bounced",
	"Soft-Bounced": "email.soft_bounced",
	"Clicked": "email.clicked",
	"Opened": "email.opened",
	"Marked As Spam": "email.complained",
	"Recipient Unsubscribed": "email.unsubscribed",
}


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
	if doc.doctype != "Communication" or not doc.get("reference_doctype") or not doc.get("reference_name"):
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


def capture_email_unsubscribe(doc, method=None) -> None:
	if doc.doctype != "Email Unsubscribe" or not doc.get("reference_doctype") or not doc.get("reference_name"):
		return
	payload = {
		"event_id": f"email-unsubscribe:{doc.name}",
		"email_id": doc.get("email"),
		"email_type": "global" if cint(doc.get("global_unsubscribe")) else "record",
	}
	_signal("email.unsubscribed", doc.get("reference_doctype"), doc.get("reference_name"), payload, payload["event_id"])


def capture_abandoned_shopping_carts() -> int:
	"""Emit the product default: an unchanged draft Shopping Cart for 24 hours."""
	if not _runtime_ready() or not frappe.db.table_exists("Quotation"):
		return 0
	cutoff = add_to_date(now_datetime(), hours=-24)
	rows = frappe.get_all(
		"Quotation",
		filters={"docstatus": 0, "order_type": "Shopping Cart", "modified": ["<=", cutoff]},
		fields=["name", "quotation_to", "party_name", "contact_person", "modified"],
		limit=500,
	)
	emitted = 0
	for row in rows:
		if frappe.db.exists("Sales Order Item", {"prevdoc_docname": row.name}):
			continue
		payload = {
			"event_id": f"shopping-cart:{row.name}:abandoned",
			"store_id": "ERPNext Shopping Cart",
			"cart_id": row.name,
			"abandoned_after_hours": 24,
		}
		links = set()
		if row.quotation_to in {"Customer", "Lead"} and row.party_name:
			links.add((row.quotation_to, row.party_name))
		if row.contact_person:
			links.add(("Contact", row.contact_person))
		for doctype, name in sorted(links):
			_signal("commerce.order.abandoned", doctype, name, payload, payload["event_id"])
			emitted += 1
	return emitted
