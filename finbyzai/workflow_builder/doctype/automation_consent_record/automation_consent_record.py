import frappe
from frappe import _
from frappe.model.document import Document


class AutomationConsentRecord(Document):
	def before_save(self):
		if not self.is_new() and "System Manager" not in frappe.get_roles():
			frappe.throw(_("Automation Consent Records are immutable and cannot be modified."))

	def on_trash(self):
		if "System Manager" not in frappe.get_roles():
			frappe.throw(_("Automation Consent Records are immutable and cannot be deleted."))

		