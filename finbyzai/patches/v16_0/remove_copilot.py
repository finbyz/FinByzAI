# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Remove Copilot DocTypes, data, roles, module, and Copilot-only AI records."""

import frappe

COPILOT_DOCTYPES = (
	"Copilot Scheduled Task",
	"Copilot Message",
	"Copilot Run",
	"Copilot Conversation",
	"Copilot User Settings",
	"Copilot Available Agent",
	"Copilot Available Model",
	"Copilot Settings",
)

COPILOT_ROLES = ("Copilot Admin", "Copilot User")


def execute() -> None:
	# 1. Remove Copilot DocTypes and their data/tables.
	for doctype in COPILOT_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=1, ignore_permissions=True)

	frappe.db.commit()
	for doctype in COPILOT_DOCTYPES:
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype}`")

	frappe.db.delete("Singles", {"doctype": ["in", list(COPILOT_DOCTYPES)]})

	# 2. Remove Copilot-only AI Agent ("Copilot") and AI Tools (module="Copilot").
	if frappe.db.exists("AI Agent", "Copilot"):
		frappe.delete_doc("AI Agent", "Copilot", force=1, ignore_permissions=True)

	copilot_tools = frappe.get_all("AI Tool", filters={"module": "Copilot"}, pluck="name")
	if copilot_tools:
		frappe.db.delete("AI Agent Tool", {"tool": ["in", copilot_tools]})
		for tool_name in copilot_tools:
			frappe.delete_doc("AI Tool", tool_name, force=1, ignore_permissions=True)

	# 3. Remove Custom DocPerms and Role records for Copilot roles.
	frappe.db.delete("Custom DocPerm", {"role": ["in", list(COPILOT_ROLES)]})
	frappe.db.delete("Custom DocPerm", {"parent": ["in", list(COPILOT_DOCTYPES)]})
	frappe.db.delete("Has Role", {"role": ["in", list(COPILOT_ROLES)]})
	for role_name in COPILOT_ROLES:
		if frappe.db.exists("Role", role_name):
			frappe.delete_doc("Role", role_name, force=1, ignore_permissions=True)

	# 4. Remove Copilot Module Def.
	if frappe.db.exists("Module Def", "Copilot"):
		frappe.delete_doc("Module Def", "Copilot", force=1, ignore_permissions=True)

	frappe.clear_cache()
