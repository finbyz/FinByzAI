from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class AutomationHumanApproval(Document):
	def validate(self):
		if not self.is_new() and self.has_value_changed("approval_key"):
			frappe.throw(_("Approval identity is immutable."))
		if not self.is_new() and self.has_value_changed("status"):
			before = self.get_doc_before_save()
			if before and before.status in {"APPROVED", "EDITED", "REJECTED", "CANCELLED", "EXPIRED"}:
				frappe.throw(_("A resolved approval cannot be reopened."))
