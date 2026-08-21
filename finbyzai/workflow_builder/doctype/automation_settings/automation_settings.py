import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AutomationSettings(Document):
	def validate(self):
		self.history_retention_days = cint(self.history_retention_days)
		self.log_cleanup_interval_hours = cint(self.log_cleanup_interval_hours)
		self.log_cleanup_batch_size = cint(self.log_cleanup_batch_size)
		if not 180 <= self.history_retention_days <= 3650:
			frappe.throw(_("Workflow log retention must be between 180 and 3650 days."))
		if not 1 <= self.log_cleanup_interval_hours <= 168:
			frappe.throw(_("Log cleanup interval must be between 1 and 168 hours."))
		if not 100 <= self.log_cleanup_batch_size <= 5000:
			frappe.throw(_("Log cleanup batch size must be between 100 and 5000 records."))
