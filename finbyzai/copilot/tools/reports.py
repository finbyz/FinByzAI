# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Report tools — the highest-value tools in this app.

This site has 222 Report records (201 Script, 13 Query, 8 Report Builder), and every
question the client asked for already exists as one: Sales Analytics, Sales Order
Analysis, Item-wise Sales History, Customer Ledger Summary, Accounts Receivable,
Gross Profit, Stock Ageing, Stock Projected Qty, Item Shortage Report, General Ledger.

Those reports are written, tested by ERPNext and permission-aware. Running one beats
having the model rediscover the schema and generate its own query — that is exactly
what failed six times in a row in the Flow transcript.

`describe_report` is what makes the "required filter" mistake self-correcting: the
model reads the real filter list, sees `reqd: True`, and asks the user for the value
instead of guessing. Standard Script Reports keep their filters only in the report's
JavaScript (on this site 221 of 222 have no Report Filter rows), so we parse them out.
"""

import re

import frappe

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

# Rows handed to the model. The UI block carries the full set — a 3,000-row report
# must never enter the context window.
PREVIEW_ROWS = 30
BLOCK_ROWS = 500
NUMERIC_FIELDTYPES = {"Currency", "Float", "Int", "Percent"}


@tool(
    "list_reports",
    """Find existing reports. ALWAYS try this before writing your own query: this site
    has 222 ready-made, permission-aware reports and one of them usually answers the
    question exactly. Search by words in the report name, or list every report for a
    DocType with `ref_doctype`.""",
    tags=["reports"],
    label="Finding Reports",
)
def list_reports(
    search: str | None = None,
    ref_doctype: str | None = None,
    module: str | None = None,
    limit: int = 25,
) -> dict:
    limit = max(1, min(int(limit or 25), 100))
    filters = {"disabled": 0}
    if search:
        filters["name"] = ("like", f"%{search.strip()}%")
    if ref_doctype:
        filters["ref_doctype"] = ref_doctype
    if module:
        filters["module"] = module

    rows = frappe.get_all(
        "Report",
        filters=filters,
        fields=["name", "report_type", "ref_doctype", "module"],
        limit=limit * 5,
    )
    rows.sort(key=lambda r: len(r.name or ""))

    out = []
    for row in rows:
        if row.ref_doctype and not frappe.has_permission(row.ref_doctype, "report"):
            continue
        out.append(
            {
                "report": row.name,
                "type": row.report_type,
                "doctype": row.ref_doctype,
                "module": row.module,
            }
        )
        if len(out) >= limit:
            break

    return {"reports": out, "count": len(out)}


@tool(
    "describe_report",
    """The filters a report accepts, before you run it. Check `required` — if a required
    filter has no default and you cannot infer it from the conversation, ask the user
    rather than guessing. Never invent a filter fieldname.""",
    tags=["reports"],
    label="Reading Report Filters",
)
def describe_report(report: str) -> dict:
    doc = frappe.get_doc("Report", report)
    if doc.ref_doctype and not frappe.has_permission(doc.ref_doctype, "report"):
        raise frappe.PermissionError(f"No report permission for {doc.ref_doctype}")

    filters, source = _report_filters(doc)
    return {
        "report": doc.name,
        "type": doc.report_type,
        "doctype": doc.ref_doctype,
        "filters": filters,
        "filters_source": source,
        "hint": "Could not read this report's filters. Call run_report and read the error "
        "message it returns — it names what is missing."
        if source == "unknown"
        else None,
    }


@tool(
    "run_report",
    """Run a report with filters and get its result. Call describe_report first so you
    pass real filter fieldnames. You receive the columns, the row count, column totals
    and the first rows — the user sees the full table in the chat, so summarize and
    interpret rather than listing every row back.""",
    tags=["reports"],
    label="Running Report",
)
def run_report(report: str, filters: dict | None = None, limit: int = BLOCK_ROWS) -> dict:
    from frappe.desk.query_report import run as run_query_report

    limit = max(1, min(int(limit or BLOCK_ROWS), BLOCK_ROWS))
    filters = _apply_filter_defaults(report, dict(filters or {}))
    result = run_query_report(report, filters=filters, are_default_filters=False)

    columns = _normalize_columns(result.get("columns") or [])
    rows = _normalize_rows(result.get("result") or [], columns)

    payload = {
        "report": report,
        "filters": filters,
        "columns": [{"key": c["key"], "label": c["label"], "type": c["type"]} for c in columns],
        "row_count": len(rows),
        "rows": rows[:PREVIEW_ROWS],
        "truncated": len(rows) > PREVIEW_ROWS,
        "totals": _totals(rows, columns),
    }
    if result.get("message"):
        payload["message"] = frappe.utils.strip_html(str(result["message"]))[:500]

    # Full rows go to the panel, not into the model's context.
    return blocks.attach(
        payload,
        blocks.table(rows[:limit], columns=[{"key": c["key"], "label": c["label"]} for c in columns]),
    )


def _apply_filter_defaults(report: str, filters: dict) -> dict:
    """Fill in the report's own literal defaults, then refuse to run if a required
    filter is still unset.

    Without this, a missing filter surfaces as whatever the report's Python happens to
    do with None — `KeyError: 'to_date'`, `'NoneType' object has no attribute 'split'` —
    which tells the model nothing. Raising MandatoryError in Frappe's own
    "[scope]: field1, field2" format means the guard hands the model
    {"fields": [...], "hint": "add every field listed and retry"} instead.
    """
    spec, source = _report_filters(frappe.get_doc("Report", report))
    if source == "unknown":
        return filters

    for entry in spec:
        fieldname = entry.get("fieldname")
        if fieldname and fieldname not in filters and entry.get("default") is not None:
            filters[fieldname] = entry["default"]

    missing = [
        entry["fieldname"]
        for entry in spec
        if entry.get("required") and not filters.get(entry["fieldname"])
    ]
    if missing:
        raise frappe.MandatoryError(f"[Report {report}]: {', '.join(missing)}")

    return filters


def _normalize_columns(raw: list) -> list:
    """Script Reports return columns as dicts or as "Label:Type/Options:Width" strings."""
    out = []
    for col in raw:
        if isinstance(col, dict):
            key = col.get("fieldname") or frappe.scrub(col.get("label") or "")
            out.append(
                {
                    "key": key,
                    "label": col.get("label") or frappe.unscrub(key),
                    "type": col.get("fieldtype") or "Data",
                }
            )
            continue
        parts = str(col).split(":")
        label = parts[0]
        fieldtype = parts[1].split("/")[0] if len(parts) > 1 and parts[1] else "Data"
        out.append({"key": frappe.scrub(label), "label": label, "type": fieldtype})
    return out


def _normalize_rows(raw: list, columns: list) -> list:
    """Rows come back as dicts or as positional lists depending on the report."""
    keys = [c["key"] for c in columns]
    out = []
    for row in raw:
        if isinstance(row, dict):
            out.append({k: row.get(k) for k in keys} if keys else dict(row))
        elif isinstance(row, list | tuple):
            out.append(dict(zip(keys, row, strict=False)))
    return out


def _totals(rows: list, columns: list) -> dict:
    """Column totals so the model can state the bottom line without reading every row."""
    totals = {}
    for col in columns:
        if col["type"] not in NUMERIC_FIELDTYPES:
            continue
        values = [row.get(col["key"]) for row in rows]
        numbers = [v for v in values if isinstance(v, int | float)]
        if numbers:
            totals[col["key"]] = round(sum(numbers), 2)
    return totals


# ── filter discovery ──────────────────────────────────────────────────────────
# Report Filter rows first (only 1 of 222 reports on this site uses them), then the
# report's JavaScript, which is where every standard Script Report keeps its filters.

_FILTER_ARRAY = re.compile(r"filters\s*:\s*\[", re.S)
_FIELDNAME = re.compile(r"""fieldname\s*:\s*["']([^"']+)["']""")
_LABEL = re.compile(r"""label\s*:\s*(?:__\(\s*)?["']([^"']+)["']""")
_FIELDTYPE = re.compile(r"""fieldtype\s*:\s*["']([^"']+)["']""")
_OPTIONS = re.compile(r"""options\s*:\s*["']([^"']+)["']""")
_REQD = re.compile(r"reqd\s*:\s*(1|true)")
_DEFAULT_LITERAL = re.compile(r"""default\s*:\s*["']([^"']*)["']""")
_DEFAULT_NUMBER = re.compile(r"default\s*:\s*(-?\d+(?:\.\d+)?)\s*[,}]")
_DEFAULT_DYNAMIC = re.compile(r"default\s*:")


def _report_filters(doc) -> tuple:
    if doc.filters:
        return (
            [
                {
                    "fieldname": f.fieldname,
                    "label": f.label,
                    "fieldtype": f.fieldtype,
                    "options": f.options,
                    "required": bool(f.mandatory),
                    "default": f.default,
                }
                for f in doc.filters
            ],
            "report_filter_rows",
        )

    parsed = _filters_from_script(doc.name)
    return (parsed, "script") if parsed else ([], "unknown")


def _filters_from_script(report_name: str) -> list:
    from frappe.desk.query_report import get_script

    try:
        script = (get_script(report_name) or {}).get("script") or ""
    except Exception:
        return []

    array = _balanced(script, _FILTER_ARRAY, "[", "]")
    if not array:
        return []

    out = []
    for block in _objects(array):
        fieldname = _FIELDNAME.search(block)
        if not fieldname:
            continue
        entry = {"fieldname": fieldname.group(1)}
        for key, pattern in (("label", _LABEL), ("fieldtype", _FIELDTYPE), ("options", _OPTIONS)):
            match = pattern.search(block)
            if match:
                entry[key] = match.group(1)
        entry["required"] = bool(_REQD.search(block))
        literal = _DEFAULT_LITERAL.search(block) or _DEFAULT_NUMBER.search(block)
        if literal:
            entry["default"] = literal.group(1)
        elif _DEFAULT_DYNAMIC.search(block):
            # An expression like frappe.datetime.get_today(): the desk computes it, we
            # can't. Flagged so run_report knows to ask for it rather than send garbage.
            entry["dynamic_default"] = True
        out.append(entry)
    return out


def _balanced(text: str, start_pattern, open_char: str, close_char: str):
    """The bracketed span that `start_pattern` opens, brackets balanced."""
    match = start_pattern.search(text)
    if not match:
        return None
    start = text.rfind(open_char, match.start(), match.end())
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_char:
            depth += 1
        elif text[i] == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _objects(array_text: str) -> list:
    """Top-level {...} blocks inside a JS array literal. Brace-balanced, which also
    steps over nested get_query/arrow-function bodies."""
    out = []
    depth = 0
    start = None
    for i, char in enumerate(array_text):
        if char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                out.append(array_text[start : i + 1])
                start = None
    return out
