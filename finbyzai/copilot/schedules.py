"""Owner-scoped scheduled prompts and idempotent background dispatch."""

from datetime import datetime, timezone

import frappe
from frappe.utils import escape_html, get_datetime

from finbyzai.copilot import access, runner
from finbyzai.copilot.schedule_time import next_occurrence, schedule_start

DOCTYPE = "Copilot Scheduled Task"
EDITABLE = (
    "title", "prompt", "scheduled_for", "timezone", "recurrence", "cron_expression",
    "agent", "model", "knowledge_base", "enabled",
)


@frappe.whitelist()
def list_tasks():
    access.require_copilot()
    rows = frappe.get_list(
        DOCTYPE, filters={"owner": frappe.session.user},
        fields=["name", *EDITABLE, "next_run", "completed", "last_run", "last_conversation", "error"],
        order_by="creation desc", limit_page_length=500,
    )
    for row in rows:
        row.next_run = to_iso(row.next_run)
        row.run_status = frappe.db.get_value("Copilot Run", row.last_run, "status") if row.last_run else None
    return rows


@frappe.whitelist(methods=["POST"])
def save_task(task, name=None):
    access.require_copilot()
    values = frappe.parse_json(task) if isinstance(task, str) else task
    if not isinstance(values, dict):
        frappe.throw("Task must be an object.")
    doc = own_task(name, for_update=True) if name else frappe.new_doc(DOCTYPE)
    doc.update({field: values[field] for field in EDITABLE if field in values})
    doc.save()
    return {"name": doc.name, "next_run": to_iso(doc.next_run), "timezone": doc.timezone}


@frappe.whitelist(methods=["POST"])
def set_enabled(name, enabled):
    access.require_copilot()
    doc = own_task(name, for_update=True)
    if str(enabled) not in ("0", "1", "True", "False"):
        frappe.throw("Enabled must be true or false.")
    doc.enabled = int(str(enabled) in ("1", "True"))
    doc.save()
    return {"name": doc.name, "enabled": doc.enabled}


@frappe.whitelist(methods=["POST"])
def delete_task(name):
    access.require_copilot()
    doc = own_task(name, for_update=True)
    frappe.delete_doc(DOCTYPE, doc.name)
    return {"deleted": doc.name}


def own_task(name, for_update=False):
    doc = frappe.get_doc(DOCTYPE, name, for_update=for_update)
    if doc.owner != frappe.session.user:
        raise frappe.PermissionError("You can only manage your own scheduled tasks.")
    return doc


def dispatch_due_tasks():
    if not runner.get_settings().enabled:
        return
    task = frappe.qb.DocType(DOCTYPE)
    run = frappe.qb.DocType("Copilot Run")
    due = (
        frappe.qb.from_(task).left_join(run).on(task.last_run == run.name)
        .select(task.name)
        .where((task.enabled == 1) & (task.completed == 0) & (task.next_run <= to_storage_time(utc_now())))
        .where(run.status.isnull() | run.status.notin(["Running", "Paused"]))
        .orderby(task.next_run).limit(100).run(pluck=True)
    )
    for name in due:
        frappe.enqueue(
            "finbyzai.copilot.schedules.execute_task", task=name,
            queue="long", timeout=120, enqueue_after_commit=True,
            job_id=f"copilot-schedule:{name}", deduplicate=True,
        )


def execute_task(task):
    """Lock, recheck and advance together with run creation in one transaction."""
    user = frappe.session.user
    doc = None
    try:
        doc = frappe.get_doc(DOCTYPE, task, for_update=True)
        if not doc.enabled or doc.completed or not doc.next_run or get_datetime(doc.next_run) > to_storage_time(utc_now()):
            return
        if not runner.get_settings().enabled:
            return
        if doc.last_run and frappe.db.get_value("Copilot Run", doc.last_run, "status") in ("Running", "Paused"):
            return
        frappe.set_user(doc.owner)
        if not frappe.db.get_value("User", doc.owner, "enabled"):
            raise frappe.PermissionError("The task owner's account is disabled.")
        access.require_copilot()
        for field, doctype in (("agent", "AI Agent"), ("model", "LLM"), ("knowledge_base", "Knowledge Base")):
            access.require_read(doctype, doc.get(field))
        from finbyzai.copilot.api import start_run

        conversation = frappe.get_doc({
            "doctype": "Copilot Conversation", "title": doc.title,
            "user": doc.owner, "agent": doc.agent, "model": doc.model,
            "knowledge_base": doc.knowledge_base,
        }).insert(ignore_permissions=True)
        result = start_run(doc.prompt, conversation=conversation.name)
        due = next_occurrence(
            schedule_start(doc.scheduled_for, doc.timezone), doc.recurrence,
            doc.cron_expression, after=utc_now(),
        )
        frappe.db.set_value(DOCTYPE, doc.name, {
            "last_run": result["run"], "last_conversation": result["conversation"],
            "next_run": to_storage_time(due) if due else None,
            "completed": int(due is None), "error": None,
        })
        frappe.db.commit()
    except Exception as exc:
        frappe.db.rollback()
        try:
            current = frappe.get_doc(DOCTYPE, task, for_update=True)
        except frappe.DoesNotExistError:
            return
        if doc and current.modified == doc.modified:
            frappe.db.set_value(DOCTYPE, task, {"enabled": 0, "error": str(exc)[:2000]})
        frappe.db.commit()
        frappe.log_error("Copilot scheduled task failed", frappe.get_traceback())
    finally:
        frappe.set_user(user)


def notify_result(run, event):
    if event.get("type") != "done" or not frappe.db.table_exists(DOCTYPE):
        return
    task = frappe.db.get_value(DOCTYPE, {"last_run": run}, ["title", "owner", "last_conversation"], as_dict=True)
    if not task:
        return
    status = event.get("status") or "Needs your attention"
    frappe.get_doc({
        "doctype": "Notification Log", "for_user": task.owner,
        "type": "Alert", "subject": f"{escape_html(task.title)}: {escape_html(status)}",
        "document_type": "Copilot Conversation", "document_name": task.last_conversation,
    }).insert(ignore_permissions=True)


def utc_now():
    return datetime.now(timezone.utc)


def to_storage_time(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def to_iso(value):
    if not value:
        return None
    return get_datetime(value).replace(tzinfo=timezone.utc).isoformat()
