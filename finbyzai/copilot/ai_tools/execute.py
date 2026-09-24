# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``execute`` AI Tool."""

import re

import frappe

from finbyzai.copilot import blocks

from finbyzai.copilot.registry import tool

@tool(
    "execute",
    """Run a short Python script when no report or tool answers the question — a join
    across doctypes, a computation over rows, an ad-hoc grouping.

    NEVER write an import. Not `import frappe`, not anything else — the sandbox has no
    import at all and the script fails immediately. `frappe` is already defined.

    NEVER use `frappe.get_all` or `frappe.db.sql`; neither exists here, because both
    ignore permissions. Use `frappe.get_list`, which takes the same arguments.

    A correct script looks exactly like this, with no preamble:

        rows = frappe.get_list("Lead", fields=["name", "creation"],
                               filters={"creation": ["between", ["2025-01-01", "2025-12-31"]]},
                               limit=500)
        result = len(rows)

    Available names: `frappe.get_list`, `frappe.get_doc`, `frappe.get_meta`,
    `frappe.get_value`, `frappe.db.count`, `frappe.db.exists`, `frappe.utils.*`, and the
    copilot's own `read`, `aggregate`, `count`, `run_report`. No shell, no filesystem,
    no raw SQL, no writes. Assign your answer to `result`.

    Prefer a report or `aggregate` over this — it is slower and easier to get wrong.
    To change data, use create / update / run_action instead; writes do not work here.""",
    confirm=True,
    tags=["compute"],
    label="Executing",
)
def execute_tool(code: str, description: str | None = None) -> dict:
    from finbyzai.copilot.sandbox import execute_code

    if not isinstance(code, str) or not code.strip():
        raise frappe.ValidationError("`code` is required")

    out = execute_code(code)
    out["description"] = description
    # When the script's own `result` is a flat list, it deserves the same table every
    # other tool gets — titled with the sentence the model already wrote to describe
    # what it was computing, since nothing else here can say what the rows are.
    return blocks.attach(out, blocks.flat_table(out.get("result"), title=description))
