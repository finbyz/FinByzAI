# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Whitelisted endpoints the chat panel calls. Thin — the work is in runner.py.

Every endpoint that touches a run or a conversation checks ownership first. A run
name is a short hash and therefore guessable, so that check is the whole defence
between one user's conversation and another's.
"""

import json
import re

import frappe
from frappe import _

from finbyzai.copilot import access, runner
from finbyzai.copilot.doctype.copilot_user_settings import (
    copilot_user_settings as user_settings,
)

TITLE_LENGTH = 60
HISTORY_LIMIT = 500


@frappe.whitelist()
def start_run(input, conversation=None, agent=None, model=None, attachments=None, knowledge_base=None):
    """Start a turn. Creates the conversation when none is given. Returns immediately —
    the run happens in a worker and streams on `copilot:<run>`."""
    settings = runner.get_settings()
    if not settings.enabled:
        frappe.throw(_("The Copilot is turned off in Copilot Settings."))

    text = (input or "").strip()
    if not text:
        frappe.throw(_("Type something first."), title=_("Nothing to send"))

    files = _parse(attachments) or []
    conversation_doc = (
        _own_conversation(conversation)
        if conversation
        else _new_conversation(text, model, agent, knowledge_base)
    )
    selected_model = access.require_read("LLM", model, label="model") if model else None
    if selected_model and selected_model != conversation_doc.model:
        conversation_doc.db_set("model", selected_model, update_modified=False)

    start_lock = _acquire_start_lock(conversation_doc.name)
    try:
        _block_while_running(conversation_doc.name)
        run = frappe.get_doc(
            {
                "doctype": "Copilot Run",
                "conversation": conversation_doc.name,
                "status": "Running",
                "agent": conversation_doc.agent,
                "model": selected_model or conversation_doc.model,
                "input": text,
                "attachments": json.dumps(files) if files else None,
            }
        ).insert(ignore_permissions=True)

        runner._append_message(
            conversation_doc.name,
            "user",
            content=_with_attachments(text, files),
            run=run.name,
        )
        runner.enqueue_run(run.name)
    except Exception:
        _release_lock(start_lock)
        raise
    else:
        _release_lock_after_transaction(start_lock)
    return {"run": run.name, "conversation": conversation_doc.name}


@frappe.whitelist()
def approve_run(run, call_id, decision, values=None):
    """Answer the Approve/Reject card. Resumes the run in a worker."""
    doc = _own_run(run)
    if doc.status != "Paused":
        frappe.throw(_("This run is {0}, not waiting for an answer.").format(doc.status))

    choice = (decision or "").strip().lower()
    if choice not in ("approve", "reject"):
        frappe.throw(_("decision must be 'approve' or 'reject'."))

    pending = runner._json(doc.pending_call) or {}
    if call_id and pending.get("id") and call_id != pending["id"]:
        frappe.throw(_("That approval is no longer the one in progress."))

    doc.db_set(
        {
            "status": "Running",
            "decision": json.dumps({"decision": choice, "values": _parse(values) or {}}, default=str),
        },
        update_modified=False,
    )
    runner.enqueue_run(doc.name)
    return {"status": "Running"}


@frappe.whitelist()
def answer_question(run, call_id, answer):
    """Answer an `ask_user` question. The answer is delivered as that tool call's
    result, not as a new user turn — the model asked, so the model gets a reply to its
    own call and the tool-call sequence stays valid."""
    doc = _own_run(run)
    if doc.status != "Paused":
        frappe.throw(_("This run is {0}, not waiting for an answer.").format(doc.status))

    text = (answer or "").strip()
    if not text:
        frappe.throw(_("Answer cannot be empty."))

    pending = runner._json(doc.pending_call) or {}
    if pending.get("kind") != "question":
        frappe.throw(_("This run is waiting for an approval, not an answer."))
    if call_id and pending.get("id") and call_id != pending["id"]:
        frappe.throw(_("That question is no longer the one in progress."))

    doc.db_set(
        {
            "status": "Running",
            "decision": json.dumps({"decision": "answer", "answer": text}, default=str),
        },
        update_modified=False,
    )
    runner.enqueue_run(doc.name)
    return {"status": "Running"}


@frappe.whitelist()
def stop_run(run):
    """User pressed Stop. The worker checks status between steps and gives up."""
    doc = _own_run(run)
    if doc.status in ("Running", "Paused"):
        doc.db_set({"status": "Stopped", "pending_call": None, "decision": None}, update_modified=False)
        runner.publish(doc.name, {"type": "done", "status": "Stopped", "output": None})
    return {"status": doc.status}


@frappe.whitelist()
def get_run(run):
    """The run's current state, for the panel to check when realtime goes quiet.

    Realtime is one delivery path and it can fail: a run that errors in the first
    200ms can finish before the browser has finished subscribing, socketio may be down,
    or a backgrounded tab can miss events. The panel polls this while a turn is in
    flight so a finished or failed run is never left spinning.
    """
    doc = _own_run(run)
    return {
        "run": doc.name,
        "status": doc.status,
        "error": doc.error,
        "output": doc.output,
        "iterations": doc.iterations,
        "duration": doc.duration,
        "pending_call": runner._json(doc.pending_call),
        "events": _replay(doc),
    }


def _replay(doc):
    """The turn so far, as the same events the realtime channel publishes.

    Realtime is an optimisation, not the delivery mechanism. Every step of a run is
    committed as it happens — a Copilot Message per tool call, per tool result and
    per piece of the reply — so the whole turn can be reconstructed from the
    database at any moment. The panel polls this while a run is in flight, which
    means the conversation still moves step by step on a site where socketio
    cannot connect at all, instead of sitting blank until the user reloads.
    """
    rows = frappe.get_all(
        "Copilot Message",
        filters={
            "parent": doc.conversation,
            "parenttype": "Copilot Conversation",
            "run": doc.name,
        },
        fields=["role", "content", "tool_calls", "tool_call_id", "blocks", "idx"],
        order_by="idx asc",
    )

    events = []
    for row in rows:
        if row.role == "assistant":
            for call in _labelled(runner._json(row.tool_calls)) or []:
                events.append(
                    {
                        "type": "tool_started",
                        "id": call.get("id"),
                        "name": call.get("name"),
                        "label": call.get("label"),
                        "context": call.get("context"),
                        "arguments": call.get("args") or {},
                    }
                )
            if row.content:
                events.append({"type": "text", "delta": row.content})
        elif row.role == "tool":
            events.append(
                {"type": "tool_ended", "id": row.tool_call_id, "ok": True, "result": row.content}
            )
        for block in runner._json(row.blocks) or []:
            events.append({"type": "block", "block": block})
    return events


#: Seconds a freshly-enqueued run gets before it's fair game for recovery — comfortably
#: longer than the panel-open-to-worker-claim gap, far shorter than the claim's own
#: crash safety net (runner.CLAIM_TTL, ~31 minutes).
RECOVER_GRACE = 60


@frappe.whitelist()
def recover_conversation(conversation):
    """Fail runs left Running by a worker that died or a panel that went away.

    The panel calls this when it (re)opens a conversation. Without it a crashed run
    would block every later turn on that conversation, since only one turn may be in
    flight at a time. A Paused run is left alone — it is waiting for the user, not stuck.

    A run is only ever recovered when it can be shown to be dead: no worker currently
    holds its execution claim (runner.is_claimed), and it has had long enough to be
    claimed off the queue. Skipping either check would fail a run whose worker is
    still very much alive and mid-turn — the worker keeps writing to a conversation
    the panel now believes is free, and a new turn started on top of it interleaves
    both runs' messages into one transcript.
    """
    doc = _own_conversation(conversation)
    candidates = frappe.get_all(
        "Copilot Run",
        filters={"conversation": doc.name, "status": "Running"},
        fields=["name", "modified"],
    )
    now = frappe.utils.now_datetime()
    recovered = 0
    for row in candidates:
        if frappe.utils.time_diff_in_seconds(now, row.modified) <= RECOVER_GRACE:
            continue

        claim = runner._claim(row.name)
        if not claim:
            continue
        hold_until_transaction_end = False
        try:
            current = frappe.db.get_value(
                "Copilot Run", row.name, ["status", "modified"], as_dict=True
            )
            if not current or current.status != "Running":
                continue
            if frappe.utils.time_diff_in_seconds(now, current.modified) <= RECOVER_GRACE:
                continue
            frappe.db.set_value(
                "Copilot Run",
                row.name,
                {"status": "Failed", "error": "Abandoned: the run stopped without finishing."},
                update_modified=False,
            )
            recovered += 1
            _release_lock_after_transaction(claim)
            hold_until_transaction_end = True
        finally:
            if not hold_until_transaction_end:
                runner._release(claim)
    return {"recovered": recovered}


def _labelled(calls):
    """Stamp each persisted tool call with its activity label and subject, the same
    two strings the live stream publishes, so a reloaded conversation reads
    identically to the one that was watched happening."""
    for call in calls or []:
        call["label"] = runner.registry.label_for(call.get("name"))
        call["context"] = runner._context(call.get("args") or {})
    return calls


@frappe.whitelist()
def get_conversation(conversation):
    """Rebuild a conversation on reload: messages in order, with their blocks."""
    doc = _own_conversation(conversation)
    rows = frappe.get_all(
        "Copilot Message",
        filters={"parent": doc.name, "parenttype": "Copilot Conversation"},
        fields=["role", "content", "tool_calls", "tool_call_id", "run", "blocks", "idx"],
        order_by="idx asc",
        limit=HISTORY_LIMIT,
    )

    messages, all_blocks = [], []
    for row in rows:
        blocks = runner._json(row.blocks) or []
        all_blocks.extend(blocks)
        messages.append(
            {
                "role": row.role,
                "content": row.content,
                "tool_calls": _labelled(runner._json(row.tool_calls)),
                "tool_call_id": row.tool_call_id,
                "run": row.run,
                "blocks": blocks,
            }
        )

    active = frappe.get_all(
        "Copilot Run",
        filters={"conversation": doc.name, "status": ("in", ("Running", "Paused"))},
        fields=["name", "status", "pending_call"],
        order_by="creation desc",
        limit=1,
    )

    out = {
        "conversation": doc.name,
        "title": doc.title,
        "agent": doc.agent,
        "knowledge_base": doc.knowledge_base,
        "model": doc.model,
        "messages": messages,
        "blocks": all_blocks,
        # How long each turn took, so a reloaded conversation still says it.
        "runs": {
            row.name: {"duration": row.duration, "status": row.status}
            for row in frappe.get_all(
                "Copilot Run",
                filters={"conversation": doc.name},
                fields=["name", "duration", "status"],
            )
        },
    }
    if active:
        # Resubscribe rather than showing the last turn as failed.
        pending = runner._json(active[0].pending_call)
        if pending:
            pending["label"] = runner.registry.label_for(pending.get("name"))
            pending["summary"] = runner._summary(pending.get("name"), pending.get("arguments") or {})
        out["active_run"] = {
            "run": active[0].name,
            "status": active[0].status,
            "pending_call": pending,
        }
    return out


@frappe.whitelist()
def list_conversations(limit=20):
    return frappe.get_all(
        "Copilot Conversation",
        filters={"owner": frappe.session.user},
        fields=["name", "title", "model", "agent", "knowledge_base", "modified"],
        order_by="modified desc",
        limit=max(1, min(int(limit or 20), 100)),
    )


@frappe.whitelist()
def rename_conversation(conversation, title):
    doc = _own_conversation(conversation)
    doc.db_set("title", (title or "").strip()[:TITLE_LENGTH] or doc.title)
    return {"title": doc.title}


@frappe.whitelist()
def delete_conversation(conversation):
    doc = _own_conversation(conversation)
    frappe.delete_doc("Copilot Conversation", doc.name, ignore_permissions=True)
    return {"deleted": doc.name}


@frappe.whitelist()
def get_knowledge_bases():
    """Knowledge base picker contents — finbyzai's own Knowledge Base records."""
    return frappe.get_list(
        "Knowledge Base",
        fields=["name", "title", "vector_store", "status", "embeding_model"],
        order_by="modified desc",
        limit=50,
    )


