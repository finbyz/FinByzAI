import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AutomationBackfillJob(Document):
	def validate(self):
		if self.source not in {"BACKFILL", "SCHEDULE"}:
			frappe.throw(_("Backfill source must be BACKFILL or SCHEDULE."))
		if cint(self.batch_size) < 1 or cint(self.batch_size) > 500:
			frappe.throw(_("Backfill batch size must be between 1 and 500."))
		if cint(self.records_per_minute) < 1 or cint(self.records_per_minute) > 10000:
			frappe.throw(_("Backfill rate must be between 1 and 10,000 records per minute."))
		if cint(self.max_records) < 0:
			frappe.throw(_("Maximum records cannot be negative."))
		version_workflow = frappe.db.get_value("Automation Workflow Version", self.workflow_version, "workflow")
		if version_workflow != self.workflow:
			frappe.throw(_("The pinned workflow version does not belong to this workflow."))
		if self.source == "SCHEDULE" and not self.schedule:
			frappe.throw(_("Scheduled backfills must reference their schedule."))
