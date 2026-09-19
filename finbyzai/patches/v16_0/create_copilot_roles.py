# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Create the Copilot roles before any DocType that grants permission to them syncs.

This runs pre_model_sync on purpose: Frappe validates a DocType's permission rows
against existing Role records, so a DocType shipping a `Copilot Admin` perm would
fail to import on a site that has never seen the role.
"""

import frappe

ROLES = {
	"Copilot Admin": "Manages the Copilot for everyone: agents, tools, knowledge bases, "
	"and the system prompt that applies to every user.",
	"Copilot User": "Uses the Copilot. Reads agents, tools and knowledge bases, and keeps "
	"their own instructions.",
}


def execute() -> None:
	for role_name, description in ROLES.items():
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
				"description": description,
			}
		).insert(ignore_permissions=True)
