import frappe
from frappe.model.document import Document


class AutomationPolicyEvaluation(Document):
	"""Append-only idempotency record for event-driven run policy checks."""

	def before_save(self):
		if not self.is_new():
			raise frappe.PermissionError("Automation policy evaluations are immutable.")
