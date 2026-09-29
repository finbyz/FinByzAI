import frappe

from finbyzai.workflow_builder.errors import AutomationError
from finbyzai.workflow_builder.folders import ensure_folder_path


def execute():
	for row in frappe.get_all(
		"Automation Workflow",
		filters={"folder": ["!=", ""]},
		fields=["name", "folder"],
		limit_page_length=0,
	):
		try:
			path = ensure_folder_path(row.folder)
		except AutomationError:
			# Keep an invalid legacy value intact for the operator to move manually.
			frappe.log_error(title="Workflow folder migration skipped", message=f"Workflow {row.name} has an invalid folder path: {row.folder}")
			continue
		if path != row.folder:
			frappe.db.set_value("Automation Workflow", row.name, "folder", path, update_modified=False)