@frappe.whitelist()
def test_model(model=None, agent=None):
    """Ask the configured model to say one word, and report exactly what came back.

    A dead credential is the most common reason the Copilot "does nothing", and the
    provider's own message says why — out of credits, over a weekly cap, invalid key,
    rate limited. Reading that in the settings dialog takes seconds; discovering it by
    sending a real question and waiting for a run to fail takes minutes and leaves a
    failed run behind.

    Deliberately tiny: three tokens, no tools, no history, no conversation created.
    """
    import time

    from finbyzai.copilot import agent as agent_config

    settings = runner.get_settings()
    agent_doc = agent_config.load(agent or settings.default_agent)
    name = agent_config.resolved_model(agent_doc, settings, override=model)

    if not name:
        return {
            "ok": False,
            "error": _("No model configured."),
            "hint": _("Pick a model here, or set one on the agent."),
        }
    name = access.require_read("LLM", name, label="model")

    provider = frappe.db.get_value("LLM", name, "provider")
    started = time.monotonic()
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = frappe.get_doc("LLM", name).llm
        reply = llm.invoke(
            [
                SystemMessage(content="Reply with the single word: OK"),
                HumanMessage(content="ping"),
            ]
        )
        content = getattr(reply, "content", "") or ""
        if not isinstance(content, str):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return {
            "ok": True,
            "model": name,
            "provider": provider,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": content.strip()[:80],
        }
    except Exception as e:
        return {
            "ok": False,
            "model": name,
            "provider": provider,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": _provider_message(e),
            "hint": _provider_hint(e),
        }


