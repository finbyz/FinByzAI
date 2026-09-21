# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Starter prompts written from a user's own past questions.

The empty state used to ship four fixed examples about accounts receivable. Everyone
got them, most people could not run them, and the first click was a permission error.
The honest version of that feature is not a better guess — it is to stop guessing and
read what this person actually asks.

A scheduled job takes a handful of someone's own recent questions, asks a model to
write three prompts in the same shape, and stores them against their user. Nothing
about anyone else is involved, so a suggestion can never point at data the user has
no access to: it is derived from questions they already asked.

The filtering is the part that matters. Real transcripts here are roughly half
"hello", "ok", "and?", "say it again?" — feed those in raw and the model writes
prompts about saying hello. `_worth_learning_from` drops them, and when too little is
left the job simply does not write suggestions, which is a better outcome than three
confident sentences about nothing.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe.utils import add_to_date, now_datetime

# How often a user's prompts are rewritten, and how many are kept.
REFRESH_DAYS = 3
PROMPT_COUNT = 3

# Per scheduler tick. One model call each, so this also bounds the cost and keeps a
# free-tier rate limit out of trouble.
USERS_PER_RUN = 20
QUERIES_PER_USER = 15

# Below this a message is a greeting or an acknowledgement, not a question.
MIN_QUERY_CHARS = 25

_NOISE = re.compile(
    r"^\s*(hi|hey|hello+|yo|ok(ay)?|k|thanks?|thank you|ty|yes|no|yep|yup|sure|"
    r"confirmed|done|good|nice|great|cool|and\??|what\?*|try again|again|"
    r"say it again|repeat|continue|go on|next|test(ing)?|abc|asdf)\b[\s\W]*$",
    re.I,
)



def _worth_learning_from(text: str) -> bool:
    text = (text or "").strip()
    if len(text) < MIN_QUERY_CHARS:
        return False
    return not _NOISE.match(text)


def recent_queries(user: str, limit: int = QUERIES_PER_USER) -> list[str]:
    """This user's own questions, newest first, noise removed and de-duplicated."""
    rows = frappe.db.sql(
        """
        select m.content
        from `tabCopilot Message` m
        join `tabCopilot Conversation` c on m.parent = c.name
        where c.owner = %s and m.role = 'user'
        order by m.creation desc
        limit 200
        """,
        (user,),
        as_dict=True,
    )

    picked: list[str] = []
    seen: set[str] = set()
    for r in rows:
        text = " ".join(str(r.content or "").split())
        if not _worth_learning_from(text):
            continue
        key = text.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        picked.append(text[:200])
        if len(picked) >= limit:
            break
    return picked


def _agent():
    """The AI Agent that writes the prompts.

    `Copilot Settings.suggestion_agent` when set, otherwise the "Copilot Suggestions"
    agent that ships with the app. Having a real agent record is the point: its LLM
    and its system message are what an admin edits, in the same list as every other
    agent, instead of the wording being buried in this file.
    """
    from finbyzai.copilot import runner
    from finbyzai.copilot.setup import SUGGESTION_AGENT

    name = runner.get_settings().get("suggestion_agent") or SUGGESTION_AGENT
    if not frappe.db.exists("AI Agent", name):
        return None
    return frappe.get_doc("AI Agent", name)


def _system_prompt(agent) -> str:
    """The agent's own standing instruction — its system Chat Message rows."""
    parts = []
    for row in (agent.messages if agent else None) or []:
        kind = (getattr(row, "type", "") or "").lower()
        content = (getattr(row, "content", "") or "").strip()
        if content and kind in ("system", ""):
            parts.append(content)
    return "\n\n".join(parts)


def _model(agent):
    """The agent's LLM, falling back to the Copilot's own so a misconfigured agent
    still produces something rather than silently producing nothing."""
    from finbyzai.copilot import runner

    settings = runner.get_settings()
    name = (agent.llm if agent else None) or settings.default_model
    if not name and settings.default_agent:
        name = frappe.db.get_value("AI Agent", settings.default_agent, "llm")
    return frappe.get_doc("LLM", name).llm if name else None


