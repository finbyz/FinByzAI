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

import frappe

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

ROW_LIMIT = 200
AGG_LIMIT = 200
# Rows handed to the model. The table block carries all of them to the panel — a
# 200-row read should not spend 200 rows of context.
PREVIEW_ROWS = 20
AGGREGATIONS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}


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
    kwargs = {
        "filters": filters or {},
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
    alias = "value"
    function = {AGGREGATIONS[agg_key]: "*" if agg_key == "count" else measure, "as": alias}

    rows = frappe.get_list(
        doctype,
        filters=filters or {},
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
    columns = [group_by, {"key": alias, "label": title}]
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
    total = frappe.db.count(doctype, filters or {})
    return blocks.attach(
        {"doctype": doctype, "count": total},
        blocks.kpi(f"{doctype}{' (filtered)' if filters else ''}", total),
    )


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
