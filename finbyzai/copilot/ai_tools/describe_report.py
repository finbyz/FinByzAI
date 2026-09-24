# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``describe_report`` AI Tool."""

import re

import frappe

from finbyzai.copilot import access, blocks

from finbyzai.copilot.registry import tool

@tool(
    "describe_report",
    """The filters a report accepts, before you run it. Check `required` — if a required
    filter has no default and you cannot infer it from the conversation, ask the user
    rather than guessing. Never invent a filter fieldname.""",
    tags=["reports"],
    label="Reading Report Filters",
)
def describe_report_tool(report: str) -> dict:
    doc = _require_report(report)

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

def _require_report(report: str):
    """Authorize the requested report and every report it delegates to."""
    doc = frappe.get_doc("Report", report)
    current = doc
    visited = set()
    while True:
        if current.name in visited:
            raise frappe.ValidationError("Circular report reference")
        visited.add(current.name)
        access.require_permission(current.ref_doctype, "read")
        access.require_permission(current.ref_doctype, "report")
        if not current.is_permitted():
            raise frappe.PermissionError(f"Your roles do not allow the report {current.name}")
        if current.disabled:
            raise frappe.PermissionError(f"Report {current.name} is disabled")
        if current.report_type != "Custom Report":
            break
        current = frappe.get_doc("Report", current.reference_report)
    return doc

_K = lambda key: r"""["']?""" + key + r"""["']?\s*:\s*"""

_FILTER_ARRAY = re.compile(_K("filters") + r"\[", re.S)

_FIELDNAME = re.compile(_K("fieldname") + r"""["']([^"']+)["']""")

_LABEL = re.compile(_K("label") + r"""(?:__\(\s*)?["']([^"']+)["']""")

_FIELDTYPE = re.compile(_K("fieldtype") + r"""["']([^"']+)["']""")

_OPTIONS = re.compile(_K("options") + r"""["']([^"']+)["']""")

_REQD = re.compile(_K("reqd") + r"(1|true)")

_DEFAULT_LITERAL = re.compile(_K("default") + r"""["']([^"']*)["']""")

_DEFAULT_NUMBER = re.compile(_K("default") + r"(-?\d+(?:\.\d+)?)\s*[,}]")

_DEFAULT_DYNAMIC = re.compile(_K("default"))

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
