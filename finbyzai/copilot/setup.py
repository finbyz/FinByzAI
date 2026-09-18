# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Ships the Copilot as part of finbyzai itself, not as a manual, per-site setup step.

Runs on every migrate. Everything here is idempotent — safe to re-run, and the only
way a fresh finbyzai install ends up with a working "Copilot" AI Agent, its exported
read-only tools synced onto it, and Copilot Settings pointed at it, with no DB record
that has to be hand-copied from one site to another to get a working chat panel.

A client app (like Nayla) layers its own domain tools onto this agent separately —
that wiring is the client's, and stays out of finbyzai on purpose.
"""

import frappe

from finbyzai.copilot.exported import EXPORTED, sync_ai_tools

AGENT = "Copilot"


def ensure_defaults():
    _ensure_agent()
    sync_ai_tools()
    _attach_exported_tools()
    _ensure_settings()


def _ensure_agent():
    if frappe.db.exists("AI Agent", AGENT):
        return
    frappe.get_doc(
        {
            "doctype": "AI Agent",
            "title": AGENT,
            "agent_type": "ReAct Agent",
            "llm": _default_llm(),
            "max_iterations": 25,
        }
    ).insert(ignore_permissions=True)


def _default_llm():
    """Best-effort: any LLM record fixture-installed on this site. Left blank rather
    than guessed wrong — the runner picks its model from Copilot Settings at call
    time regardless, so this only matters to the older AgentService path."""
    return frappe.db.get_value("LLM", {}, "name", order_by="name")


def _attach_exported_tools():
    """The exported read-only tools, attached to the Copilot agent's own tool list —
    the same rows sync_ai_tools() just created or refreshed."""
    doc = frappe.get_doc("AI Agent", AGENT)
    have = {row.tool for row in doc.tools or []}
    missing = [name for name in EXPORTED if name not in have and frappe.db.exists("AI Tool", name)]
    if not missing:
        return
    for name in missing:
        doc.append("tools", {"tool": name})
    doc.save(ignore_permissions=True)


def _ensure_settings():
    """Defaults apply only the first time this Single is ever saved. After that —
    even if every field is still at its default — an admin's own choice (including
    turning Copilot off) must survive every later migrate untouched.
    """
    if frappe.db.count("Singles", {"doctype": "Copilot Settings"}):
        return
    settings = frappe.get_single("Copilot Settings")
    settings.default_agent = settings.default_agent or AGENT
    settings.enabled = 1
    settings.save(ignore_permissions=True)
