# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Letting the agent decide to draw something.

Every data tool already renders its natural visual — `read` a table, `aggregate` a
chart, `count` a KPI card. This tool is for when the agent wants a *different* view of
data it has already fetched: the top-line number as a KPI card, a monthly total as a
line instead of a bar, two columns of a report as their own chart.

The one rule: **the agent chooses the shape, never the numbers.** `from_call` names a
tool call earlier in this same conversation and the rows are read back out of what that
call actually returned. A model asked to retype a column will quietly invent values, so
it is never given the chance — there is no `rows` argument.
"""

import json

import frappe

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

KINDS = ("kpi", "table", "bar", "line", "records")
AGGREGATIONS = ("sum", "count", "avg", "min", "max")


@tool(
    "visualize",
    """Draw data you already fetched in a different shape. `from_call` is the id of an
    earlier tool call in this conversation (read, aggregate, run_report, run_query …)
    and the rows come from that call's own result — you cannot pass rows yourself.

    kind="kpi"    one headline number: `value_column` plus `agg` (sum/count/avg/min/max)
    kind="bar"    ranking: `x` is the label column, `series` the numeric column(s)
    kind="line"   over time: same as bar, sorted by `x`
    kind="table"  a table, optionally narrowed to `columns`
    kind="records"a list of records linking into the desk (needs a `doctype` column)

    Use it when the automatic visual isn't the clearest one — a KPI card for "what's the
    total", a line for anything monthly, a small table when a chart would be noise. Say
    in your reply what the visual shows; do not repeat every row.""",
    tags=["visualize"],
    label="Drawing",
)
def visualize(
    from_call: str,
    kind: str = "table",
    x: str | None = None,
    series: list | None = None,
    columns: list | None = None,
    value_column: str | None = None,
    agg: str = "sum",
    label: str | None = None,
    horizontal: bool = False,
) -> dict:
    shape = (kind or "table").lower()
    if shape not in KINDS:
        raise frappe.ValidationError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")

    rows, source = _rows_of(from_call)
    if not rows:
        raise frappe.ValidationError(
            f"Call {from_call} returned no rows to draw. Fetch the data first, then visualize it."
        )

    available = list(rows[0].keys())

    if shape == "kpi":
        block = _kpi(rows, value_column, agg, label, available)
    elif shape == "records":
        doctype = source.get("doctype")
        if not doctype:
            raise frappe.ValidationError(
                f"Call {from_call} has no doctype, so its rows cannot be linked as records. "
                "Use kind='table' instead."
            )
        block = blocks.records(doctype, rows)
    elif shape in ("bar", "line"):
        block = _chart(shape, rows, x, series, available, horizontal)
    else:
        block = blocks.table(rows, columns=columns or None, doctype=source.get("doctype"))

    return blocks.attach(
        {
            "drawn": shape,
            "from_call": from_call,
            "rows_used": len(rows),
            "columns_available": available,
        },
        block,
    )


def _kpi(rows: list, value_column: str | None, agg: str, label: str | None, available: list):
    key = value_column or _first_numeric(rows, available)
    if not key:
        raise frappe.ValidationError(
            f"No numeric column to total. Available columns: {', '.join(available)}."
        )
    if agg not in AGGREGATIONS:
        raise frappe.ValidationError(f"agg must be one of {', '.join(AGGREGATIONS)}")

    numbers = [r.get(key) for r in rows if isinstance(r.get(key), int | float)]
    if agg == "count":
        value = len(rows)
    elif not numbers:
        raise frappe.ValidationError(f"Column {key!r} holds no numbers.")
    elif agg == "sum":
        value = round(sum(numbers), 2)
    elif agg == "avg":
        value = round(sum(numbers) / len(numbers), 2)
    elif agg == "min":
        value = min(numbers)
    else:
        value = max(numbers)

    return blocks.kpi(label or f"{agg.title()} of {frappe.unscrub(key)}", value)


def _chart(shape: str, rows: list, x: str | None, series: list | None, available: list, horizontal: bool):
    x_key = x or _first_text(rows, available) or available[0]
    keys = series or [_first_numeric(rows, available)]
    keys = [k for k in keys if k]
    if not keys:
        raise frappe.ValidationError(
            f"No numeric column to plot. Available columns: {', '.join(available)}."
        )

    spec = [{"key": k, "label": frappe.unscrub(k)} for k in keys]
    if shape == "line":
        ordered = sorted(rows, key=lambda r: str(r.get(x_key) or ""))
        return blocks.line(ordered, x=x_key, series=spec)
    return blocks.bar(rows, x=x_key, series=spec, horizontal=horizontal)


def _rows_of(call_id: str) -> tuple:
    """The rows an earlier tool call produced, read back from its persisted result.

    The table block written alongside the tool message holds every row, while the
    message content holds only the sample the model saw — so the block is the source
    of truth and a visual can cover data the model never had in context.
    """
    context = frappe.flags.get("copilot") or {}
    conversation = context.get("conversation")
    if not conversation:
        raise frappe.ValidationError("visualize can only run inside a copilot conversation.")

    row = frappe.db.get_value(
        "Copilot Message",
        {
            "parent": conversation,
            "parenttype": "Copilot Conversation",
            "role": "tool",
            "tool_call_id": call_id,
        },
        ["content", "blocks"],
        as_dict=True,
    )
    if not row:
        raise frappe.ValidationError(
            f"No tool call {call_id!r} in this conversation. Use the id of a call you made here."
        )

    for block in _json(row.blocks) or []:
        if block.get("rows"):
            return block["rows"], block
    content = _json(row.content) or {}
    return (content.get("rows") or []), content


def _first_numeric(rows: list, available: list):
    for key in available:
        if any(isinstance(r.get(key), int | float) for r in rows):
            return key
    return None


def _first_text(rows: list, available: list):
    for key in available:
        if any(isinstance(r.get(key), str) for r in rows):
            return key
    return None


def _json(value):
    if not value:
        return None
    if isinstance(value, dict | list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
