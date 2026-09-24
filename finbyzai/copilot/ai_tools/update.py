# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``update`` AI Tool."""

import frappe

import frappe.model

from finbyzai.copilot import access, blocks

from finbyzai.copilot.registry import tool

BATCH_LIMIT = 50

DEVELOPER_ONLY = {
    "DocType",
    "Custom Field",
    "Property Setter",
    "Customize Form",
    "Server Script",
    "Client Script",
    "Custom DocPerm",
}

@tool(
    "update",
    """Update fields on existing records. `names` is a list of record names, `values`
    the fields to set on each. Read the records first so you know their current state,
    and check describe(doctype, name) if the record may be submitted — submitted
    records only allow fields marked "allow on submit".""",
    confirm=True,
    tags=["write"],
    label="Updating Records",
)
def update_tool(doctype: str, names: list, values: dict) -> dict:
    _assert_writable(doctype, "write")
    if not isinstance(values, dict) or not values:
        raise frappe.ValidationError("`values` must be a non-empty dict of field -> value")
    names = _as_names(names)
    _assert_known_fields(doctype, values)

    updated, failures = [], []
    for name in names:
        savepoint = _savepoint()
        try:
            doc = frappe.get_doc(doctype, name)
            doc.check_permission("write")
            doc.update(values)
            doc.save()
            updated.append(doc.name)
        except Exception as e:
            frappe.db.rollback(save_point=savepoint)
            failures.append({"name": name, "error": _error(e)})

    return _result(doctype, "updated", updated, failures)

def _assert_schema_safe(doctype: str):
    if doctype in DEVELOPER_ONLY and not _schema_changes_allowed():
        raise frappe.PermissionError(
            f"{doctype} changes how the site works and the copilot is not allowed to write it. "
            "A developer must make this change (or set copilot_allow_schema_changes in "
            "site_config.json on a development site)."
        )

def _assert_writable(doctype: str, permission: str):
    _assert_schema_safe(doctype)
    if not frappe.has_permission(doctype, permission):
        raise frappe.PermissionError(f"No permission to {permission} {doctype}")

def _schema_changes_allowed() -> bool:
    """Both flags, deliberately: developer_mode alone is on for most of our dev benches,
    and "the agent may rewrite the schema" should never be the default anywhere."""
    return bool(frappe.conf.get("developer_mode")) and bool(
        frappe.conf.get("copilot_allow_schema_changes")
    )

CONTROL_FIELDS = frozenset(
    (
        "docstatus",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "parent",
        "parenttype",
        "parentfield",
        "idx",
        "_user_tags",
        "_comments",
        "_assign",
        "_liked_by",
    )
)

def _assert_no_control_fields(doctype: str, values):
    """Refuse the fields that change a document's state or provenance."""
    if isinstance(values, list | tuple):
        for item in values:
            _assert_no_control_fields(doctype, item)
        return
    if not isinstance(values, dict):
        return
    found = sorted(set(values) & CONTROL_FIELDS)
    if found:
        raise frappe.ValidationError(
            f"{', '.join(found)} cannot be set through this tool. "
            "Submitting, cancelling and re-assigning are actions, not fields: create "
            "or update the document, then call run_action(action='submit') — which "
            "runs the document's own lifecycle and asks the user again."
        )
    # Child rows carry the same trap.
    for value in values.values():
        if isinstance(value, list | tuple):
            _assert_no_control_fields(doctype, value)

def _assert_known_fields(doctype: str, values: dict):
    """Frappe's doc.update() silently drops keys that aren't fields, so a typo would
    look like a success and the model would report having set something it didn't."""
    if not isinstance(values, dict):
        return
    _assert_no_control_fields(doctype, values)
    meta = frappe.get_meta(doctype)
    known = {df.fieldname for df in meta.fields}
    known.update(frappe.model.default_fields)
    known.update(frappe.model.child_table_fields)
    known.update({"name", "doctype", "amended_from"})

    unknown = [key for key in values if key not in known]
    if unknown:
        raise frappe.ValidationError(
            f"Unknown field(s) on {doctype}: {', '.join(sorted(unknown))}. "
            f"Call describe({doctype!r}) and use the exact fieldnames it lists."
        )

def _savepoint() -> str:
    name = f"copilot_row_{frappe.generate_hash(length=8)}"
    frappe.db.savepoint(name)
    return name

def _as_names(names) -> list:
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        raise frappe.ValidationError("`names` must be a non-empty list of record names")
    if len(names) > BATCH_LIMIT:
        raise frappe.ValidationError(f"At most {BATCH_LIMIT} records per call, got {len(names)}")
    return names

def _error(e: Exception) -> dict:
    """Per-row errors get the same normalization as a whole failed tool call."""
    from finbyzai.copilot.guard import normalize_exception

    payload = normalize_exception(e)
    payload.pop("ok", None)
    return payload

def _result(doctype: str, key: str, names: list, failures: list) -> dict:
    out = {"doctype": doctype, key: names, "count": len(names)}
    if failures:
        out["failures"] = failures
        out["hint"] = (
            f"{len(failures)} row(s) failed and were rolled back; the {key} records above are saved. "
            f"Fix only the failed rows and retry those — do not repeat the successful ones."
        )
    if names:
        out = blocks.attach(out, blocks.records(doctype, [{"name": n} for n in names]))
    return out