def _provider_message(error: Exception) -> str:
    """The provider's own sentence, not litellm's wrapper around it."""
    text = frappe.utils.strip_html(str(error) or "") or error.__class__.__name__
    # Providers answer with JSON; the useful part is the message inside it.
    match = re.search(r'"message"\s*:\s*"([^"]{4,400})"', text)
    return (match.group(1) if match else text)[:400]


def _provider_hint(error: Exception) -> str | None:
    text = str(error).lower()
    if "weekly limit" in text or "key limit" in text:
        return _("This key has hit its own spending cap. Raise the cap on the key, not the account.")
    if "no credits" in text or "insufficient_quota" in text or "billing" in text:
        return _("The account is out of credit. Top it up, or pick a model from another provider.")
    if "api key" in text or "authentication" in text or "401" in text:
        return _("The key is wrong or revoked. Set it again on the LLM Provider record.")
    if "rate" in text and "limit" in text:
        return _("Rate limited right now. Try again shortly, or use a paid model instead of a free one.")
    if "timeout" in text or "timed out" in text:
        return _("The provider did not answer in time. Free models are often overloaded.")
    return None


@frappe.whitelist()
def get_tools(agent=None, knowledge_base=None):
    """What this agent can actually do, for the settings dialog's Tools pane.

    Reads the live registry rather than a hardcoded list, so a tool added to the app —
    or an AI Tool row linked on the agent — shows up here without a UI change.
    """
    from finbyzai.copilot import agent as agent_config
    from finbyzai.copilot import registry

    settings = runner.get_settings()
    doc = agent_config.load(agent or settings.default_agent)
    selected_kb = access.require_read("Knowledge Base", knowledge_base) if knowledge_base else None

    registry.load_tools()
    builtin = [
        {
            "name": spec["name"],
            "label": spec["label"],
            "description": " ".join((spec["description"] or "").split())[:220],
            "tags": spec["tags"],
            "confirm": spec["confirm"],
            "asks": spec["asks"],
            "source": "builtin",
        }
        for spec in registry.TOOLS.values()
    ]
    builtin.sort(key=lambda t: (t["tags"][0] if t["tags"] else "", t["name"]))

    custom = []
    for row in (doc.tools if doc else None) or []:
        if not access.can_read("AI Tool", row.tool):
            continue
        tool = frappe.db.get_value(
            "AI Tool", row.tool, ["name", "description", "requires_confirmation"], as_dict=True
        )
        if tool:
            custom.append(
                {
                    "name": tool.name,
                    "label": frappe.unscrub(tool.name),
                    "description": " ".join((tool.description or "").split())[:220],
                    "tags": ["custom"],
                    "confirm": bool(tool.requires_confirmation),
                    "asks": False,
                    "source": "AI Tool",
                }
            )

    active_kb = selected_kb or (doc.knowledge_base if doc else None)
    if active_kb and not access.can_read("Knowledge Base", active_kb):
        active_kb = None
    if active_kb:
        custom.append(
            {
                "name": "search_knowledge",
                "label": _("Searching Knowledge"),
                "description": _("Searches the {0} knowledge base.").format(active_kb),
                "tags": ["knowledge"],
                "confirm": False,
                "asks": False,
                "source": "Knowledge Base",
            }
        )

    return {"agent": doc.name if doc else None, "tools": builtin + custom}



