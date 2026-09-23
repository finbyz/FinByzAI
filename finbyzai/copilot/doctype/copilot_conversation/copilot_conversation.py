# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from finbyzai.copilot import access


class CopilotConversation(Document):
	def on_trash(self):
		# Each turn's Copilot Run links back to this conversation, which blocks
		# deletion with LinkExistsError unless those runs go first.
		for name in frappe.get_all("Copilot Run", filters={"conversation": self.name}, pluck="name"):
			frappe.delete_doc("Copilot Run", name, ignore_permissions=True, force=1)


def get_permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	if not access.has_copilot(user):
		return "1 = 0"
	return f"`tabCopilot Conversation`.`owner` = {frappe.db.escape(user)}"


def has_permission(doc, ptype=None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return True
	if ptype == "create":
		return access.has_copilot(user)
	return bool(access.has_copilot(user) and doc.owner == user)
