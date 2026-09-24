# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``read`` AI Tool."""

import json

import re

import frappe

from finbyzai.copilot import access, blocks

from finbyzai.copilot.registry import tool

def permitted_count(doctype: str, filters: dict) -> int:
    """A row count that respects user permissions and permission-query conditions.

    `frappe.db.count` runs a raw SQL count with no permission filtering at all —
    a user restricted to their own territory would get the site-wide total. Routed
    through `frappe.get_list` (same rule as the module docstring) with an aggregate
    field instead of loading rows.
    """
    rows = frappe.get_list(doctype, filters=filters, fields=["count(name) as total"], limit_page_length=0)
    return rows[0].total if rows else 0

ROW_LIMIT = 200

PREVIEW_ROWS = 20

OPERATORS = frozenset(
    (
        "=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in",
        "between", "is", "descendants of", "ancestors of", "not descendants of",
        "not ancestors of",
    )
)

def normalize_filters(doctype: str, filters):
    """Accept every filter shape a model plausibly writes, and reject the rest loudly.

    This is not politeness. Two of these shapes were *silently* returning the wrong
    answer — a JSON string matched nothing, and the four-element form with a leading
    doctype was ignored by `frappe.db.count`, which answered 134 for a filter that
    should have matched 0 — and a third ("between" with the two dates unwrapped, the
    most natural thing to write) failed with "too many values to unpack", which tells
    the model nothing it can act on. A wrong number presented as fact is the worst
    outcome this app can produce, so the shapes are handled here instead.

    Accepted:
        {"status": "Overdue"}                       plain equality
        {"posting_date": [">=", "2026-01-01"]}      operator and value
        {"posting_date": ["between", a, b]}         two values, unwrapped
        {"status": ["in", "Paid", "Overdue"]}       values, unwrapped
        [["posting_date", ">=", "2026-01-01"]]      Frappe's list form
        [["Sales Invoice", "status", "=", "Paid"]]  the same, with the doctype
        '{"status": "Overdue"}'                     a JSON string
        {"filters": {...}}                          double-wrapped
    """
    if not filters:
        return {}

    if isinstance(filters, str):
        text = filters.strip()
        try:
            filters = json.loads(text)
        except ValueError:
            frappe.throw(
                f"`filters` was the string {text[:80]!r}, which is not JSON. Pass an "
                'object like {"status": "Overdue"}.',
                title="Bad filters",
            )

    # Models sometimes wrap the argument in its own name.
    while isinstance(filters, dict) and set(filters) == {"filters"}:
        filters = filters["filters"]

    if isinstance(filters, dict):
        return {key: _condition(key, value) for key, value in filters.items()}

    if isinstance(filters, list | tuple):
        out = {}
        for item in filters:
            if isinstance(item, dict):
                out.update(normalize_filters(doctype, item))
                continue
            if not isinstance(item, list | tuple):
                frappe.throw(
                    f"`filters` contained {item!r}. Each condition must be "
                    '[fieldname, operator, value].',
                    title="Bad filters",
                )
            parts = list(item)
            # [doctype, fieldname, operator, value] — Frappe's own long form. It is
            # only meaningful for a joined read, and `count` drops it silently, so the
            # doctype is removed when it is this one and refused when it is another.
            if len(parts) == 4:
                if parts[0] != doctype:
                    frappe.throw(
                        f"`filters` asked about {parts[0]}, but this call reads {doctype}. "
                        "Read one doctype at a time.",
                        title="Bad filters",
                    )
                parts = parts[1:]
            if len(parts) == 3:
                out[parts[0]] = _condition(parts[0], [parts[1], parts[2]])
            elif len(parts) == 2:
                out[parts[0]] = _condition(parts[0], parts[1])
            else:
                frappe.throw(
                    f"`filters` contained {item!r}. Each condition must be "
                    '[fieldname, operator, value].',
                    title="Bad filters",
                )
        return out

    frappe.throw(
        f"`filters` must be an object, not {type(filters).__name__}. "
        'For example {"status": "Overdue"}.',
        title="Bad filters",
    )