def can_administer() -> bool:
    """Who may change what every user sees: the system prompt and the site defaults."""
    return bool(set(frappe.get_roles()) & {"System Manager", "Copilot Admin"})


@frappe.whitelist()
def get_settings():
    """Everything the settings dialog shows: the pickers' contents, the system defaults,
    and whether this user may change them."""
    settings = runner.get_settings()
    is_admin = can_administer()

    out = {
        "can_edit_system": is_admin,
        "agents": get_agents(),
        "models": get_models(),
        "knowledge_bases": get_knowledge_bases(),
        # Everyone gets their own instructions; only an admin sees the system prompt.
        "user": {"instructions": user_settings.for_user()},
        "system": {
            "enabled": bool(settings.enabled),
            "default_agent": settings.default_agent,
            "default_model": settings.default_model,
            "max_iterations": settings.max_iterations,
            "auto_approve": bool(settings.auto_approve),
            "enable_external_search": bool(settings.enable_external_search),
        },
    }
    # The prompt is long and only an admin can change it; don't ship it to everyone.
    if is_admin:
        out["system"]["system_prompt"] = settings.system_prompt
        # The built-in prompt, always current — so the dialog can show what an
        # empty field actually resolves to, and "Reset to default" has something
        # to reset *to*. A blank override was the whole point: a saved prompt
        # freezes at whatever DEFAULT_SYSTEM_PROMPT said the day it was written,
        # silently missing every improvement since. This site's own override sat
        # untouched since the prompt was a third shorter than it is now.
        out["system_prompt_default"] = runner.DEFAULT_SYSTEM_PROMPT
    else:
        agent_names = {row.name for row in out["agents"]}
        model_names = {row.name for row in out["models"]}
        if out["system"]["default_agent"] not in agent_names:
            out["system"]["default_agent"] = None
        if out["system"]["default_model"] not in model_names:
            out["system"]["default_model"] = None
    return out


