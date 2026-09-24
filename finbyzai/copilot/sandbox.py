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

    try:
        with safe_exec_flags():
            exec(compile_code(code, filename=SCRIPT_FILENAME), exec_globals, exec_locals)
    except Exception as e:
        raise _explained(e) from e

    result = exec_locals.get("result", exec_globals.get("result"))
    return {"result": _clip(result), "output": _printed(exec_locals, exec_globals)}


# What the sandbox actually offers, repeated in the error because that is where a
# model reliably reads it — the tool description is read once, an error is read at
# exactly the moment the script has to be rewritten.
AVAILABLE = (
    "frappe.get_list, frappe.get_doc, frappe.get_meta, frappe.get_value, "
    "frappe.db.count, frappe.db.exists, frappe.utils.*, and the copilot's own "
    "read / aggregate / count / run_report"
)


def _explained(error: Exception) -> Exception:
    """Turn RestrictedPython's terse refusals into something a model can act on.

    `__import__ not found` says nothing about what to do instead, so the same script
    comes back a second time with the same import at the top. Naming the replacement
    is what actually stops the loop.
    """
    text = str(error)

    if isinstance(error, ImportError) or "__import__" in text:
        return frappe.ValidationError(
            "This sandbox has no imports — do not write `import frappe` or any other "
            f"import. Everything you need is already defined: {AVAILABLE}. "
            "Rewrite the script without the import line."
        )

    if isinstance(error, AttributeError) and "no attribute" in text:
        missing = text.rsplit("'", 2)[-2] if "'" in text else ""
        if missing == "get_all":
            return frappe.ValidationError(
                "`frappe.get_all` is not available here because it ignores permissions. "
                "Use `frappe.get_list` instead — same arguments, and it returns only "
                "rows this user may see."
            )
        return frappe.ValidationError(
            f"`{missing}` is not available in this sandbox. Available names: {AVAILABLE}."
        )

    return error


def build_globals() -> NamespaceDict:
    """The sandbox namespace. Everything data-shaped in here is permission-checked."""
    from finbyzai.copilot.ai_tools.aggregate import aggregate_tool
    from finbyzai.copilot.ai_tools.count import count_tool
    from finbyzai.copilot.ai_tools.read import read_tool
    from finbyzai.copilot.ai_tools.run_report import run_report_tool

    out = NamespaceDict(
        json=NamespaceDict(loads=json.loads, dumps=json.dumps),
        frappe=NamespaceDict(
            # reads — permission-respecting only
            get_list=_gated_get_list,
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
        read=read_tool,
        aggregate=aggregate_tool,
        count=count_tool,
        run_report=run_report_tool,
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


def _gated_get_list(
    doctype: str, fields=None, filters=None, or_filters=None,
    group_by=None, order_by=None, limit_start=0, limit_page_length=20,
    *, limit=None, start=None, page_length=None, as_list=False,
    distinct=False, pluck=None, parent_doctype=None,
):
    """Expose query options without permission overrides or user impersonation."""
    return frappe.get_list(
        doctype, fields=fields, filters=filters, or_filters=or_filters,
        group_by=group_by, order_by=order_by, limit_start=limit_start,
        limit_page_length=limit_page_length, limit=limit, start=start,
        page_length=page_length, as_list=as_list, distinct=distinct,
        pluck=pluck, parent_doctype=parent_doctype,
        user=frappe.session.user, ignore_permissions=False,
    )


def _gated_get_doc(doctype: str, name: str | None = None) -> dict:
    """A record as a plain dict, after a read permission check. Returning a dict rather
    than a Document keeps `.save()`/`.delete()` out of the sandbox."""
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    doc.apply_fieldlevel_read_permissions()
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
