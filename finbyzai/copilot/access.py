# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Permission checks for configurable Copilot resources."""

import frappe
from frappe import _


def require_read(doctype: str, name, *, label: str | None = None) -> str | None:
    """Return a normalized Link value after enforcing record read permission."""
    if name in (None, ""):
        return None
    if not isinstance(name, str):
        frappe.throw(
            _("{0} must be a record name.").format(label or doctype),
            title=_("Invalid selection"),
        )

    name = name.strip()
    if not name:
        return None

    doc = frappe.get_doc(doctype, name)
    if not frappe.has_permission(doctype, "read", doc=doc):
        raise frappe.PermissionError(
            _("You do not have permission to use {0} {1}.").format(label or doctype, name)
        )
    return doc.name


def can_read(doctype: str, name) -> bool:
    """Return whether the current user can read a configured Link value."""
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        doc = frappe.get_doc(doctype, name.strip())
    except frappe.DoesNotExistError:
        return False
    return bool(frappe.has_permission(doctype, "read", doc=doc))