@frappe.whitelist()
def save_settings(system=None, conversation=None, user=None):
    """Save the dialog. System defaults need System Manager; the per-conversation
    agent / model / knowledge base is the user's own choice on their own chat."""
    saved = {}

    values = _parse(user) or {}
    if "instructions" in values:
        # A user's own instructions, applied to their turns only. No role check beyond
        # being able to reach the Copilot at all: this cannot affect anyone else.
        user_settings.save_for_user(values.get("instructions") or "")
        saved["user"] = True

    values = _parse(system) or {}
    if values:
        if not can_administer():
            frappe.throw(_("Only a Copilot Admin can change the Copilot's defaults."))
        allowed = (
            "enabled",
            "default_agent",
            "default_model",
            "max_iterations",
            "auto_approve",
            "enable_external_search",
            "system_prompt",
        )
        doc = frappe.get_single("Copilot Settings")
        for key in allowed:
            if key in values:
                doc.set(key, values[key])
        doc.save(ignore_permissions=True)
        frappe.clear_cache(doctype="Copilot Settings")
        saved["system"] = True

    values = _parse(conversation) or {}
    if values.get("name"):
        doc = _own_conversation(values["name"])
        selections = {
            "agent": ("AI Agent", "agent"),
            "model": ("LLM", "model"),
            "knowledge_base": ("Knowledge Base", "knowledge base"),
        }
        for key, (doctype, label) in selections.items():
            if key in values:
                value = access.require_read(doctype, values[key], label=label)
                doc.db_set(key, value, update_modified=False)
        saved["conversation"] = doc.name

    return saved


