# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Tool registry — one place that knows every tool, its schema, and whether it
needs the user's approval before it runs.

Register with the decorator, then hand `langchain_tools()` to the LangGraph agent.
Nothing calls a tool function directly: `run_guarded` (see guard.py) always wraps
it, so a failure comes back to the model as a result it can act on.
"""

import frappe
from finbyzai.copilot.guard import run_guarded

TOOLS = {}


def tool(name, description, args_schema=None, confirm=False, tags=None, label=None, asks=False):
    """Register a callable as an agent tool.

    confirm=True  -> the runner pauses the graph and asks the user to Approve/Reject
                     before this call runs (every write tool should set this).
    args_schema   -> a pydantic model describing the arguments, for the LLM's tool spec.
    """

    def wrapper(fn):
        TOOLS[name] = {
            "name": name,
            "label": label or name.replace("_", " ").capitalize(),
            "description": description.strip(),
            "fn": fn,
            "args_schema": args_schema,
            "confirm": bool(confirm),
            # asks=True means the runner pauses and shows the user a question instead
            # of calling the function at all.
            "asks": bool(asks),
            "tags": tags or [],
        }
        return fn

    return wrapper


def get(name):
    """Any entry point may be first — a web request, a worker, a test — so the registry
    fills itself rather than depending on someone having called load_tools()."""
    if not TOOLS:
        load_tools()
    spec = TOOLS.get(name)
    if not spec:
        frappe.throw(f"Unknown copilot tool: {name}. Available: {', '.join(sorted(TOOLS))}")
    return spec


def call(name, arguments=None):
    """Run a registered tool through the guard. Always returns a result dict."""
    spec = get(name)
    return run_guarded(name, spec["fn"], arguments or {})


def needs_confirmation(name):
    return get(name)["confirm"]


def label_for(name):
    """Present-tense activity label, e.g. "Reading DocType Meta". The panel shows this
    on the collapsible activity line; it lives here so a live run and a reloaded
    conversation cannot disagree about what a step was called."""
    if not TOOLS:
        load_tools()
    spec = TOOLS.get(name)
    return spec["label"] if spec else name.replace("_", " ").capitalize()


def langchain_tools(names=None):
    """Tool specs to bind to the model.

    These are schemas only — the runner executes through `call()` so the guard can
    never be bypassed by something invoking the tool object directly. Schemas are
    inferred from each function's type hints, which is why every tool is annotated.
    """
    from langchain_core.tools import StructuredTool

    load_tools()
    wanted = names or list(TOOLS)
    return [
        StructuredTool.from_function(
            func=TOOLS[name]["fn"],
            name=name,
            description=TOOLS[name]["description"],
            args_schema=TOOLS[name]["args_schema"],
            infer_schema=TOOLS[name]["args_schema"] is None,
        )
        for name in wanted
        if name in TOOLS
    ]


def load_tools():
    """Import the tool modules so their decorators run. Called by the runner."""
    from finbyzai.copilot.tools import (  # noqa: F401
        compute,
        data,
        files,
        interact,
        memory,
        meta,
        notify,
        reports,
        visualize,
        write,
    )

    return TOOLS
