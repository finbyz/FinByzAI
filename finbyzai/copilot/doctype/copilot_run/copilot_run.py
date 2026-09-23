# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from finbyzai.copilot import access


class CopilotRun(Document):
	pass


def get_permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	if not access.has_copilot(user):
		return "1 = 0"
	return f"`tabCopilot Run`.`owner` = {frappe.db.escape(user)}"


def has_permission(doc, ptype=None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return True
	if ptype == "create":
		return access.has_copilot(user)
	return bool(access.has_copilot(user) and doc.owner == user)
