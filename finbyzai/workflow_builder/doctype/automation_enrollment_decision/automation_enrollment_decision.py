from frappe.model.document import Document


class AutomationEnrollmentDecision(Document):
	"""Append-only explanation of an enrollment outcome."""

	def before_save(self):
		if not self.is_new():
			raise PermissionError("Automation enrollment decisions are immutable.")
