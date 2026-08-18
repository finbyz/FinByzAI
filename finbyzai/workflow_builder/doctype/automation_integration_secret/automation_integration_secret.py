from __future__ import annotations

import frappe
from frappe.model.document import Document


class AutomationIntegrationSecret(Document):
	def validate(self):
		self.allowed_hosts = "\n".join(
			sorted({line.strip().lower() for line in (self.allowed_hosts or "").replace(",", "\n").splitlines() if line.strip()})
		)
		if self.auth_type == "API Key" and not self.header_name:
			frappe.throw("Header Name is required for API Key authentication.")
		if self.requests_per_minute and self.requests_per_minute < 1:
			frappe.throw("Requests Per Minute must be positive.")
