from __future__ import annotations

import frappe
from frappe import _

from .errors import AutomationError


def normalize_folder_path(value: str | None) -> str:
	path = str(value or "").strip()
	if not path:
		return ""
	parts = [part.strip() for part in path.split("/")]
	if any(not part or part in {".", ".."} for part in parts) or len("/".join(parts)) > 140:
		raise AutomationError(_("Use folder names separated by /, without empty or dot path segments (140 characters maximum)."))
	return "/".join(parts)


def ensure_folder_path(value: str | None) -> str:
	path = normalize_folder_path(value)
	parent = ""
	for segment in path.split("/") if path else []:
		current = f"{parent}/{segment}" if parent else segment
		if not frappe.db.exists("Automation Workflow Folder", current):
			try:
				frappe.get_doc({
					"doctype": "Automation Workflow Folder",
					"path": current,
					"folder_name": segment,
					"parent_folder": parent or None,
					"is_group": 1,
				}).insert(ignore_permissions=True)
			except frappe.DuplicateEntryError:
				# Another request may have created the same folder concurrently.
				pass
		parent = current
	return path


def list_folders() -> list[dict]:
	return frappe.get_list(
		"Automation Workflow Folder",
		fields=["name", "folder_name", "parent_folder"],
		order_by="name asc",
		limit_page_length=0,
	)


def create_folder(folder_name: str, parent_folder: str | None = None) -> dict:
	name = str(folder_name or "").strip()
	if not name or "/" in name or name in {".", ".."}:
		raise AutomationError(_("Enter one valid folder name without a slash."))
	parent = normalize_folder_path(parent_folder)
	if parent and not frappe.db.exists("Automation Workflow Folder", parent):
		raise AutomationError(_("The parent folder no longer exists."))
	path = normalize_folder_path(f"{parent}/{name}" if parent else name)
	if frappe.db.exists("Automation Workflow Folder", path):
		raise AutomationError(_("This folder already exists."))
	return {"path": ensure_folder_path(path)}


def delete_folder(path: str) -> dict:
	path = normalize_folder_path(path)
	if not path or not frappe.db.exists("Automation Workflow Folder", path):
		raise AutomationError(_("Folder not found."))
	if frappe.db.exists("Automation Workflow Folder", {"parent_folder": path}):
		raise AutomationError(_("Move or delete subfolders first."))
	if frappe.db.exists("Automation Workflow", {"folder": path}):
		raise AutomationError(_("Move workflows out of this folder first."))
	frappe.delete_doc("Automation Workflow Folder", path)
	return {"deleted": path}
