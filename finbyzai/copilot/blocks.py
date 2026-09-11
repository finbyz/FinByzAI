# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""UI blocks — the contract between the agent and the chat panel.

The model picks the block type and which columns to map; the *rows always come from
a tool result*. Nothing here accepts numbers typed by the model, because a model
asked to retype a column will quietly invent values.

Every block is a dict the panel can render directly:

    {"type": "kpi",   "label": str, "value": num, "delta": num|None, "unit": str|None}
    {"type": "table", "columns": [{"key","label","align"}], "rows": [dict]}
    {"type": "line",  "x": str, "series": [{"key","label"}], "rows": [dict]}
    {"type": "bar",   "x": str, "series": [{"key","label"}], "rows": [dict], "horizontal": bool}
    {"type": "records", "doctype": str, "rows": [dict]}   # rows link into the desk
"""

import frappe

ROW_LIMIT = 500

# Key under which a tool hangs blocks on its result. The runner pops it, publishes each
# block to the panel, and passes the rest of the result to the model — so a 3,000-row
# table can reach the user's screen without ever entering the context window.
BLOCKS_KEY = "_blocks"


def attach(result, *block_list):
    """Attach UI blocks to a tool result. See BLOCKS_KEY."""
    if not block_list:
        return result
    result.setdefault(BLOCKS_KEY, []).extend([b for b in block_list if b])
    return result


def take(result):
    """Pop the blocks off a tool result. Returns (result_without_blocks, blocks)."""
    if not isinstance(result, dict):
        return result, []
    return result, result.pop(BLOCKS_KEY, []) or []


def kpi(label, value, delta=None, unit=None):
    block = {"type": "kpi", "label": label, "value": value}
    if delta is not None:
        block["delta"] = delta
    if unit:
        block["unit"] = unit
    return block


def table(rows, columns=None, doctype=None):
    """`columns` may be a list of keys or of {"key","label"} dicts; inferred when omitted."""
    rows = _clip(rows)
    block = {"type": "table", "columns": _columns(columns, rows), "rows": rows}
    if doctype:
        block["doctype"] = doctype
    return block


def line(rows, x, series):
    return {"type": "line", "x": x, "series": _series(series), "rows": _clip(rows)}


def bar(rows, x, series, horizontal=False):
    return {
        "type": "bar",
        "x": x,
        "series": _series(series),
        "rows": _clip(rows),
        "horizontal": bool(horizontal),
    }


def records(doctype, rows):
    """A list the panel renders as links into the desk form view."""
    return {"type": "records", "doctype": doctype, "rows": _clip(rows)}


def _clip(rows):
    rows = list(rows or [])
    return rows[:ROW_LIMIT]


def _columns(columns, rows):
    if not columns:
        columns = list(rows[0].keys()) if rows else []
    out = []
    for col in columns:
        if isinstance(col, dict):
            key = col.get("key")
            out.append({"key": key, "label": col.get("label") or _label(key), "align": col.get("align")})
        else:
            out.append({"key": col, "label": _label(col), "align": None})
    return out


def _series(series):
    out = []
    for item in series or []:
        if isinstance(item, dict):
            out.append({"key": item.get("key"), "label": item.get("label") or _label(item.get("key"))})
        else:
            out.append({"key": item, "label": _label(item)})
    return out


def _label(key):
    return frappe.unscrub(key or "")


# ── rendering results from tools that know nothing about blocks ───────────────

AUTO_TABLE_LIMIT = 3
AUTO_KPI_LIMIT = 4
AUTO_ROW_MINIMUM = 2
SUMMARY_KEYS = ("executive_summary", "summary")
SKIP_KEYS = ("period", "generated_on", "company", "basis", "note", "caveat")


def autorender(result, tool_name=None):
    """Blocks for a tool that returns plain data instead of attaching its own.

    Custom `AI Tool` functions — the ones a user writes in the desk, and the Nayla
    domain tools — return JSON or a dict. They have no reason to know about this
    module, so the runner renders them here: any list of flat records becomes a table,
    and a summary of scalars becomes KPI cards.

    Deliberately conservative. It only renders what is unambiguously tabular, caps how
    much it emits, and returns nothing at all for markdown or prose — a wrong guess
    that fills the chat with junk tables is worse than no table.
    """
    payload = _loads(result)
    if payload is None:
        return []

    out = []
    if isinstance(payload, list):
        rows = _flat_records(payload)
        return [table(rows)] if rows else []

    if not isinstance(payload, dict):
        return []

    for key in SUMMARY_KEYS:
        section = payload.get(key)
        if isinstance(section, dict):
            out.extend(_summary_cards(section))
            break

    for key, value in payload.items():
        if len(out) >= AUTO_TABLE_LIMIT + AUTO_KPI_LIMIT:
            break
        if key in SKIP_KEYS or key in SUMMARY_KEYS or not isinstance(value, list):
            continue
        rows = _flat_records(value)
        if rows:
            out.append(table(rows))

    return out


def _summary_cards(section: dict) -> list:
    """The headline numbers as KPI cards — counts and totals only, not ids or dates."""
    cards = []
    for key, value in section.items():
        if len(cards) >= AUTO_KPI_LIMIT:
            break
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if value == 0:
            continue
        cards.append(kpi(_label(key), value))
    return cards


def _flat_records(value) -> list:
    """A list of records with scalar fields, or nothing.

    Nested dicts and lists inside a row have no cell to live in, so a list holding
    them is left alone rather than rendered with "—" everywhere.
    """
    if not isinstance(value, list) or len(value) < AUTO_ROW_MINIMUM:
        return []
    rows = []
    for item in value[:ROW_LIMIT]:
        if not isinstance(item, dict) or not item:
            return []
        if any(isinstance(cell, dict | list) for cell in item.values()):
            return []
        rows.append(item)
    # A single-column list of ids reads better in prose than in a table.
    return rows if rows and len(rows[0]) > 1 else []


def _loads(value):
    if isinstance(value, dict | list):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith(("{", "[")):
        return None  # markdown or prose: not ours to render
    import json as _json

    try:
        return _json.loads(text)
    except ValueError:
        return None
