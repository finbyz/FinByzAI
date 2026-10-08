import frappe
from frappe.model.document import Document

from finbyzai.copilot import access
from finbyzai.copilot.schedule_time import next_occurrence, schedule_start


class CopilotScheduledTask(Document):
    def validate(self):
        from finbyzai.copilot.schedules import to_storage_time, utc_now

        access.require_copilot()
        if self.owner and self.owner != frappe.session.user:
            raise frappe.PermissionError("You can only manage your own scheduled tasks.")
        previous = self.get_doc_before_save()
        if previous and self.owner != previous.owner:
            frappe.throw("A scheduled task's owner cannot be changed.")
        self.title = (self.title or "").strip()
        self.prompt = (self.prompt or "").strip()
        if not self.title or not self.prompt:
            frappe.throw("A title and task instructions are required.")
        if len(self.title) > 140 or len(self.prompt) > 20000:
            frappe.throw("Keep the title under 140 and instructions under 20,000 characters.")
        for field, doctype in (("agent", "AI Agent"), ("model", "LLM"), ("knowledge_base", "Knowledge Base")):
            access.require_read(doctype, self.get(field))
        try:
            start = schedule_start(self.scheduled_for, self.timezone)
            next_occurrence(start, self.recurrence, self.cron_expression)
        except (ValueError, TypeError, KeyError) as exc:
            frappe.throw(str(exc))
        changed = not previous or any(
            self.get(field) != previous.get(field)
            for field in ("scheduled_for", "timezone", "recurrence", "cron_expression")
        )
        if not previous:
            self.last_run = self.last_conversation = None
        if changed or (previous and self.enabled and not previous.enabled):
            if self.recurrence == "Once" and start <= utc_now():
                frappe.throw("Choose a future date and time.")
            due = next_occurrence(start, self.recurrence, self.cron_expression, after=utc_now())
            self.next_run = to_storage_time(due) if due else None
            self.completed = 0
            self.error = None
        elif previous:
            for field in ("next_run", "completed", "error", "last_run", "last_conversation"):
                self.set(field, previous.get(field))


def get_permission_query_conditions(user=None):
    user = user or frappe.session.user
    if not access.has_copilot(user):
        return "1 = 0"
    return f"`tabCopilot Scheduled Task`.`owner` = {frappe.db.escape(user)}"


def has_permission(doc, ptype=None, user=None):
    user = user or frappe.session.user
    return access.has_copilot(user) and (ptype == "create" or doc.owner == user)
