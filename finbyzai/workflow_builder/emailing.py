from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

import frappe
from frappe import _

from .errors import AutomationError
from .schema import resolve_value


def template_reference_doctype(template) -> str:
	meta = frappe.get_meta("Email Template")
	return str(
		(template.get("custom_reference_doctype") if meta.has_field("custom_reference_doctype") else "")
		or (template.get("reference_doctype") if meta.has_field("reference_doctype") else "")
		or ""
	).strip()


def email_template_summary(template, primary_doctype: str | None = None) -> dict:
	if not template:
		raise AutomationError(_("Choose an Email Template."))
	if hasattr(template, "enabled") and not bool(template.enabled):
		raise AutomationError(_("The selected Email Template is disabled."))
	reference_doctype = template_reference_doctype(template)
	if reference_doctype and primary_doctype and reference_doctype != primary_doctype:
		raise AutomationError(
			_("Email Template {0} is designed for {1}, not {2}.").format(
				template.name, reference_doctype, primary_doctype
			)
		)
	meta = frappe.get_meta("Email Template")
	mode = str(template.get("custom_builder_mode") if meta.has_field("custom_builder_mode") else "")
	if not mode:
		mode = "Raw HTML" if template.use_html else "Standard"
	return {
		"name": template.name,
		"subject": template.subject or "",
		"mode": mode,
		"preheader": template.get("custom_preheader_text") if meta.has_field("custom_preheader_text") else "",
		"reference_doctype": reference_doctype,
		"modified": str(template.modified),
		"builder_route": f"/builder?template={quote(template.name, safe='')}" if mode == "Visual" else None,
		"desk_route": f"/app/email-template/{quote(template.name, safe='')}",
	}


def get_email_template(template_name: str, primary_doctype: str | None = None, *, check_permission: bool = True):
	template_name = str(template_name or "").strip()
	if not template_name or not frappe.db.exists("Email Template", template_name):
		raise AutomationError(_("Choose an existing Email Template."))
	template = frappe.get_doc("Email Template", template_name)
	if check_permission:
		template.check_permission("read")
	email_template_summary(template, primary_doctype)
	return template


def _template_snapshot(template, subject_override: str | None = None) -> dict:
	if "finbyzreach" in frappe.get_installed_apps():
		from finbyzreach.email_template_builder.services import get_campaign_snapshot

		snapshot = dict(
			get_campaign_snapshot(template.name, subject_override=subject_override, check_permission=False)
		)
		# Rendering must follow the Email Template's actual content field. Visual and
		# manually-authored HTML templates both live in response_html/use_html.
		snapshot["raw_html"] = bool(template.use_html)
		return snapshot
	subject = subject_override or template.subject
	html = template.response_html if template.use_html else template.response
	if not str(subject or "").strip() or not str(html or "").strip():
		raise AutomationError(_("The selected Email Template must contain a subject and email content."))
	return {
		"template": template.name,
		"mode": "Raw HTML" if template.use_html else "Standard",
		"reference_doctype": template_reference_doctype(template),
		"subject": subject,
		"preheader": "",
		"html": html,
		"raw_html": bool(template.use_html),
		"content_hash": hashlib.sha256(f"{subject}\n{html}".encode()).hexdigest(),
	}


def _render_snapshot(snapshot: dict, record) -> dict:
	if "finbyzreach" in frappe.get_installed_apps():
		from finbyzreach.email_template_builder.services import render_campaign_snapshot

		# frappe._dict returns None for unknown attributes, which makes a plain
		# hasattr(record, "as_dict") check look true in older builder services.
		# Normalize preview-only dictionaries so the shared renderer takes its dict path.
		render_record = record
		if isinstance(record, dict) and not callable(getattr(record, "as_dict", None)):
			render_record = dict(record)
		return dict(render_campaign_snapshot(snapshot["subject"], snapshot["html"], render_record))
	values = frappe._dict(record.as_dict() if hasattr(record, "as_dict") else dict(record or {}))
	context = frappe._dict({**values, "doc": values})
	return {
		"subject": frappe.render_template(snapshot["subject"], context),
		"html": frappe.render_template(snapshot["html"], context),
	}


def resolve_email_content(config: dict, *, record, outputs: dict[str, Any], primary_doctype: str) -> dict:
	content_mode = str(config.get("content_mode") or ("template" if config.get("email_template") else "inline"))
	if content_mode == "template":
		template = get_email_template(config.get("email_template"), primary_doctype, check_permission=False)
		override = resolve_value(config.get("subject_override"), record=record, outputs=outputs)
		snapshot = _template_snapshot(template, str(override or "").strip() or None)
		rendered = _render_snapshot(snapshot, record)
		return {
			"content_mode": "template",
			"email_template": template.name,
			"subject": str(rendered.get("subject") or "")[:998],
			"message": str(rendered.get("html") or ""),
			"preheader": snapshot.get("preheader") or "",
			"raw_html": bool(snapshot.get("raw_html")),
			"content_hash": snapshot.get("content_hash"),
		}
	if content_mode != "inline":
		raise AutomationError(_("Choose Email Template or quick inline content."))
	return {
		"content_mode": "inline",
		"email_template": None,
		"subject": str(resolve_value(config.get("subject"), record=record, outputs=outputs) or "")[:998],
		"message": str(resolve_value(config.get("message"), record=record, outputs=outputs) or ""),
		"preheader": "",
		"raw_html": bool(config.get("raw_html")),
		"content_hash": None,
	}
