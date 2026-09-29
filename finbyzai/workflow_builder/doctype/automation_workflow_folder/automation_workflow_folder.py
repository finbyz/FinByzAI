import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet


class AutomationWorkflowFolder(NestedSet):
	nsm_parent_field = "parent_folder"

	def validate(self):
		from finbyzai.workflow_builder.folders import normalize_folder_path

		path = normalize_folder_path(self.path)
		parent = normalize_folder_path(self.parent_folder)
		if not path or path != self.path or path.rsplit("/", 1)[-1] != self.folder_name:
			frappe.throw(_("Invalid workflow folder path."))
		if (path.rsplit("/", 1)[0] if "/" in path else "") != parent:
			frappe.throw(_("The folder path must match its parent."))
		if parent and not frappe.db.exists(self.doctype, parent):
			frappe.throw(_("Parent folder does not exist."))

	def on_trash(self):
		if frappe.db.exists("Automation Workflow", {"folder": self.name}):
			frappe.throw(_("Move workflows out of this folder first."))
		super().on_trash()
