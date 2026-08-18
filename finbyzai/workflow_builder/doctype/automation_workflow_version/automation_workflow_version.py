import frappe
from frappe import _
from frappe.model.document import Document


class AutomationWorkflowVersion(Document):
	def before_save(self):
		if not self.is_new():
			frappe.throw(_("Published workflow versions are immutable."))

	def on_trash(self):
		frappe.throw(_("Published workflow versions cannot be deleted."))
