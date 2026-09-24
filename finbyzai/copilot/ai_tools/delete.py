# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``delete`` AI Tool."""

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
    "delete",
    """Delete records. Refuses submitted documents — cancel them with run_action first.
    Prefer cancelling or updating status over deleting; deletion is not recoverable and
    fails anyway when other records link to the one being deleted.""",
    confirm=True,
    tags=["write"],
    label="Deleting Records",
)
def delete_tool(doctype: str, names: list) -> dict:
    _assert_writable(doctype, "delete")
    names = _as_names(names)

    deleted, failures = [], []
    for name in names:
        savepoint = _savepoint()
        try:
            doc = frappe.get_doc(doctype, name)
            doc.check_permission("delete")
            if int(doc.docstatus or 0) == 1:
                raise frappe.ValidationError(
                    f"{name} is submitted. Cancel it with run_action before deleting."
                )
            doc.delete()
            deleted.append(name)
        except Exception as e:
            frappe.db.rollback(save_point=savepoint)
            failures.append({"name": name, "error": _error(e)})

    return _result(doctype, "deleted", deleted, failures)

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
