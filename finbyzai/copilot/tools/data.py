# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Permission-respecting reads.

Rule that must not be broken: `frappe.get_list`, never `frappe.get_all`. get_all
ignores permissions — tested on this site, a Guest user reading Sales Invoices
through get_all returns rows, while get_list raises "Insufficient Permission".

`aggregate` exists so the model never writes SQL. Frappe v16 rejects function
strings in `fields` ("SQL functions are not allowed as strings in SELECT ... use
dict syntax") and the models don't know that; this tool takes a plain
group_by/measure/agg and builds the dict form itself.
"""

import json

import frappe

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

ROW_LIMIT = 200
AGG_LIMIT = 200
# Rows handed to the model. The table block carries all of them to the panel — a
# 200-row read should not spend 200 rows of context.
PREVIEW_ROWS = 20
AGGREGATIONS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}


# ── filters ───────────────────────────────────────────────────────────────────

# Every operator Frappe understands, so a mistyped one is caught here with a hint
# rather than turning into a confusing SQL error.
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


@tool(
    "read",
    """Read records. `filters` is a dict like {"status": "Overdue"} or
    {"posting_date": [">=", "2026-01-01"]}. `fields` defaults to name + title field.
    Use `parent` to read a child table (e.g. doctype="Sales Invoice Item",
    parent="Sales Invoice"). Confirm fieldnames with describe() first.""",
    tags=["read"],
    label="Reading DocType Records",
)
def read(
    doctype: str,
    filters: dict | None = None,
    fields: list | None = None,
    order_by: str | None = None,
    limit: int = 50,
    parent: str | None = None,
) -> dict:
    limit = max(1, min(int(limit or 50), ROW_LIMIT))
    conditions = normalize_filters(doctype, filters)
    kwargs = {
        "filters": conditions,
        "fields": fields or _default_fields(doctype),
        "limit": limit + 1,
        "ignore_ifnull": True,
    }
    if order_by:
        kwargs["order_by"] = order_by
    if parent:
        kwargs["parent"] = parent

    rows = frappe.get_list(doctype, **kwargs)
    truncated = len(rows) > limit
    rows = rows[:limit]

    payload = {
        "doctype": doctype,
        "rows": rows[:PREVIEW_ROWS],
        "count": len(rows),
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


@tool(
    "aggregate",
    """Grouped totals without writing SQL: e.g. total sales per customer is
    aggregate("Sales Invoice", group_by="customer", measure="base_grand_total",
    agg="sum"). `agg` is one of sum, count, avg, min, max. Returns rows sorted by the
    aggregate, largest first — use it for "top N" and "per month" questions.

    The user is shown a chart and a table of the result automatically. `chart` picks the
    shape: "bar" (default, for rankings), "line" (for anything over time — months,
    dates), or "none" for a table only.""",
    tags=["read"],
    label="Grouping Records",
)
def aggregate(
    doctype: str,
    group_by: str,
    measure: str | None = None,
    agg: str = "sum",
    filters: dict | None = None,
    limit: int = 20,
    ascending: bool = False,
    chart: str = "bar",
) -> dict:
    agg_key = (agg or "sum").lower()
    if agg_key not in AGGREGATIONS:
        raise frappe.ValidationError(f"agg must be one of {', '.join(AGGREGATIONS)}, got {agg!r}")
    if agg_key != "count" and not measure:
        raise frappe.ValidationError(f"`measure` is required for agg={agg_key!r} (the field to total)")

    limit = max(1, min(int(limit or 20), AGG_LIMIT))
    conditions = normalize_filters(doctype, filters)
    alias = "value"
    function = {AGGREGATIONS[agg_key]: "*" if agg_key == "count" else measure, "as": alias}

    rows = frappe.get_list(
        doctype,
        filters=conditions,
        fields=[group_by, function],
        group_by=group_by,
        order_by=f"{alias} {'asc' if ascending else 'desc'}",
        limit=limit,
    )

    payload = {
        "doctype": doctype,
        "group_by": group_by,
        "measure": measure,
        "agg": agg_key,
        "rows": rows,
        "count": len(rows),
    }
    if not rows:
        return payload

    title = f"{agg_key.upper()} of {measure or 'records'} by {frappe.unscrub(group_by)}"
    series = [{"key": alias, "label": title}]
    # The measure's own fieldtype decides how the figure reads. A SUM of a Currency
    # field is money; a COUNT never is, whatever the field was called.
    columns = [group_by, {"key": alias, "label": title, "format": _measure_format(doctype, measure, agg_key)}]
    shape = (chart or "bar").lower()

    if shape == "line":
        # A time series reads as a line; sort by the group so the x axis runs forward.
        ordered = sorted(rows, key=lambda r: str(r.get(group_by) or ""))
        return blocks.attach(
            payload, blocks.line(ordered, x=group_by, series=series), blocks.table(ordered, columns=columns)
        )
    if shape == "none":
        return blocks.attach(payload, blocks.table(rows, columns=columns))
    return blocks.attach(
        payload,
        blocks.bar(rows, x=group_by, series=series, horizontal=len(rows) > 8),
        blocks.table(rows, columns=columns),
    )


@tool(
    "count",
    "How many records match. Cheaper than read() when the user only wants a number.",
    tags=["read"],
    label="Counting Records",
)
def count(doctype: str, filters: dict | None = None) -> dict:
    if not frappe.has_permission(doctype, "read"):
        raise frappe.PermissionError(f"No permission to read {doctype}")
    total = frappe.db.count(doctype, normalize_filters(doctype, filters))
    return blocks.attach(
        {"doctype": doctype, "count": total},
        # A count is a count: without saying so, a tile labelled "Sales Invoice
        # (filtered)" was given the site's currency symbol and read "Rp 0".
        blocks.kpi(f"{doctype}{' (filtered)' if filters else ''}", total, format="number"),
    )


def _measure_format(doctype: str, measure: str | None, agg_key: str):
    """"currency" | "number", from the aggregated field's own fieldtype."""
    if agg_key == "count" or not measure:
        return "number"
    try:
        field = frappe.get_meta(doctype).get_field(measure)
    except Exception:
        return None
    if not field:
        return None
    return "currency" if field.fieldtype == "Currency" else "number"


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
