import frappe
from frappe import _
from frappe.model.document import Document

from finbyzai.workflow_builder.registry import doctype_eligibility


class AutomationWorkflow(Document):
	def validate(self):
		if self.status not in {"DRAFT", "ACTIVE", "PAUSED", "DISABLED"}:
			frappe.throw(_("Invalid automation workflow status."))
		if self.execution_user and not frappe.db.get_value("User", self.execution_user, "enabled"):
			frappe.throw(_("Execution user must be enabled."))
		if self.is_new():
			access = doctype_eligibility(self.primary_doctype)
			if not access["available"]:
				frappe.throw(access["explanation"], frappe.ValidationError)
		else:
			previous = self.get_doc_before_save()
			if previous and previous.primary_doctype != self.primary_doctype:
				frappe.throw(_("The primary DocType cannot change after a workflow is created."))
