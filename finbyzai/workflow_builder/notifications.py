from __future__ import annotations

from typing import Any

import frappe
from frappe.desk.doctype.notification_log.notification_log import (
	enqueue_create_notification,
)


def enqueue_notification_for_user(user: str | None, doc: dict[str, Any]) -> bool:
	"""Queue a Frappe notification for an enabled User document name.

	Frappe's notification helper accepts email addresses even though the created
	Notification Log stores the User document name. Keep that integration detail
	here so callers can consistently work with User links.
	"""
	if not user:
		return False

	recipient = frappe.db.get_value("User", user, ["enabled", "email"], as_dict=True)
	if not recipient or not recipient.enabled or not recipient.email:
		return False

	enqueue_create_notification(recipient.email, doc)
	return True
