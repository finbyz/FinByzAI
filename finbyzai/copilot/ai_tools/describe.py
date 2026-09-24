# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``describe`` AI Tool."""

import frappe

from finbyzai.copilot import access

from finbyzai.copilot.registry import tool

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

FIELD_LIMIT = 200

@tool(
    "describe",
    """Inspect a DocType: its fields, your permissions on it, and — when `name` is a
    record — the actions available on that record. Call this before create/update so
    you use real fieldnames, and before run_action so you use a real action.""",
    tags=["discovery"],
    label="Reading DocType Meta",
)
def describe_tool(doctype: str, name: str | None = None) -> dict:
    access.require_permission(doctype, "read")

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