def _condition(field: str, value):
    """One field's condition, in the [operator, value] shape Frappe expects."""
    if not isinstance(value, list | tuple):
        return value

    parts = list(value)
    if not parts:
        frappe.throw(f"`filters` gave {field} an empty condition.", title="Bad filters")

    operator = parts[0] if isinstance(parts[0], str) else None
    if not operator or operator.lower() not in OPERATORS:
        # A bare list of values is an `in`, which is what a model means by
        # {"status": ["Paid", "Overdue"]}.
        return ["in", parts]

    operator = operator.lower()
    rest = parts[1:]
    if len(rest) == 1:
        return [operator, rest[0]]
    # ["between", a, b] and ["in", a, b, c] — the values written out rather than
    # wrapped in their own list. This is the most common thing a model writes.
    if operator == "between":
        if len(rest) != 2:
            frappe.throw(
                f"`filters` gave {field} a `between` with {len(rest)} values. "
                "It takes exactly two, the start and the end.",
                title="Bad filters",
            )
        return [operator, rest]
    if operator in ("in", "not in"):
        return [operator, rest]
    frappe.throw(
        f"`filters` gave {field} the condition {value!r}. Use [operator, value], "
        'for example ["between", ["2026-01-01", "2026-03-31"]].',
        title="Bad filters",
    )

def scope_to_live(doctype: str, conditions: dict):
    """Exclude cancelled documents, and say whether drafts are in the answer.

    Watching six models answer "which customer bought the most this year", the ones
    that used `aggregate` reported 28.8M and the one that used the domain tool
    reported 19.8M. The domain tool was right: it filters docstatus, and this site
    has 34 cancelled Sales Invoices in 2026. A cancelled document is not data, so it
    is excluded unless the model asked about docstatus itself — and because a draft
    *is* data but is not revenue, the count of drafts is handed back so the model can
    say so rather than quietly mixing them in.
    """
    if "docstatus" in conditions:
        return conditions, None
    try:
        if not frappe.get_meta(doctype).is_submittable:
            return conditions, None
    except Exception:
        return conditions, None

    scoped = {**conditions, "docstatus": ["!=", 2]}
    drafts = permitted_count(doctype, {**conditions, "docstatus": 0})
    if drafts:
        note = (
            f"Cancelled documents are excluded. {drafts} of these are drafts — add "
            'docstatus=1 to the filters for submitted documents only, which is what '
            "money and volume questions usually mean."
        )
    else:
        note = "Cancelled documents are excluded; every record here is submitted."
    return scoped, note

@tool(
    "read",
    """Read records. `filters` is a dict like {"status": "Overdue"} or
    {"posting_date": [">=", "2026-01-01"]}. `fields` defaults to name + title field.
    Use `parent` to read a child table (e.g. doctype="Sales Invoice Item",
    parent="Sales Invoice"). Confirm fieldnames with describe() first.""",
    tags=["read"],
    label="Reading DocType Records",
)
def read_tool(
    doctype: str,
    filters: dict | None = None,
    fields: list | None = None,
    order_by: str | None = None,
    limit: int = 50,
    parent: str | None = None,
) -> dict:
    permission_doctype = parent if parent and frappe.get_meta(doctype).istable else doctype
    access.require_permission(permission_doctype, "read")
    limit = max(1, min(int(limit or 50), ROW_LIMIT))
    conditions, scope = scope_to_live(doctype, normalize_filters(doctype, filters))
    kwargs = {
        "filters": conditions,
        "fields": fields or _default_fields(doctype),
        "limit": limit + 1,
        "ignore_ifnull": True,
    }
    if order_by:
        kwargs["order_by"] = order_by
    if parent:
        kwargs["parent_doctype"] = parent

    rows = frappe.get_list(doctype, **kwargs)
    truncated = len(rows) > limit
    rows = rows[:limit]

    payload = {
        "doctype": doctype,
        "rows": rows[:PREVIEW_ROWS],
        "count": len(rows),
        "scope": scope,
        "truncated": truncated,
        "hint": "More rows exist. Narrow the filters or aggregate instead of paging."
        if truncated
        else None,
    }
    if len(rows) > PREVIEW_ROWS:
        payload["preview_only"] = (
            f"You are seeing {PREVIEW_ROWS} of {len(rows)} rows; the user sees all of them "
            "in the table. Summarize, do not list them back."
        )
    # The user sees every row as a table; the model only needs a sample of them.
    return blocks.attach(payload, blocks.table(rows, doctype=doctype)) if rows else payload

def _default_fields(doctype: str) -> list:
    """name plus the title field, so a bare read() is still readable."""
    fields = ["name"]
    try:
        title_field = frappe.get_meta(doctype).title_field
    except Exception:
        title_field = None
    if title_field and title_field != "name":
        fields.append(title_field)
    return fields
