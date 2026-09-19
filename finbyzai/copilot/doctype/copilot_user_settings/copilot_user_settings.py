# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""One row per user, holding the instructions that apply to that user's Copilot only.

The site-wide prompt lives on `Copilot Settings` and needs Copilot Admin. This is the
other half: anyone who may use the Copilot can steer their own turns without being
able to change what anyone else sees.
"""

import frappe
from frappe import _
from frappe.model.document import Document

MAX_INSTRUCTIONS = 4000


class CopilotUserSettings(Document):
	def validate(self):
		# The row is named after the user, so letting one user create a row for
		# another is the whole of the privilege escalation here.
		if self.user != frappe.session.user and not can_manage_others():
			frappe.throw(
				_("You can only change your own Copilot instructions."),
				frappe.PermissionError,
			)
		text = (self.instructions or "").strip()
		if len(text) > MAX_INSTRUCTIONS:
			frappe.throw(
				_("Keep your instructions under {0} characters; this is {1}.").format(
					MAX_INSTRUCTIONS, len(text)
				)
			)
		self.instructions = text


def can_manage_others() -> bool:
	roles = set(frappe.get_roles())
	return bool(roles & {"System Manager", "Copilot Admin"})


def get_permission_query_conditions(user: str | None = None) -> str:
	"""List view: a user sees their own row; an admin sees every row."""
	user = user or frappe.session.user
	if can_manage_others():
		return ""
	return f"""`tabCopilot User Settings`.`user` = {frappe.db.escape(user)}"""


def has_permission(doc, ptype=None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	return bool(doc.user == user or can_manage_others())


def for_user(user: str | None = None) -> str:
	"""This user's own instructions, or an empty string."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return ""
	return (frappe.db.get_value("Copilot User Settings", {"user": user}, "instructions") or "").strip()


def save_for_user(instructions: str, user: str | None = None) -> str:
	"""Create or update the caller's own row."""
	user = user or frappe.session.user
	text = (instructions or "").strip()
	name = frappe.db.get_value("Copilot User Settings", {"user": user}, "name")
	if name:
		doc = frappe.get_doc("Copilot User Settings", name)
		doc.instructions = text
		doc.save()
	else:
		doc = frappe.get_doc(
			{"doctype": "Copilot User Settings", "user": user, "instructions": text}
		).insert()
	return doc.name
