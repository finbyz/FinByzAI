# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""The escape hatches: sandboxed Python and read-only SQL.

Both ask the user for approval before they run (confirm=True), because the user is
approving something they can read — the code or the query — not a vague "the agent
wants to do something".

Order of preference the system prompt enforces: an existing report, then a fixed
tool, then `aggregate`, then `execute`, and `run_query` only when SQL is genuinely
the only way. Generated code is the slowest and least reliable path, not the first.
"""

import re

import frappe

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

SQL_ROW_LIMIT = 500
SQL_PREVIEW_ROWS = 30
SQL_ROLE = "System Manager"

_MULTI_STATEMENT = re.compile(r";\s*\S")
_LIMIT_PRESENT = re.compile(r"\blimit\b\s+\d+", re.IGNORECASE)


@tool(
    "execute",
    """Run a short Python script when no report or tool answers the question — a join
    across doctypes, a computation over rows, an ad-hoc grouping.

    The sandbox: no import, no shell, no filesystem, no raw SQL, no writes. Available
    names are `frappe.get_list`, `frappe.get_doc`, `frappe.get_meta`, `frappe.get_value`,
    `frappe.db.count`, `frappe.db.exists`, `frappe.utils.*`, and the copilot's own
    `read`, `aggregate`, `count`, `run_report`. Assign your answer to `result`.

    Prefer a report or `aggregate` over this — it is slower and easier to get wrong.
    To change data, use create / update / run_action instead; writes do not work here.""",
    confirm=True,
    tags=["compute"],
    label="Executing",
)
def execute(code: str, description: str | None = None) -> dict:
    from finbyzai.copilot.sandbox import execute_code

    if not isinstance(code, str) or not code.strip():
        raise frappe.ValidationError("`code` is required")

    out = execute_code(code)
    out["description"] = description
    # When the script's own `result` is a flat list, it deserves the same table every
    # other tool gets — titled with the sentence the model already wrote to describe
    # what it was computing, since nothing else here can say what the rows are.
    return blocks.attach(out, blocks.flat_table(out.get("result"), title=description))


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
def run_query(sql: str, description: str | None = None, limit: int = SQL_ROW_LIMIT) -> dict:
    from frappe.utils.safe_exec import check_safe_sql_query

    if SQL_ROLE not in frappe.get_roles():
        raise frappe.PermissionError(
            f"run_query needs the {SQL_ROLE} role. Use read, aggregate or run_report instead."
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
