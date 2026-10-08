"""Create a scheduled prompt through Copilot's normal approval flow."""

import frappe

from finbyzai.copilot.registry import tool


@tool(
    "schedule_task",
    """Schedule a future Copilot task. Use when the user asks for a reminder, a later
    report, or a recurring task. Provide self-contained instructions for the future
    run, a local ISO date and time without an offset, and an IANA timezone. Repeat
    can be Once, Hourly, Daily, Weekly, Monthly or Custom (five-field cron).
    Ask the user if timing or timezone is unclear. Never claim a task is scheduled
    until this tool succeeds. The future task runs in its own conversation and uses
    this chat's agent, model and knowledge base. Start date is a lower bound for cron.
    Future actions follow the existing Copilot approval settings.""",
    confirm=True, tags=["scheduling"], label="Scheduling task",
)
def schedule_task_tool(title: str, prompt: str, scheduled_for: str, timezone: str,
                       recurrence: str = "Once", cron_expression: str | None = None) -> dict:
    from finbyzai.copilot.schedules import save_task

    context = frappe.flags.get("copilot") or {}
    conversation = frappe.get_doc("Copilot Conversation", context["conversation"])
    if conversation.owner != frappe.session.user:
        raise frappe.PermissionError("This conversation belongs to another user.")
    return save_task({
        "title": title, "prompt": prompt, "scheduled_for": scheduled_for,
        "timezone": timezone, "recurrence": recurrence, "cron_expression": cron_expression,
        "agent": conversation.agent, "model": conversation.model,
        "knowledge_base": conversation.knowledge_base, "enabled": 1,
    })
