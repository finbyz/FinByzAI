# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""The bridge to finbyzai's own AI Agent doctype.

finbyzai already models an agent: `AI Agent` carries the LLM, a tool list (`AI Agent
Tool` -> `AI Tool`, each a dynamically imported python function), a `Knowledge Base`,
memory settings, and the generation limits. None of that is rebuilt here — this module
reads an AI Agent record and hands the runner:

    model()      the ChatLiteLLM from AI Agent.llm (falling back to Copilot Settings)
    tools()      the 16 copilot builtins + the agent's own AI Tools + its knowledge
                 base, which the vector-store adapter already exposes `as_tool()`
    limits()     max_iterations / temperature / max_tokens from the agent
    memory()     history shaped by AI Agent.memory_type

So configuring the Copilot means configuring an AI Agent — add a tool there and it
appears in chat; attach a knowledge base and the agent can search it.

Memory note: finbyzai's `FrappeChatMemory` persists to `AI Conversation`. The Copilot
keeps its own transcript in `Copilot Message` (it has to: tool calls and tool results
have no place in AI Conversation's schema). So the memory *types* are honoured here
against the Copilot's own transcript rather than double-writing history to two
doctypes.
"""

import frappe

WINDOW_TURNS = 6
SUMMARY_TRIGGER = 24
SUMMARY_KEEP = 8


def load(agent_name: str | None):
    """The AI Agent doc, or None to run on Copilot Settings alone."""
    if not agent_name:
        return None
    if not frappe.db.exists("AI Agent", agent_name):
        return None
    return frappe.get_cached_doc("AI Agent", agent_name)


def model(agent, settings, override: str | None = None):
    """The model to call, in precedence order.

    `override` is this conversation's own choice, made in the composer or the settings
    dialog. It wins, because a chat asking for a different model must not have to edit
    a shared record: the AI Agent is used by every user and by the digest, so a chat
    changing it would change everyone's. The agent's LLM is the default, not the law.

        run / conversation choice  ->  AI Agent.llm  ->  Copilot Settings.default_model
    """
    name = override or (agent and agent.llm) or settings.default_model
    if not name:
        frappe.throw(
            "No model configured. Set an LLM on the AI Agent, or default_model in Copilot Settings."
        )
    if not frappe.db.exists("LLM", name):
        frappe.throw(
            f"Model {name!r} no longer exists. Pick another one in the Copilot's model picker."
        )
    llm = frappe.get_doc("LLM", name).llm

    # AI Agent's generation limits, when the underlying client accepts them.
    options = {}
    if agent and agent.temperature:
        options["temperature"] = float(agent.temperature)
    if agent and agent.max_tokens:
        options["max_tokens"] = int(agent.max_tokens)
    if options:
        try:
            llm = llm.bind(**options)
        except Exception:
            pass
    return llm


def tools(agent, knowledge_base=None):
    """Copilot builtins + the agent's AI Tools + a knowledge base as a tool.

    `knowledge_base` is the conversation's own choice; it overrides the agent's, so a
    user can point one chat at a different knowledge base without editing the agent.
    """
    from finbyzai.copilot import registry

    out = list(registry.langchain_tools())
    known = {t.name for t in out}

    if not agent:
        kb_tool = knowledge_tool(None, knowledge_base)
        if kb_tool and getattr(kb_tool, "name", None) not in known:
            out.append(kb_tool)
        return out

    for row in agent.tools or []:
        try:
            tool = frappe.get_doc("AI Tool", row.tool).get_tool()
        except Exception:
            frappe.log_error(f"Copilot: AI Tool {row.tool} failed to load", frappe.get_traceback())
            continue
        # A broken AI Tool returns None rather than raising; and an AI Tool must never
        # shadow a builtin, or the guard and approval gate would be bypassed.
        if tool and getattr(tool, "name", None) and tool.name not in known:
            out.append(tool)
            known.add(tool.name)

    kb_tool = knowledge_tool(agent, knowledge_base)
    if kb_tool and getattr(kb_tool, "name", None) not in known:
        out.append(kb_tool)

    return out


def confirming_tools(external: dict) -> set:
    """AI Tool names whose record asks for confirmation.

    A tool attached by an admin runs unprompted by default — that is the same trust
    model as a Server Script, and a digest could not run otherwise. But a custom tool
    that writes should be gated like any other write, so the AI Tool record carries the
    flag and the runner reads it here.
    """
    if not external:
        return set()
    names = [name for name in external if frappe.db.exists("AI Tool", name)]
    if not names:
        return set()
    gated = frappe.get_all(
        "AI Tool", filters={"name": ("in", names), "requires_confirmation": 1}, pluck="name"
    )
    return set(gated)


def knowledge_tool(agent, knowledge_base=None):
    """A Knowledge Base as a tool, via finbyzai's own vector-store adapter."""
    name = knowledge_base or (agent.knowledge_base if agent else None)
    if not name:
        return None
    try:
        store = frappe.get_doc("Knowledge Base", name).get_vector_store()
        return store.as_tool()
    except Exception:
        frappe.log_error(f"Copilot: knowledge base {name} unavailable", frappe.get_traceback())
        return None


def limits(agent, settings):
    return {
        "max_iterations": int(
            (agent and agent.max_iterations) or settings.max_iterations or 25
        ),
        "verbose": bool(agent and agent.verbose_mode),
    }


def instructions(agent, settings, knowledge_base=None):
    """System prompt: Copilot Settings, plus the agent's own seeded messages.

    AI Agent.messages (Chat Message rows) is where finbyzai keeps an agent's standing
    instructions, so they are appended rather than ignored.
    """
    from finbyzai.copilot.runner import DEFAULT_SYSTEM_PROMPT

    parts = [(settings.system_prompt or DEFAULT_SYSTEM_PROMPT).strip()]

    for row in (agent.messages if agent else None) or []:
        content = (getattr(row, "content", "") or "").strip()
        # The Chat Message child table calls this column `type` (system/human), not
        # `role`. Reading the wrong one silently dropped every agent instruction.
        kind = (getattr(row, "type", None) or getattr(row, "role", "") or "").lower()
        if content and kind in ("system", ""):
            parts.append(content)

    parts.append(_site_context())

    active_kb = knowledge_base or (agent.knowledge_base if agent else None)
    if active_kb:
        parts.append(
            f'A knowledge base named "{active_kb}" is attached. Search it before answering '
            "questions about processes, policies or documents rather than guessing, and use "
            "`remember` to save a durable fact the user tells you."
        )

    return "\n\n".join(parts)


def _site_context() -> str:
    """Today's date, the company, the currency and the fiscal year.

    Without this the model is guessing at the one thing it cannot infer. Watching it
    work, it wrote `delivery_date: "2025-01-15"` — eight months in the past — and
    reasoned about "this year" from its training cutoff rather than from the site. Every
    question with a date in it depends on this, so it costs a couple of hundred
    characters in every request and earns them back immediately.
    """
    today = frappe.utils.getdate(frappe.utils.nowdate())
    lines = [f"SITE — today is {today.strftime('%A, %d %B %Y')}."]

    company = frappe.db.get_single_value("Global Defaults", "default_company")
    if company:
        currency = frappe.db.get_value("Company", company, "default_currency")
        lines.append(f'The default company is "{company}"' + (f" and it reports in {currency}." if currency else "."))

    fiscal = frappe.get_all(
        "Fiscal Year",
        filters={"year_start_date": ("<=", today), "year_end_date": (">=", today), "disabled": 0},
        fields=["name", "year_start_date", "year_end_date"],
        limit=1,
    )
    if fiscal:
        year = fiscal[0]
        lines.append(
            f"The current fiscal year is {year.name} ({year.year_start_date} to {year.year_end_date}); "
            f'"this year" means the calendar year {today.year} unless the user says fiscal year.'
        )
    else:
        lines.append(f'"This year" means the calendar year {today.year}.')

    lines.append("Never invent a date. Derive every date from today's date above.")
    return " ".join(lines)


def shape_history(agent, messages: list) -> list:
    """Apply AI Agent.memory_type to the transcript handed to the model.

    Buffer  — everything (the default).
    Window  — the last few turns only, so a long chat stays cheap.
    Summary — older turns collapsed into one system note by the model itself.
    Vector  — Buffer here; retrieval is the knowledge-base tool's job, and the agent
              calls it when it needs to remember something written down.
    """
    if not agent or not agent.enable_memory:
        return messages
    kind = (agent.memory_type or "Buffer Memory").strip()

    system, rest = messages[:1], messages[1:]

    if kind == "Window Memory":
        window = int(getattr(agent, "window_size", 0) or WINDOW_TURNS)
        return system + _trim_to_turns(rest, window)

    if kind == "Summary Memory" and len(rest) > SUMMARY_TRIGGER:
        return system + _summarize(agent, rest)

    return messages


def _trim_to_turns(messages: list, turns: int) -> list:
    """Keep the last `turns` user turns, never splitting a tool call from its result."""
    starts = [i for i, m in enumerate(messages) if _role(m) == "human"]
    if len(starts) <= turns:
        return messages
    return messages[starts[-turns] :]


def _summarize(agent, messages: list) -> list:
    """Collapse the older half into a summary, keeping recent turns verbatim."""
    from langchain_core.messages import SystemMessage

    old, recent = messages[:-SUMMARY_KEEP], messages[-SUMMARY_KEEP:]
    transcript = "\n".join(f"{_role(m)}: {_text(m)[:400]}" for m in old if _text(m))
    if not transcript.strip():
        return messages

    try:
        llm = frappe.get_doc("LLM", agent.llm).llm
        summary = _text(
            llm.invoke(
                [
                    SystemMessage(
                        content="Summarize this ERP conversation in under 150 words: what the "
                        "user wanted, what was found, what was changed. Keep names and numbers."
                    ),
                    SystemMessage(content=transcript),
                ]
            )
        )
    except Exception:
        frappe.log_error("Copilot: memory summarization failed", frappe.get_traceback())
        return messages

    return [SystemMessage(content=f"Earlier in this conversation: {summary}")] + _repair(recent)


def _repair(messages: list) -> list:
    """Drop a leading tool result whose call is no longer in the window — providers
    reject a tool message that answers nothing."""
    while messages and _role(messages[0]) == "tool":
        messages = messages[1:]
    return messages


def _role(message) -> str:
    return getattr(message, "type", "") or ""


def _text(message) -> str:
    content = getattr(message, "content", "") or ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict))


def resolved_model(agent, settings, override: str | None = None) -> str | None:
    """Which model name the next call will actually use, without building the client."""
    return override or (agent and agent.llm) or settings.default_model
