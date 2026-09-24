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


def require_permission(
    doctype: str,
    ptype: str = "read",
    *,
    doc=None,
    label: str | None = None,
) -> None:
    """Require an effective Frappe permission for the current user."""
    if not isinstance(doctype, str) or not doctype.strip():
        raise frappe.ValidationError(_("DocType must be a non-empty string."))

    doctype = doctype.strip()
    if not frappe.has_permission(doctype, ptype, doc=doc):
        raise frappe.PermissionError(
            _("You do not have {0} permission for {1}.").format(ptype, label or doctype)
        )


# Who the Copilot exists for. Anything outside this set should not see the launcher,
# should not have the panel mounted, and should be refused by every endpoint — not
# shown an error, because a feature they were never given is not a failure.
COPILOT_ROLES = {"Copilot User", "Copilot Admin", "System Manager"}
ADMIN_ROLES = {"Copilot Admin", "System Manager"}


def has_copilot(user: str | None = None) -> bool:
    """Whether this user may use the Copilot at all."""
    user = user or frappe.session.user
    if user == "Guest":
        return False
    return bool(set(frappe.get_roles(user)) & COPILOT_ROLES)


def is_copilot_admin(user: str | None = None) -> bool:
    return bool(set(frappe.get_roles(user or frappe.session.user)) & ADMIN_ROLES)


def require_copilot() -> None:
    """Gate every Copilot endpoint.

    Raises rather than returning empty, so a user without the role cannot reach the
    chat by calling the API directly. The panel never calls these for them, because
    boot tells it not to mount at all.
    """
    if not has_copilot():
        raise frappe.PermissionError(
            _("The Copilot is not enabled for your account.")
        )
