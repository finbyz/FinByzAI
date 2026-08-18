from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AutomationSchedule(Document):
	def validate(self):
		if self.frequency not in {"HOURLY", "DAILY", "WEEKLY"}:
			frappe.throw(_("Schedule frequency must be hourly, daily, or weekly."))
		if self.version_policy not in {"ACTIVE_AT_RUN", "PINNED"}:
			frappe.throw(_("Choose a valid schedule version policy."))
		if self.catch_up_policy not in {"RUN_ONCE", "SKIP"}:
			frappe.throw(_("Choose a valid catch-up policy."))
		if self.overlap_policy not in {"SKIP", "QUEUE"}:
			frappe.throw(_("Choose a valid overlap policy."))
		if cint(self.batch_size) < 1 or cint(self.batch_size) > 500:
			frappe.throw(_("Schedule batch size must be between 1 and 500."))
		if cint(self.records_per_minute) < 1 or cint(self.records_per_minute) > 10000:
			frappe.throw(_("Schedule rate must be between 1 and 10,000 records per minute."))
		if cint(self.max_records) < 0:
			frappe.throw(_("Maximum records cannot be negative."))
		try:
			ZoneInfo(self.timezone)
		except (ZoneInfoNotFoundError, TypeError):
			frappe.throw(_("Choose a valid IANA timezone."))
		if self.version_policy == "PINNED":
			if not self.workflow_version:
				frappe.throw(_("Choose a workflow version for a pinned schedule."))
			version_workflow = frappe.db.get_value("Automation Workflow Version", self.workflow_version, "workflow")
			if version_workflow != self.workflow:
				frappe.throw(_("The pinned workflow version does not belong to this workflow."))
		else:
			self.workflow_version = None