@frappe.whitelist()
def get_agents():
    """Agent picker contents — finbyzai's own AI Agent records."""
    from finbyzai.copilot import branding

    rows = frappe.get_list(
        "AI Agent",
        fields=["name", "title", "llm", "llm_provider", "knowledge_base"],
        order_by="title asc",
        limit=50,
    )
    # Which one the picker should start on. Without this the panel fell back to the
    # alphabetically first AI Agent on the site, so a site with its own agents opened
    # the Copilot on someone else's agent and its provider.
    default_agent = runner.get_settings().default_agent
    for row in rows:
        row["logo"] = branding.logo_for(row.llm_provider, row.llm)
        row["is_default"] = 1 if row.name == default_agent else 0
    return rows


@frappe.whitelist()
def get_models():
    """Model picker contents: the enabled, non-embedding LLM records, each with its
    provider's logo so the picker can show who serves it."""
    from finbyzai.copilot import branding

    rows = frappe.get_list(
        "LLM",
        filters={"enabled": 1, "is_embedding_model": 0},
        fields=["name", "title", "provider", "supports_vision", "is_reasoning", "size"],
        order_by="provider asc, name asc",
    )
    for row in rows:
        row["logo"] = branding.logo_for(row.provider, row.name)
    return rows


# ── ownership ─────────────────────────────────────────────────────────────────


def _own_conversation(name):
    doc = frappe.get_doc("Copilot Conversation", (name or "").strip())
    _assert_owner(doc)
    return doc


def _own_run(name):
    doc = frappe.get_doc("Copilot Run", (name or "").strip())
    _assert_owner(doc)
    return doc


def _assert_owner(doc):
    if doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
        raise frappe.PermissionError(f"{doc.doctype} {doc.name} belongs to another user.")


def _new_conversation(text, model=None, agent=None, knowledge_base=None):
    settings = runner.get_settings()
    chosen = access.require_read("AI Agent", agent or settings.default_agent, label="agent")
    selected_model = access.require_read("LLM", model, label="model") if model else None
    selected_kb = (
        access.require_read("Knowledge Base", knowledge_base, label="knowledge base")
        if knowledge_base
        else None
    )
    return frappe.get_doc(
        {
            "doctype": "Copilot Conversation",
            "title": text[:TITLE_LENGTH],
            "user": frappe.session.user,
            "agent": chosen,
            "knowledge_base": selected_kb,
            # Left empty unless the user actually picked one. Stamping the fallback here
            # would make every new chat look like it had chosen a model, and that choice
            # would then outrank the agent's own LLM.
            "model": selected_model,
        }
    ).insert(ignore_permissions=True)


def _start_lock_key(conversation):
    return f"{frappe.local.site}|copilot:start:{conversation}"


def _acquire_start_lock(conversation):
    lock = frappe.cache.lock(_start_lock_key(conversation), timeout=60, blocking=False)
    if lock.acquire(blocking=False):
        return lock
    frappe.throw(
        _("This conversation has a turn in progress. Answer it or stop it first."),
        title=_("Still working"),
    )


def _release_lock(lock):
    if lock and lock.owned():
        lock.release()


def _release_lock_after_transaction(lock):
    released = False

    def release():
        nonlocal released
        if not released:
            _release_lock(lock)
            released = True

    frappe.db.after_commit.add(release)
    frappe.db.after_rollback.add(release)


def _block_while_running(conversation):
    """One turn at a time per conversation — two loops writing the same message list
    would interleave tool calls and confuse the model.

    The check and the caller's insert of the new Running row are two separate steps;
    without a lock between them, two requests arriving together can both see no
    active run and both proceed to insert one. The Redis lock stays held until commit
    or rollback; after commit, the new row's "Running" status becomes the durable
    guard for later requests.
    """
    active = frappe.get_all(
        "Copilot Run",
        filters={"conversation": conversation, "status": ("in", ("Running", "Paused"))},
        pluck="name",
        limit=1,
    )
    if active:
        frappe.throw(
            _("This conversation has a turn in progress. Answer it or stop it first."),
            title=_("Still working"),
        )


def _with_attachments(text, files):
    if not files:
        return text
    return f"{text}\n\n[Attached files: {', '.join(files)}]"


def _parse(value):
    if value in (None, ""):
        return None
    if isinstance(value, dict | list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
