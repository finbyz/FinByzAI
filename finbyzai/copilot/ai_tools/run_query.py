# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``run_query`` AI Tool."""

import re

import frappe

from finbyzai.copilot import blocks

from finbyzai.copilot.registry import tool

SQL_ROW_LIMIT = 500

SQL_PREVIEW_ROWS = 30

_MULTI_STATEMENT = re.compile(r";\s*\S")

_LIMIT_PRESENT = re.compile(r"\blimit\b\s+\d+", re.IGNORECASE)

@tool(
    "run_query",
    """Run a read-only SQL SELECT when the question cannot be expressed any other way.

    Restrictions: SELECT (or a read-only WITH) only, one statement, and a LIMIT is
    added if you omit one. Requires the System Manager role.

    Important: raw SQL bypasses Frappe's row-level permissions, so it reads whatever
    the database holds. Use `read` / `aggregate` / `run_report` whenever they can
    answer the question — they respect the user's permissions and this does not.""",
    confirm=True,
    tags=["compute"],
    label="Running Query",
)
def run_query_tool(sql: str, description: str | None = None, limit: int = SQL_ROW_LIMIT) -> dict:
    from frappe.utils.safe_exec import check_safe_sql_query

    if frappe.session.user != "Administrator":
        raise frappe.PermissionError(
            "Raw SQL cannot enforce your data permissions. Use read, aggregate or run_report instead."
        )

    query = (sql or "").strip().rstrip(";").strip()
    if not query:
        raise frappe.ValidationError("`sql` is required")
    if _MULTI_STATEMENT.search(query):
        raise frappe.ValidationError("One statement per call — remove the extra `;`.")

    # SELECT / EXPLAIN / read-only WITH only; raises PermissionError otherwise.
    check_safe_sql_query(query)

    limit = max(1, min(int(limit or SQL_ROW_LIMIT), SQL_ROW_LIMIT))
    if not _LIMIT_PRESENT.search(query):
        query = f"{query} LIMIT {limit}"

    rows = frappe.db.sql(query, as_dict=True)
    columns = list(rows[0].keys()) if rows else []

    payload = {
        "sql": query,
        "description": description,
        "columns": columns,
        "row_count": len(rows),
        "rows": rows[:SQL_PREVIEW_ROWS],
        "truncated": len(rows) > SQL_PREVIEW_ROWS,
        "note": "Raw SQL result — not filtered by the user's record-level permissions.",
    }
    return blocks.attach(payload, blocks.table(rows[:limit], columns=columns, title=description))
