# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``remember`` AI Tool."""

import frappe

from finbyzai.copilot import access

from finbyzai.copilot.registry import tool

NOTE_LIMIT = 2000

@tool(
    "remember",
    """Save a durable fact about how this business works, so future conversations know
    it: a policy, a naming convention, a default the user always wants, a correction
    they gave you. It is written to the knowledge base and searchable afterwards.

    Only save what will still be true next month. Never save a number you just
    calculated, a one-off filter, or anything the user could look up — that is what the
    data tools are for. Keep it to one sentence, in the user's own words where possible.""",
    tags=["memory"],
    label="Remembering",
)
def remember_tool(fact: str, category: str | None = None) -> dict:
    text = (fact or "").strip()
    if not text:
        raise frappe.ValidationError("`fact` is required")
    if len(text) > NOTE_LIMIT:
        raise frappe.ValidationError(f"Keep it under {NOTE_LIMIT} characters; got {len(text)}")

    kb_name = _knowledge_base()
    if not kb_name:
        raise frappe.ValidationError(
            "No knowledge base is attached to this conversation or its agent, so there is "
            "nowhere to remember this. Ask the user to attach one in Copilot settings."
        )

    kb = frappe.get_doc("Knowledge Base", kb_name)
    if not frappe.has_permission("Knowledge Base", "write", doc=kb):
        raise frappe.PermissionError(f"No permission to add to {kb_name}")

    note = f"[{category.strip()}] {text}" if category else text
    kb.append("notes", {"note": note, "added_by": frappe.session.user, "is_processed": 0})
    kb.save(ignore_permissions=True)

    # finbyzai's pipeline embeds unprocessed notes; queue it so recall works soon.
    frappe.enqueue(
        "finbyzai.ai.doctype.knowledge_base.knowledge_base.process_knowledge_base",
        queue="long",
        enqueue_after_commit=True,
        kb_name=kb_name,
    )

    return {
        "remembered": note,
        "knowledge_base": kb_name,
        "note": "Saved. It becomes searchable once the knowledge base finishes embedding it.",
    }

def _knowledge_base() -> str | None:
    """The conversation's knowledge base, else its agent's."""
    context = frappe.flags.get("copilot") or {}
    conversation = context.get("conversation")
    if conversation:
        access.require_read("Copilot Conversation", conversation, label="conversation")
        chosen = frappe.db.get_value("Copilot Conversation", conversation, ["knowledge_base", "agent"], as_dict=True)
        if chosen and chosen.knowledge_base:
            return chosen.knowledge_base
        if chosen and chosen.agent:
            return frappe.db.get_value("AI Agent", chosen.agent, "knowledge_base")
    return None
