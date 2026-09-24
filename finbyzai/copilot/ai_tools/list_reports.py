# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``list_reports`` AI Tool."""

import re

import frappe

from finbyzai.copilot import access, blocks

from finbyzai.copilot.registry import tool

def _allowed_role_map() -> dict:
    """{report: allowed roles} for every role-restricted Report.

    Mirrors `Report.is_permitted()` — including a Custom Role, which *replaces* the
    report's own `Has Role` rows — but in two queries instead of loading every Report
    document. 302 of this site's 325 reports are role-restricted, so skipping this
    check makes `list_reports` offer reports the user cannot actually run.
    """
    allowed: dict = {}
    for row in frappe.get_all(
        "Has Role", filters={"parenttype": "Report"}, fields=["parent", "role"], limit_page_length=0
    ):
        allowed.setdefault(row.parent, set()).add(row.role)

    custom = {
        row.name: row.report
        for row in frappe.get_all(
            "Custom Role", filters={"report": ("is", "set")}, fields=["name", "report"], limit_page_length=0
        )
    }
    if custom:
        override: dict = {}
        for row in frappe.get_all(
            "Has Role",
            filters={"parenttype": "Custom Role", "parent": ("in", list(custom))},
            fields=["parent", "role"],
            limit_page_length=0,
        ):
            override.setdefault(custom[row.parent], set()).add(row.role)
        allowed.update(override)
    return allowed

def _report_permitted(name: str, ref_doctype: str | None, allowed: dict, user_roles: set) -> bool:
    """Both gates Frappe applies when a report is actually run."""
    if ref_doctype:
        try:
            if not frappe.has_permission(ref_doctype, "report") or not frappe.has_permission(
                ref_doctype, "read"
            ):
                return False
        except Exception:
            # The report points at a DocType that no longer exists (this site has one:
            # "Price Graph" -> "Purchase Price"). Unusable, and it must not abort the list.
            return False
    roles = allowed.get(name)
    return not roles or bool(roles & user_roles)

@tool(
    "list_reports",
    """Find existing reports. ALWAYS try this before writing your own query: this site
    has 222 ready-made, permission-aware reports and one of them usually answers the
    question exactly. Search by words in the report name, or list every report for a
    DocType with `ref_doctype`.""",
    tags=["reports"],
    label="Finding Reports",
)
def list_reports_tool(
    search: str | None = None,
    ref_doctype: str | None = None,
    module: str | None = None,
    limit: int = 25,
) -> dict:
    if ref_doctype:
        access.require_permission(ref_doctype, "report")
        access.require_permission(ref_doctype, "read")
    limit = max(1, min(int(limit or 25), 100))
    filters = {"disabled": 0}
    if search:
        filters["name"] = ("like", f"%{search.strip()}%")
    if ref_doctype:
        filters["ref_doctype"] = ref_doctype
    if module:
        filters["module"] = module

    rows = frappe.get_all(
        "Report",
        filters=filters,
        fields=["name", "report_type", "ref_doctype", "module"],
        limit=limit * 5,
    )
    rows.sort(key=lambda r: len(r.name or ""))

    allowed = _allowed_role_map()
    user_roles = set(frappe.get_roles())

    out = []
    for row in rows:
        if not _report_permitted(row.name, row.ref_doctype, allowed, user_roles):
            continue
        out.append(
            {
                "report": row.name,
                "type": row.report_type,
                "doctype": row.ref_doctype,
                "module": row.module,
            }
        )
        if len(out) >= limit:
            break

    return {"reports": out, "count": len(out)}
