# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``run_action`` AI Tool."""

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
    "run_action",
    """Run a document action: submit, cancel, amend, rename, a workflow transition, or
    a whitelisted method. Confirm the action is available with describe(doctype, name)
    first — it lists exactly what this user can do to that record in its current state.

    `args` carries extras: {"new_name": "..."} for rename, {"action": "Approve"} is
    passed as `action` itself for a workflow transition.""",
    confirm=True,
    tags=["write"],
    label="Running Document Actions",
)
def run_action_tool(doctype: str, names: list, action: str, args: dict | None = None) -> dict:
    # Each action below enforces its own permission (submit/cancel/amend through the
    # document, rename and whitelisted methods explicitly), so this is not a second
    # permission check — it is the schema guard the other write tools apply, which
    # run_action was skipping. Without it, `create` on a Client Script is refused
    # while `run_action` on one is not.
    _assert_schema_safe(doctype)
    names = _as_names(names)
    args = args or {}
    action_key = (action or "").strip()
    if not action_key:
        raise frappe.ValidationError("`action` is required")

    done, failures = [], []
    for name in names:
        savepoint = _savepoint()
        try:
            doc = frappe.get_doc(doctype, name)
            done.append({"name": name, "result": _apply_action(doc, action_key, args)})
        except Exception as e:
            frappe.db.rollback(save_point=savepoint)
            failures.append({"name": name, "action": action_key, "error": _error(e)})

    out = {"doctype": doctype, "action": action_key, "done": done, "count": len(done)}
    if failures:
        out["failures"] = failures
    return out

def _apply_action(doc, action: str, args: dict):
    """Lifecycle first, then workflow, then whitelisted methods."""
    lowered = action.lower()
    permission = {
        "submit": "submit",
        "cancel": "cancel",
        "amend": "amend",
        "rename": "write",
    }.get(lowered)
    if permission:
        access.require_permission(doc.doctype, permission, doc=doc)

    if lowered == "submit":
        doc.submit()
        return "submitted"
    if lowered == "cancel":
        doc.cancel()
        return "cancelled"
    if lowered == "amend":
        amended = frappe.copy_doc(doc)
        amended.amended_from = doc.name
        amended.docstatus = 0
        amended.insert()
        return amended.name
    if lowered == "rename":
        new_name = args.get("new_name")
        if not new_name:
            raise frappe.ValidationError('rename needs args {"new_name": "..."}')
        return frappe.rename_doc(doc.doctype, doc.name, new_name, force=False)

    if _is_workflow_action(doc, action):
        access.require_permission(doc.doctype, "write", doc=doc)
        from frappe.model.workflow import apply_workflow

        apply_workflow(doc, action)
        return f"workflow: {action}"

    return _call_whitelisted(doc, action, args)

def _is_workflow_action(doc, action: str) -> bool:
    from frappe.model.workflow import get_transitions

    try:
        return action in [t.get("action") for t in get_transitions(doc) or []]
    except Exception:
        return False

def _call_whitelisted(doc, method: str, args: dict):
    """Only methods the controller explicitly whitelisted — never arbitrary attributes."""
    fn = getattr(doc, method, None)
    if not callable(fn) or not getattr(fn, "__func__", fn).__dict__.get("whitelisted"):
        raise frappe.ValidationError(
            f"{method!r} is not an available action on {doc.doctype}. "
            "Call describe(doctype, name) to see what is."
        )
    doc.check_permission("write")
    return fn(**(args or {}))

def _assert_schema_safe(doctype: str):
    if doctype in DEVELOPER_ONLY and not _schema_changes_allowed():
        raise frappe.PermissionError(
            f"{doctype} changes how the site works and the copilot is not allowed to write it. "
            "A developer must make this change (or set copilot_allow_schema_changes in "
            "site_config.json on a development site)."
        )

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
