# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Native implementation of the ``ask_user`` AI Tool."""

from finbyzai.copilot.registry import tool

@tool(
    "ask_user",
    """Ask the user a short question and wait for their answer. Use it when a fact is
    genuinely unknowable from the data — which company, which warehouse, what period,
    which of several matching records they meant.

    Give `options` when the answer is one of a few known values; the user gets them as
    buttons and can still type something else. Ask one thing at a time, and never ask
    for something `find_doctypes`, `describe`, `read` or `list_reports` could tell you.
    Do not use this to request permission for a write — writes are approved separately.""",
    asks=True,
    tags=["interaction"],
    label="Asking you",
)
def ask_user_tool(question: str, options: list | None = None) -> dict:
    # Never executed: the runner intercepts asks=True tools. Present so the model gets
    # a schema, and so a direct call in a test fails loudly rather than silently.
    raise NotImplementedError("ask_user is resolved by the runner, not called directly")