def generate(queries: list[str]) -> list[str]:
    """Ask the model for prompts. Returns [] rather than raising — this is a
    background nicety and must never be the reason a scheduler tick fails."""
    from langchain_core.messages import HumanMessage, SystemMessage

    agent = _agent()
    instruction = _system_prompt(agent)
    llm = _model(agent)
    if not llm or not instruction:
        # No agent, or an agent with no system message: nothing sensible to ask for.
        return []

    asked = "\n".join(f"- {q}" for q in queries)
    try:
        reply = llm.invoke(
            [SystemMessage(content=instruction), HumanMessage(content=f"Their questions:\n{asked}")]
        )
    except Exception:
        # Rate limits and provider outages are expected here; the next run retries.
        frappe.log_error("Copilot: could not generate starter prompts", frappe.get_traceback())
        return []

    text = getattr(reply, "content", "") or ""
    if isinstance(text, list):  # some providers return content blocks
        text = " ".join(str(part.get("text", part)) for part in text)

    out: list[str] = []
    for line in str(text).splitlines():
        line = line.strip().lstrip("-•*0123456789. ").strip().strip('"').strip()
        if 10 <= len(line) <= 120:
            out.append(line)
        if len(out) >= PROMPT_COUNT:
            break
    return out


def refresh_for(user: str) -> list[str]:
    """Rewrite one user's prompts. Safe to call directly; the job calls it per user."""
    queries = recent_queries(user)
    # Three prompts extrapolated from two questions is invention, not personalisation.
    if len(queries) < 3:
        return []

    prompts = generate(queries)
    if not prompts:
        return []

    _store(user, prompts)
    return prompts


def _store(user: str, prompts: list[str]) -> None:
    from finbyzai.copilot.doctype.copilot_user_settings import copilot_user_settings as store

    name = frappe.db.get_value("Copilot User Settings", {"user": user}, "name")
    if not name:
        name = store.save_for_user("", user=user)
    frappe.db.set_value(
        "Copilot User Settings",
        name,
        {
            "suggestions": json.dumps(prompts, ensure_ascii=False),
            "suggestions_updated_on": now_datetime(),
        },
        update_modified=False,
    )


def refresh_stale() -> dict:
    """Scheduler entry point: rewrite prompts for users whose set has gone stale."""
    cutoff = add_to_date(now_datetime(), days=-REFRESH_DAYS)

    # Only people who have used the Copilot recently enough to have something to learn
    # from — there is nothing to personalise for someone who has never asked anything.
    active = frappe.db.sql(
        """
        select c.owner as user, count(*) as asked
        from `tabCopilot Message` m
        join `tabCopilot Conversation` c on m.parent = c.name
        where m.role = 'user' and c.owner not in ('Administrator', 'Guest')
        group by c.owner
        having asked >= 3
        """,
        as_dict=True,
    )

    fresh = {
        r.user
        for r in frappe.get_all(
            "Copilot User Settings",
            filters={"suggestions_updated_on": [">", cutoff]},
            fields=["user"],
            limit=1000,
        )
    }

    due = [r.user for r in active if r.user not in fresh][:USERS_PER_RUN]
    for user in due:
        # One job per user: a slow or rate-limited provider then delays that user's
        # prompts, not the whole batch.
        frappe.enqueue(
            "finbyzai.copilot.suggestions.refresh_for",
            queue="long",
            job_id=f"copilot-suggestions-{user}",
            deduplicate=True,
            user=user,
        )
    return {"queued": len(due), "considered": len(active)}


def for_user(user: str | None = None) -> list[str]:
    """What the panel shows. Never raises — an empty list just means no suggestions."""
    user = user or frappe.session.user
    raw = frappe.db.get_value("Copilot User Settings", {"user": user}, "suggestions")
    try:
        prompts = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [p for p in prompts if isinstance(p, str)][:PROMPT_COUNT]
