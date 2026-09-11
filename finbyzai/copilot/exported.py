# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""The copilot's read-only tools, exported as `AI Tool` records.

Two things run agents on this bench: the Copilot (this package's runner) and
finbyzai's own `AgentService`, which the AI Digest email uses. The runner reads its
tools from the Python registry; AgentService reads them from `AI Tool` records. So a
tool that should work in both places needs an entry in both — and these wrappers are
that entry, without a second implementation.

Every wrapper goes through `registry.call`, which means the guard applies on both
paths: a savepoint per call, and a failure returned as data the model can read instead
of an exception that ends the run.

**Only read-only tools are exported, deliberately.** `create`, `update`, `delete`,
`run_action`, `execute` and `run_query` are approval-gated, and the gate lives in the
runner's loop — AgentService has no way to pause and ask. Exporting them would give
the digest agent a silent, unattended path to writing and to raw SQL. `ask_user` needs
the same pause machinery, and `visualize` / `remember` need the run context the runner
sets. Those six stay Copilot-only.
"""

import json

import frappe

from finbyzai.copilot import registry

# The tools worth having in both places, and safe to run unattended.
EXPORTED = (
    "find_doctypes",
    "describe",
    "read",
    "aggregate",
    "count",
    "list_reports",
    "describe_report",
    "run_report",
    "extract_file_content",
)

RESULT_CHARS = 12000


def _run(name: str, **arguments) -> str:
    """Call a registry tool through the guard and return JSON for the model."""
    outcome = registry.call(name, {k: v for k, v in arguments.items() if v is not None})
    payload = outcome.get("result") if outcome.get("ok") else outcome
    if isinstance(payload, dict):
        payload.pop("_blocks", None)  # UI blocks mean nothing outside the panel
    return json.dumps(payload, indent=2, default=str)[:RESULT_CHARS]


def find_doctypes(search: str, module: str | None = None, limit: int = 20) -> str:
    """Find the exact DocType name for something described in words. Use it before any
    other tool when a DocType name might not be exactly right."""
    return _run("find_doctypes", search=search, module=module, limit=limit)


def describe(doctype: str, name: str | None = None) -> str:
    """Inspect a DocType: its real fieldnames, your permissions, and — with a record
    name — the actions available on that record."""
    return _run("describe", doctype=doctype, name=name)


def read(
    doctype: str,
    filters: dict | None = None,
    fields: list | None = None,
    order_by: str | None = None,
    limit: int = 50,
) -> str:
    """Read records with the caller's own permissions. Confirm fieldnames with
    describe first. filters is a dict like {"status": "Overdue"}."""
    return _run("read", doctype=doctype, filters=filters, fields=fields, order_by=order_by, limit=limit)


def aggregate(
    doctype: str,
    group_by: str,
    measure: str | None = None,
    agg: str = "sum",
    filters: dict | None = None,
    limit: int = 20,
) -> str:
    """Grouped totals without writing SQL: total per customer, count per month, and so
    on. agg is one of sum, count, avg, min, max."""
    return _run(
        "aggregate", doctype=doctype, group_by=group_by, measure=measure, agg=agg, filters=filters, limit=limit
    )


def count(doctype: str, filters: dict | None = None) -> str:
    """How many records match. Cheaper than read when only the number is wanted."""
    return _run("count", doctype=doctype, filters=filters)


def list_reports(search: str | None = None, ref_doctype: str | None = None, limit: int = 25) -> str:
    """Find existing reports. Try this before writing a query: this site has hundreds
    of tested, permission-aware reports and one usually answers the question."""
    return _run("list_reports", search=search, ref_doctype=ref_doctype, limit=limit)


def describe_report(report: str) -> str:
    """The filters a report accepts, and which of them are required, before running it."""
    return _run("describe_report", report=report)


def run_report(report: str, filters: dict | None = None, limit: int = 500) -> str:
    """Run a report with filters and get its columns, rows and totals."""
    return _run("run_report", report=report, filters=filters, limit=limit)


def extract_file_content(file: str, limit: int = 20000) -> str:
    """Read an attachment the user shared: pdf, docx, xlsx, csv, pptx, html or text."""
    return _run("extract_file_content", file=file, limit=limit)


# ── registration ─────────────────────────────────────────────────────────────


def sync_ai_tools(module: str = "Copilot") -> dict:
    """Create or refresh one `AI Tool` record per exported tool.

    Idempotent, and safe to re-run after a description changes. `requires_confirmation`
    is left alone on an existing record so a site that chose to gate one keeps its
    choice.
    """
    registry.load_tools()
    created, updated = [], []

    for name in EXPORTED:
        spec = registry.TOOLS.get(name)
        if not spec:
            continue
        description = " ".join((spec["description"] or "").split())[:500]
        path = f"finbyzai.copilot.exported.{name}"

        if frappe.db.exists("AI Tool", name):
            doc = frappe.get_doc("AI Tool", name)
            doc.tool_function, doc.description = path, description
            doc.module, doc.is_custom, doc.from_package = module, 1, "langchain_community.tools"
            doc.save(ignore_permissions=True)
            updated.append(name)
        else:
            frappe.get_doc(
                {
                    "doctype": "AI Tool",
                    "__newname": name,
                    "tool_function": path,
                    "description": description,
                    "module": module,
                    "is_custom": 1,
                    "from_package": "langchain_community.tools",
                    "requires_confirmation": 0,
                }
            ).insert(ignore_permissions=True)
            created.append(name)

    return {"created": created, "updated": updated}
