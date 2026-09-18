# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Ground-truth tools: resolve exact DocType and field names before acting.

Discover -> verify -> act. The model must never invent a DocType, fieldname or
record name; these two tools are how it finds out, and the system prompt requires
them before any read or write.
"""

import frappe

from finbyzai.copilot.registry import tool

# Layout-only fieldtypes: they carry no data and only waste context.
SKIP_FIELDTYPES = {
    "Section Break",
    "Column Break",
    "Tab Break",
    "HTML",
    "Button",
    "Image",
    "Fold",
    "Heading",
}

PERMISSION_TYPES = ("read", "write", "create", "delete", "submit", "cancel", "amend", "report")

FIND_LIMIT = 50
FIELD_LIMIT = 200


@tool(
    "find_doctypes",
    """Find the exact DocType name for something the user described. Always call this
    before any other tool when you are not certain a DocType name is exactly right.
    Returns only DocTypes the current user may read.""",
    tags=["discovery"],
    label="Finding relevant DocTypes",
)
def find_doctypes(search: str, module: str | None = None, limit: int = 20) -> dict:
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


@tool(
    "describe",
    """Inspect a DocType: its fields, your permissions on it, and — when `name` is a
    record — the actions available on that record. Call this before create/update so
    you use real fieldnames, and before run_action so you use a real action.""",
    tags=["discovery"],
    label="Reading DocType Meta",
)
def describe(doctype: str, name: str | None = None) -> dict:
    if not frappe.has_permission(doctype, "read"):
        raise frappe.PermissionError(f"No permission to read {doctype}")

    meta = frappe.get_meta(doctype)
    fields = []
    for df in meta.fields:
        if df.fieldtype in SKIP_FIELDTYPES:
            continue
        field = {"fieldname": df.fieldname, "label": df.label, "type": df.fieldtype}
        if df.options and df.fieldtype in ("Link", "Select", "Table", "Table MultiSelect", "Dynamic Link"):
            field["options"] = df.options
        if df.reqd:
            field["required"] = True
        if df.read_only:
            field["read_only"] = True
        if df.default:
            field["default"] = df.default
        fields.append(field)
        if len(fields) >= FIELD_LIMIT:
            break

    out = {
        "doctype": doctype,
        "is_submittable": bool(meta.is_submittable),
        "is_single": bool(meta.issingle),
        "is_child_table": bool(meta.istable),
        "title_field": meta.title_field,
        "fields": fields,
        "truncated_fields": len(fields) >= FIELD_LIMIT,
        "permissions": {p: bool(frappe.has_permission(doctype, p)) for p in PERMISSION_TYPES},
    }

    if name:
        out["record"] = _record_actions(doctype, name, meta)

    return out


def _record_actions(doctype: str, name: str, meta) -> dict:
    """What the current user can actually do to this one record."""
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")

    actions = []
    docstatus = int(doc.docstatus or 0)
    if meta.is_submittable:
        if docstatus == 0 and frappe.has_permission(doctype, "submit", doc=doc):
            actions.append("submit")
        elif docstatus == 1 and frappe.has_permission(doctype, "cancel", doc=doc):
            actions.append("cancel")
        elif docstatus == 2 and frappe.has_permission(doctype, "amend", doc=doc):
            actions.append("amend")
    if docstatus != 1 and frappe.has_permission(doctype, "delete", doc=doc):
        actions.append("delete")
    if docstatus == 0 and frappe.has_permission(doctype, "write", doc=doc):
        actions.append("update")

    out = {"name": doc.name, "docstatus": docstatus, "actions": actions}

    transitions = _workflow_transitions(doc)
    if transitions:
        out["workflow_transitions"] = transitions
        out["workflow_state"] = doc.get(_workflow_state_field(doc.doctype))

    return out


def _workflow_transitions(doc) -> list:
    from frappe.model.workflow import get_transitions

    try:
        return [t.get("action") for t in get_transitions(doc) or [] if t.get("action")]
    except Exception:
        # No workflow on this doctype, or the user holds no role in it — not an error.
        return []


def _workflow_state_field(doctype: str):
    return frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "workflow_state_field")
