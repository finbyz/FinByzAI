from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.database.utils import commit_after_response
from frappe.utils import get_url, now_datetime
from frappe.utils.verified_command import get_signed_params, verify_request

from .events import signal_business_event


WORKFLOW_CLICK_METHOD = "finbyzai.workflow_builder.tracking.track_workflow_email_click"
EMAIL_OPEN_PLACEHOLDER = "<!--email_open_check-->"
_SKIPPED_LINK_MARKERS = (
	WORKFLOW_CLICK_METHOD,
	"finbyzreach.email_marketing.track_marketing_click",
	"megasol_customisation.megasol_customisation.ai_outreach.track_click",
	"/manage_subscriptions",
	"unsubscribe",
	"/view_email",
)


def ensure_workflow_open_tracking(html: str) -> str:
	"""Add Frappe's per-recipient open placeholder to a raw HTML email.

	Frappe's standard email wrapper already contains this placeholder, but
	``raw_html=True`` deliberately skips that wrapper. Visual Email Templates
	must therefore carry the placeholder themselves so Email Queue can replace it
	with the signed Communication tracking pixel during recipient rendering.
	"""
	content = str(html or "")
	if EMAIL_OPEN_PLACEHOLDER in content:
		return content
	pixel = (
		'<div class="email-pixel" aria-hidden="true" '
		'style="display:none!important;max-height:0;overflow:hidden">'
		f"{EMAIL_OPEN_PLACEHOLDER}</div>"
	)
	body_end = content.lower().rfind("</body>")
	if body_end >= 0:
		return f"{content[:body_end]}{pixel}{content[body_end:]}"
	return f"{content}{pixel}"


def _is_trackable_url(value: str) -> bool:
	url = str(value or "").strip()
	if not url or any(marker in url for marker in _SKIPPED_LINK_MARKERS):
		return False
	parsed = urlsplit(url)
	return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def decorate_workflow_email_links(html: str, communication: str) -> tuple[str, int]:
	"""Wrap HTTP links with a signed, record-correlated workflow click URL."""
	soup = BeautifulSoup(str(html or ""), "html.parser")
	tracked = 0
	for index, link in enumerate(soup.find_all("a"), start=1):
		target = str(link.get("href") or "").strip()
		if not _is_trackable_url(target):
			continue
		params = get_signed_params(
			{
				"communication": communication,
				"link_id": str(index),
				"url": target,
			}
		)
		link["href"] = get_url(f"/api/method/{WORKFLOW_CLICK_METHOD}?{params}")
		tracked += 1
	return str(soup), tracked


def _communication_context(name: str) -> frappe._dict | None:
	if not name or not isinstance(name, str):
		return None
	return frappe.db.get_value(
		"Communication",
		name,
		[
			"name",
			"reference_doctype",
			"reference_name",
			"subject",
			"sender",
			"message_id",
			"sent_or_received",
			"read_by_recipient",
			"read_by_recipient_on",
		],
		as_dict=True,
	)


def _emit_tracking_event(
	topic: str,
	communication: frappe._dict,
	*,
	queue_name: str | None,
	event_id: str,
	url: str | None = None,
	link_id: str | None = None,
) -> None:
	if (
		not communication
		or communication.sent_or_received != "Sent"
		or not communication.reference_doctype
		or not communication.reference_name
	):
		return
	payload = {
		"event_id": event_id,
		"communication": communication.name,
		"email_queue": queue_name,
		"email_id": communication.message_id or communication.name,
		"email_type": communication.subject,
		"sender": communication.sender,
	}
	if url:
		payload["link_url"] = url
	if link_id:
		payload["link_id"] = link_id

	savepoint = "automation_email_tracking_event"
	frappe.db.savepoint(savepoint)
	try:
		signal_business_event(
			topic,
			payload,
			record_doctype=communication.reference_doctype,
			record_name=communication.reference_name,
			source_doctype="Email Queue" if queue_name else "Communication",
			source_name=queue_name or communication.name,
			idempotency_key=event_id,
		)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(
			title=f"Workflow email tracking event failed: {topic}",
			message=frappe.get_traceback(with_context=False),
		)
	else:
		frappe.db.release_savepoint(savepoint)


def _queue_for_communication(communication: str) -> str | None:
	return frappe.db.get_value("Email Queue", {"communication": communication}, "name")


@frappe.whitelist(allow_guest=True, methods=["GET"])
def track_workflow_email_click(communication=None, link_id=None, url=None):
	"""Record one signed workflow-email link click and redirect to its target."""
	target = str(url or "").strip()
	if not _is_trackable_url(target):
		frappe.local.response["http_status_code"] = 400
		return _("Invalid URL")
	if not frappe.in_test and not verify_request():
		frappe.local.response["http_status_code"] = 403
		return _("Invalid tracking signature")

	context = _communication_context(str(communication or ""))
	if context and context.sent_or_received == "Sent":
		now = now_datetime()
		was_read = bool(context.read_by_recipient)
		values = {
			"delivery_status": "Clicked",
			"read_by_recipient": 1,
		}
		if not context.read_by_recipient_on:
			values["read_by_recipient_on"] = now
		frappe.db.set_value("Communication", context.name, values, update_modified=False)
		queue_name = _queue_for_communication(context.name)
		if not was_read:
			_emit_tracking_event(
				"email.opened",
				context,
				queue_name=queue_name,
				event_id=f"communication:{context.name}:delivery:Opened",
			)
		normalized_link_id = str(link_id or "")[:40]
		url_key = normalized_link_id or hashlib.sha256(target.encode()).hexdigest()[:20]
		_emit_tracking_event(
			"email.clicked",
			context,
			queue_name=queue_name,
			event_id=f"communication:{context.name}:delivery:Clicked:{url_key}",
			url=target,
			link_id=normalized_link_id or None,
		)
		# Tracking endpoints are GET requests, which Frappe does not automatically
		# commit. Commit before redirect so the event and enrollment are durable.
		frappe.db.commit()

	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = target


def _mark_email_opened(name: str | None) -> None:
	context = _communication_context(str(name or ""))
	if not context or context.sent_or_received != "Sent" or context.read_by_recipient:
		return
	from frappe.core.doctype.communication.email import update_communication_as_read

	update_communication_as_read(context.name)
	queue_name = _queue_for_communication(context.name)
	_emit_tracking_event(
		"email.opened",
		context,
		queue_name=queue_name,
		event_id=f"communication:{context.name}:delivery:Opened",
	)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def mark_workflow_email_as_seen(name: str | None = None):
	"""Preserve Frappe's read pixel while emitting the durable opened event."""
	commit_after_response(lambda: _mark_email_opened(name))
	frappe.response.update(frappe.utils.get_imaginary_pixel_response())
