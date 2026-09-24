# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``aggregate`` AI Tool."""

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

AGG_LIMIT = 200

AGGREGATIONS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}

_FIELDNAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_STANDARD_FIELDS = frozenset(
    {
        "name", "owner", "creation", "modified", "modified_by",
        "docstatus", "idx", "parent", "parenttype", "parentfield",
    }
)

def _safe_fieldname(doctype: str, fieldname: str | None, label: str) -> str:
    name = str(fieldname or "").strip()
    if not _FIELDNAME.match(name):
        raise frappe.ValidationError(f"`{label}` must be a plain fieldname, got {fieldname!r}")
    if name not in _STANDARD_FIELDS and not frappe.get_meta(doctype).get_field(name):
        raise frappe.ValidationError(f"{doctype} has no field {name!r} (passed as `{label}`)")
    return name

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

DATE_PARTS = frozenset(("year", "month", "quarter", "week", "day", "date"))

_CALL = re.compile(r"^\s*(year|month|quarter|week|day|date)\s*\(\s*([a-z_][a-z0-9_]*)\s*\)\s*$", re.I)

def _group_expression(doctype: str, group_by: str) -> tuple[str, str]:
    """Return (sql expression, alias) for a group_by that may name a date part.

    Accepts a plain fieldname, `YEAR(creation)`, or `year:creation`. The alias is
    always a safe identifier, so the caller can order and read by it.
    """
    raw = str(group_by or "").strip()

    part = None
    field = raw
    match = _CALL.match(raw)
    if match:
        part, field = match.group(1).lower(), match.group(2)
    elif ":" in raw:
        head, _, tail = raw.partition(":")
        if head.strip().lower() in DATE_PARTS:
            part, field = head.strip().lower(), tail.strip()

    field = _safe_fieldname(doctype, field, "group_by")
    if not part:
        return field, field

    meta_field = frappe.get_meta(doctype).get_field(field)
    fieldtype = meta_field.fieldtype if meta_field else "Datetime"
    if field not in ("creation", "modified") and fieldtype not in ("Date", "Datetime", "Datetime "):
        raise frappe.ValidationError(
            f"`{field}` is a {fieldtype}, so it cannot be grouped by {part}. "
            "Use a date field, or group by the field itself."
        )
    return _date_part_expression(part, field), part

def _date_part_expression(part: str, field: str) -> str:
    """Build a chronological period key for the active database."""
    if part in ("day", "date"):
        return f"DATE({field})"

    if frappe.db.db_type == "postgres":
        year_unit = "isoyear" if part == "week" else "year"
        year = f"DATE_PART('{year_unit}', {field})"
        unit = f"DATE_PART('{part}', {field})"
    else:
        if part == "week":
            return f"YEARWEEK({field}, 3)"
        year = f"YEAR({field})"
        unit = f"{part.upper()}({field})"

    if part == "year":
        return year
    multiplier = 10 if part == "quarter" else 100
    return f"{year} * {multiplier} + {unit}"

def _format_period_rows(rows: list[dict], part: str) -> list[dict]:
    if part not in DATE_PARTS:
        return rows
    return [{**row, part: _period_label(part, row.get(part))} for row in rows]

def _period_label(part: str, value):
    if value is None or part in ("day", "date"):
        return value

    number = int(value)
    if part == "year":
        return number
    if part == "quarter":
        year, quarter = divmod(number, 10)
        return f"{year:04d}-Q{quarter}"

    year, period = divmod(number, 100)
    marker = "W" if part == "week" else ""
    return f"{year:04d}-{marker}{period:02d}"

@tool(
    "aggregate",
    """Grouped totals without writing SQL: e.g. total sales per customer is
    aggregate("Sales Invoice", group_by="customer", measure="base_grand_total",
    agg="sum"). `agg` is one of sum, count, avg, min, max. Returns rows sorted by the
    aggregate, largest first — use it for "top N" and "per month" questions. The user is
    shown a chart and a table of the result automatically. `chart` picks the shape:
    "bar" (default, for rankings), "line" (for anything over time — months, dates), or
    "none" for a table only.

    For anything per year / per month / per quarter, group by a period, not the raw
    date: group_by="year:creation", "month:creation", "quarter:creation",
    "week:creation" or "day:creation" (YEAR(creation) is accepted too). Grouping by a
    bare datetime gives one group per timestamp and answers nothing.

    This is the right tool for "how many X per year", "top projects by hours", "totals
    by status". Do not fetch the rows with read() and count them yourself.
    """,
    tags=["read"],
    label="Grouping Records",
)
def aggregate_tool(
    doctype: str,
    group_by: str,
    measure: str | None = None,
    agg: str = "sum",
    filters: dict | None = None,
    limit: int = 20,
    ascending: bool = False,
    chart: str = "bar",
) -> dict:
    access.require_permission(doctype, "read")
    agg_key = (agg or "sum").lower()
    if agg_key not in AGGREGATIONS:
        raise frappe.ValidationError(f"agg must be one of {', '.join(AGGREGATIONS)}, got {agg!r}")
    if agg_key != "count" and not measure:
        raise frappe.ValidationError(f"`measure` is required for agg={agg_key!r} (the field to total)")

    limit = max(1, min(int(limit or 20), AGG_LIMIT))
    conditions, scope = scope_to_live(doctype, normalize_filters(doctype, filters))
    alias = "value"
    expression, group_by = _group_expression(doctype, group_by)
    if agg_key == "count":
        function = f"count(name) as {alias}"
    else:
        measure = _safe_fieldname(doctype, measure, "measure")
        function = f"{AGGREGATIONS[agg_key].lower()}({measure}) as {alias}"

    # A period reads forward in time; everything else reads biggest-first.
    periodic = expression != group_by
    order = f"{group_by} asc" if periodic else f"{alias} {'asc' if ascending else 'desc'}"

    rows = frappe.get_list(
        doctype,
        filters=conditions,
        fields=[f"{expression} as {group_by}" if periodic else expression, function],
        group_by=group_by if periodic else expression,
        order_by=order,
        limit=limit,
    )
    rows = _format_period_rows(rows, group_by)

    payload = {
        "doctype": doctype,
        "group_by": group_by,
        "measure": measure,
        "agg": agg_key,
        "rows": rows,
        "count": len(rows),
        "scope": scope,
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
        return blocks.attach(
            payload,
            blocks.line(rows, x=group_by, series=series),
            blocks.table(rows, columns=columns, title=title),
        )
    if shape == "none":
        return blocks.attach(payload, blocks.table(rows, columns=columns, title=title))
    return blocks.attach(
        payload,
        blocks.bar(rows, x=group_by, series=series, horizontal=len(rows) > 8),
        blocks.table(rows, columns=columns, title=title),
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
