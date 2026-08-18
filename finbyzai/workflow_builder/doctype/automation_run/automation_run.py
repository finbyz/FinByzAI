import frappe
from frappe.utils import cint
from frappe.model.document import Document


class AutomationRun(Document):
	def before_save(self):
		if not self.is_new():
			self.state_version = cint(
				frappe.db.get_value("Automation Run Event", {"run": self.name}, [{"MAX": "sequence_no"}])
			)
