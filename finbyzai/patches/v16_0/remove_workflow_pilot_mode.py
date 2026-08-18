import frappe


def execute():
	"""Remove obsolete pilot settings after the Automation Settings schema is synced."""
	frappe.db.delete(
		"Singles",
		{
			"doctype": "Automation Settings",
			"field": ["in", ["pilot_mode", "pilot_workflows"]],
		},
	)
	frappe.clear_cache(doctype="Automation Settings")
