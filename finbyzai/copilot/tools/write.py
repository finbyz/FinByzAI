# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Writes — every tool here is registered with confirm=True.

The runner pauses before the call and shows an Approve / Reject card with the exact
doctype, values and record count. Nothing here runs without that answer.

Three rules the implementations follow:

1. Go through the Document API (`frappe.new_doc` / `frappe.get_doc` + `insert` /
   `save`), never `frappe.db.set_value`. Validations, hooks and the audit trail are
   the point, not an obstacle.
2. One savepoint per row. A batch of 10 where row 4 is invalid keeps the 9 good rows
   and reports row 4 — instead of losing the batch or, worse, half-committing it.
3. Return the names of what was written. A retry after a partial failure must be able
   to reuse them rather than inserting duplicates — the failure mode visible in the
   Flow screenshot, where a chart was created and only the link step failed.

Errors are not raised out of here: the guard turns a MandatoryError into
{"fields": [...], "hint": "add every field listed and retry"} so the model corrects
its own arguments.
"""

import frappe
import frappe.model

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

BATCH_LIMIT = 50

# Schema and layout doctypes: creating these rewrites how the site works, so they are
# refused unless the site is explicitly in developer mode.
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
    "create",
    """Create one or more records. `records` is a list of field-value dicts. Call
    describe(doctype) first so every fieldname is real. Child tables are nested lists
    of dicts, e.g. {"customer": "X", "items": [{"item_code": "A", "qty": 2}]}.

    Rows are inserted independently: you get back the names that succeeded and a
    per-row error for the ones that failed. If a later step fails, reuse the returned
    names — do not create the records again.""",
    confirm=True,
    tags=["write"],
    label="Creating Records",
)
def create(doctype: str, records: list) -> dict:
    _assert_writable(doctype, "create")
    rows = _as_rows(records)

    created, failures = [], []
    for index, values in enumerate(rows):
        savepoint = _savepoint()
        try:
            _assert_known_fields(doctype, values)
            doc = frappe.new_doc(doctype)
            doc.update(values or {})
            doc.insert()
            created.append(doc.name)
        except Exception as e:
            frappe.db.rollback(save_point=savepoint)
            failures.append({"row": index, "error": _error(e)})

    return _result(doctype, "created", created, failures)


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
def update(doctype: str, names: list, values: dict) -> dict:
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
def run_action(doctype: str, names: list, action: str, args: dict | None = None) -> dict:
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


@tool(
    "delete",
    """Delete records. Refuses submitted documents — cancel them with run_action first.
    Prefer cancelling or updating status over deleting; deletion is not recoverable and
    fails anyway when other records link to the one being deleted.""",
    confirm=True,
    tags=["write"],
    label="Deleting Records",
)
def delete(doctype: str, names: list) -> dict:
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


# ── helpers ───────────────────────────────────────────────────────────────────


def _apply_action(doc, action: str, args: dict):
    """Lifecycle first, then workflow, then whitelisted methods."""
    lowered = action.lower()

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


def _assert_writable(doctype: str, permission: str):
    if doctype in DEVELOPER_ONLY and not _schema_changes_allowed():
        raise frappe.PermissionError(
            f"{doctype} changes how the site works and the copilot is not allowed to write it. "
            "A developer must make this change (or set copilot_allow_schema_changes in "
            "site_config.json on a development site)."
        )
    if not frappe.has_permission(doctype, permission):
        raise frappe.PermissionError(f"No permission to {permission} {doctype}")


def _schema_changes_allowed() -> bool:
    """Both flags, deliberately: developer_mode alone is on for most of our dev benches,
    and "the agent may rewrite the schema" should never be the default anywhere."""
    return bool(frappe.conf.get("developer_mode")) and bool(
        frappe.conf.get("copilot_allow_schema_changes")
    )


# Columns that decide what a document *is*, rather than what it says. `doc.update()`
# takes them like any other field: watching a run, the model put `docstatus: 1` in a
# create and Frappe inserted a submitted document — no submit(), no on_submit hooks,
# so a Sales Invoice like that is posted with no ledger entries behind it. The
# approval card, meanwhile, said only "Create 1 record".
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


def _as_rows(records) -> list:
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list) or not records:
        raise frappe.ValidationError("`records` must be a non-empty list of field-value dicts")
    if len(records) > BATCH_LIMIT:
        raise frappe.ValidationError(f"At most {BATCH_LIMIT} records per call, got {len(records)}")
    return records


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
