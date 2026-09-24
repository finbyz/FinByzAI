# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``find_doctypes`` AI Tool."""

import frappe

from finbyzai.copilot import access

from finbyzai.copilot.registry import tool

FIND_LIMIT = 50

@tool(
    "find_doctypes",
    """Find the exact DocType name for something the user described. Always call this
    before any other tool when you are not certain a DocType name is exactly right.
    Returns only DocTypes the current user may read.""",
    tags=["discovery"],
    label="Finding relevant DocTypes",
)
def find_doctypes_tool(search: str, module: str | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), FIND_LIMIT))
    filters = {"name": ("like", f"%{search.strip()}%")}
    if module:
        filters["module"] = module

    rows = frappe.get_all(
        "DocType",
        filters=filters,
        fields=["name", "module", "istable", "issingle", "is_submittable"],
        limit=limit * 5,
    )
    # Shortest name first: "Sales Invoice" should beat "Sales Invoice Advance".
    rows.sort(key=lambda r: (bool(r.istable), len(r.name or "")))

    out = []
    for row in rows:
        if not frappe.has_permission(row.name, "read"):
            continue
        entry = {"name": row.name, "module": row.module}
        if row.istable:
            entry["child_table"] = True
        if row.issingle:
            entry["single"] = True
        if row.is_submittable:
            entry["submittable"] = True
        out.append(entry)
        if len(out) >= limit:
            break

    return {"doctypes": out, "count": len(out)}
