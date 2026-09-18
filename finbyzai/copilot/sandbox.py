# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""A permission-respecting sandbox for the `execute` tool.

The agent needs an escape hatch for questions no fixed tool answers — joins across
doctypes, a computation over rows, an ad-hoc grouping. This runs the model's Python
through RestrictedPython with a namespace where *every* data function enforces the
calling user's permissions.

What is deliberately NOT in the namespace, and why:

    frappe.get_all          ignores permissions entirely (verified: a Guest user reads
                            Sales Invoices through it)
    frappe.db.sql           raw SQL cannot respect row-level permissions
    frappe.qb               same, via the query builder
    frappe.db.set_value     writes with no validation, no hooks, no audit
    frappe.db.get_value     reads with no permission check
    import / __import__     no module loading; nothing reaches the filesystem,
                            the network, or the shell
    open / eval / exec      same reason

There is no shell access here and there never should be: this is sandboxed Python,
not a terminal. A chat that can run shell commands on a production ERP is one
prompt-injected PDF away from losing the site.

Writes are not available in the sandbox either. The agent writes through the
`create` / `update` / `run_action` tools, which are approval-gated individually — so
a write can never hide inside a block of generated code the user approved as
"analysis".
"""

import json

import frappe
import RestrictedPython.Guards
from frappe.utils.safe_exec import (
    NamespaceDict,
    _getattr_for_safe_exec,
    _getitem,
    _write,
    get_python_builtins,
    safe_exec_flags,
)

from finbyzai.copilot._restricted import (
    SAFE_DATA_UTILS,
    SAFE_EXCEPTIONS,
    compile_code,
    protected_inplacevar,
)
from RestrictedPython import safe_globals
from RestrictedPython.PrintCollector import PrintCollector

RESULT_LIMIT = 200
SCRIPT_FILENAME = "<copilot execute>"


def execute_code(code: str) -> dict:
    """Run `code` in the sandbox and return what it produced.

    The script reports back by assigning to `result`, or by printing. Both are
    returned; `result` is preferred because it keeps its type.
    """
    exec_globals = build_globals()
    exec_locals = {}

    with safe_exec_flags():
        exec(compile_code(code, filename=SCRIPT_FILENAME), exec_globals, exec_locals)

    result = exec_locals.get("result", exec_globals.get("result"))
    return {"result": _clip(result), "output": _printed(exec_locals, exec_globals)}


def build_globals() -> NamespaceDict:
    """The sandbox namespace. Everything data-shaped in here is permission-checked."""
    from finbyzai.copilot.tools import data as data_tools
    from finbyzai.copilot.tools import reports as report_tools

    out = NamespaceDict(
        json=NamespaceDict(loads=json.loads, dumps=json.dumps),
        frappe=NamespaceDict(
            # reads — permission-respecting only
            get_list=frappe.get_list,
            get_doc=_gated_get_doc,
            get_meta=_gated_get_meta,
            get_value=_gated_get_value,
            db=NamespaceDict(count=_gated_count, exists=_gated_exists),
            # session / context
            session=frappe._dict(user=frappe.session.user),
            user=frappe.session.user,
            utils=NamespaceDict(**SAFE_DATA_UTILS),
            # messaging
            throw=frappe.throw,
            msgprint=frappe.msgprint,
            format_value=frappe.format_value,
            _=frappe._,
            **SAFE_EXCEPTIONS,
        ),
        # the copilot's own tools, so generated code composes with them instead of
        # reimplementing them badly
        read=data_tools.read,
        aggregate=data_tools.aggregate,
        count=data_tools.count,
        run_report=report_tools.run_report,
        # RestrictedPython plumbing
        _getitem_=_getitem,
        _getattr_=_getattr_for_safe_exec,
        _write_=_write,
        # iteration / comprehensions / augmented assignment
        _getiter_=iter,
        _iter_unpack_sequence_=RestrictedPython.Guards.guarded_iter_unpack_sequence,
        _inplacevar_=protected_inplacevar,
        _print_=_Collector,
        **{k: v for k, v in SAFE_DATA_UTILS.items()},
    )

    # Order matters: safe_globals brings RestrictedPython's safe __builtins__ (len, str,
    # int, round, zip …). Frappe's extras go into the namespace itself, exactly as
    # frappe.utils.safe_exec.get_safe_globals does — never over __builtins__, or the
    # safe builtins are lost and ordinary code like round() stops working.
    out.update(safe_globals)
    out.update(get_python_builtins())
    return out


class _Collector(PrintCollector):
    """RestrictedPython's collector, kept as-is: it buffers into self.txt so the
    script's print() output can be returned to the model. Frappe's own
    FrappePrintCollector forwards to the site log and buffers nothing."""


def _printed(exec_locals: dict, exec_globals: dict):
    """RestrictedPython binds the collector to `_print` in the scope that used print()."""
    collector = exec_locals.get("_print") or exec_globals.get("_print")
    if not collector:
        return None
    text = "".join(getattr(collector, "txt", []) or [])
    return text.strip() or None


# ── gated primitives ──────────────────────────────────────────────────────────


def _gated_get_doc(doctype: str, name: str | None = None) -> dict:
    """A record as a plain dict, after a read permission check. Returning a dict rather
    than a Document keeps `.save()`/`.delete()` out of the sandbox."""
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    return doc.as_dict()


def _gated_get_meta(doctype: str) -> dict:
    if not frappe.has_permission(doctype, "read"):
        raise frappe.PermissionError(f"No permission to read {doctype}")
    return frappe.get_meta(doctype).as_dict()


def _gated_get_value(doctype: str, filters=None, fieldname: str = "name"):
    """frappe.db.get_value skips permissions; route it through get_list instead."""
    fields = fieldname if isinstance(fieldname, list) else [fieldname]
    rows = frappe.get_list(doctype, filters=filters or {}, fields=fields, limit=1)
    if not rows:
        return None
    row = rows[0]
    return row.get(fields[0]) if len(fields) == 1 else row


def _gated_count(doctype: str, filters=None) -> int:
    """frappe.db.count skips permissions, same as get_value; route through get_list."""
    if not frappe.has_permission(doctype, "read"):
        raise frappe.PermissionError(f"No permission to read {doctype}")
    rows = frappe.get_list(
        doctype, filters=filters or {}, fields=["count(name) as total"], limit_page_length=0
    )
    return rows[0].total if rows else 0


def _gated_exists(doctype: str, name=None) -> bool:
    """frappe.db.exists skips permissions (get_value(..., ignore=True) under the hood)."""
    if not frappe.has_permission(doctype, "read"):
        raise frappe.PermissionError(f"No permission to read {doctype}")
    filters = name if isinstance(name, dict) else ({"name": name} if name is not None else {})
    return bool(frappe.get_list(doctype, filters=filters, fields=["name"], limit=1))


def _clip(value):
    """Keep a runaway result from becoming the whole context window."""
    if isinstance(value, list) and len(value) > RESULT_LIMIT:
        return value[:RESULT_LIMIT]
    return value
