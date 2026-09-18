# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CopilotConversation(Document):
	def on_trash(self):
		# Each turn's Copilot Run links back to this conversation, which blocks
		# deletion with LinkExistsError unless those runs go first.
		for name in frappe.get_all("Copilot Run", filters={"conversation": self.name}, pluck="name"):
			frappe.delete_doc("Copilot Run", name, ignore_permissions=True, force=1)
