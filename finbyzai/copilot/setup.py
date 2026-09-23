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
    _ensure_role_permissions()
    _ensure_agent()
    _ensure_suggestion_agent()
    sync_ai_tools()
    _attach_exported_tools()
    _ensure_settings()


# The roles a Copilot Admin and a Copilot User need on the records the panel reads.
# Shipped in each DocType's own JSON as well — but a DocType that has any Custom
# DocPerm row ignores its standard permissions entirely, and on a site where someone
# has opened the Role Permissions Manager that is exactly what happens. AI Agent on
# this site is in that state, which silently left both roles with no access at all.
# Everything the Copilot's own pickers and settings read. An admin configures them;
# a user only ever reads them, which is what the composer's dropdowns need.
# Child tables (AI Agent Tool, Knowledge Document) are deliberately absent: Frappe
# grants no permission on them independently, they inherit the parent's.
_CONFIG_DOCTYPES = (
    "AI Agent",
    "AI Tool",
    "LLM",
    "LLM Provider",
    "Knowledge Base",
)

ROLE_PERMISSIONS = {
    "Copilot Admin": {
        **{dt: ("read", "write", "create", "delete") for dt in _CONFIG_DOCTYPES},
        "Copilot Settings": ("read", "write"),
        "Copilot User Settings": ("read", "write", "create", "delete"),
    },
    "Copilot User": {
        **{dt: ("read",) for dt in _CONFIG_DOCTYPES},
        # Their own row only — the document-level hook still confines them to it.
        "Copilot User Settings": ("read", "write", "create"),
    },
}


def _ensure_role_permissions():
    from frappe.permissions import add_permission, update_permission_property

    for role, doctypes in ROLE_PERMISSIONS.items():
        if not frappe.db.exists("Role", role):
            continue
        for doctype, rights in doctypes.items():
            if not frappe.db.exists("DocType", doctype):
                continue
            # Only reach for Custom DocPerm where the DocType is already customised;
            # everywhere else the JSON permission is live and adding a custom row
            # would needlessly freeze a copy of today's standard permissions.
            if not frappe.db.exists("Custom DocPerm", {"parent": doctype}):
                continue
            if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
                add_permission(doctype, role, 0)
            for right in rights:
                update_permission_property(doctype, role, 0, right, 1, validate=False)

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


SUGGESTION_AGENT = "Copilot Suggestions"

# The standing instruction for the agent that writes each user's starter prompts.
# It lives on the agent rather than in code so it can be read and tuned from the desk
# like every other agent on the site — and seeing it in the AI Agent list is the point.
SUGGESTION_PROMPT = """You write starter prompts for a business assistant inside an ERP.

You are given questions one person actually asked it. Write exactly 3 prompts they
would plausibly ask again.

Rules: one per line, no numbering, no quotes, no preamble. Each under 70 characters.
Keep their subject matter and vocabulary - if they ask about leads, write about leads.
Make each one a complete question that stands alone, not a follow-up. Do not invent
record names, people or numbers that are not in their questions."""


def _ensure_suggestion_agent():
    """A real AI Agent for the starter-prompt job, so it is visible and editable.

    It is not the chat agent: no tools, one iteration, low temperature, and a prompt
    of its own. Giving it its own record means an admin can change how prompts are
    written, or point it at a cheaper model, without a deploy.
    """
    if frappe.db.exists("AI Agent", SUGGESTION_AGENT):
        return

    doc = frappe.get_doc(
        {
            "doctype": "AI Agent",
            "title": SUGGESTION_AGENT,
            "agent_type": "Conversational Agent",
            "llm": _free_model() or _default_llm(),
            # One short call, and nothing is waiting on it.
            "max_iterations": 1,
            "temperature": 0.3,
            "enable_memory": 0,
        }
    )
    doc.append("messages", {"type": "system", "content_type": "text", "content": SUGGESTION_PROMPT})
    doc.insert(ignore_permissions=True)


def _free_model():
    """Prefer a free model: this job runs for every user, on a schedule, unattended.

    LLM has `enabled`, not `disabled` — getting that wrong is what stopped this agent
    being created the first time, and the failure was swallowed by the migrate hook.
    """
    # The chat agent's own model first, when it is already a free one: it is known to
    # work on this site, where picking alphabetically landed on a model that is
    # rate-limited upstream and produced nothing.
    chat = frappe.db.get_value("AI Agent", AGENT, "llm")
    if chat and chat.endswith(":free"):
        return chat
    return frappe.db.get_value(
        "LLM",
        {"name": ("like", "%:free"), "is_embedding_model": 0, "enabled": 1},
        "name",
        order_by="name",
    )


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
