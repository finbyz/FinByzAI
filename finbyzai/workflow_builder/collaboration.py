from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from .errors import AutomationPermissionError


def _workflow(workflow_id: str, permission: str = "read"):
	workflow = frappe.get_doc("Automation Workflow", workflow_id)
	workflow.check_permission(permission)
	return workflow


def list_comments(workflow_id: str, step_id: str | None = None, include_resolved: bool = True) -> dict:
	workflow = _workflow(workflow_id)
	filters: dict = {"workflow": workflow.name}
	if step_id:
		filters["step_id"] = step_id
	if not include_resolved:
		filters["resolved"] = 0
	return {"rows": frappe.get_list(
		"Automation Workflow Comment",
		filters=filters,
		fields=["name", "workflow", "step_id", "content", "mention_users_json", "resolved", "resolved_by", "resolved_at", "owner", "creation", "modified"],
		order_by="creation asc",
		limit=500,
	)}


def create_comment(workflow_id: str, content: str, step_id: str | None = None, mention_users=None) -> dict:
	workflow = _workflow(workflow_id, "write")
	users = frappe.parse_json(mention_users) if isinstance(mention_users, str) else mention_users
	users = list(dict.fromkeys(str(user).strip() for user in (users or []) if str(user).strip()))[:20]
	for user in users:
		if not frappe.db.exists("User", {"name": user, "enabled": 1}):
			frappe.throw(_("User {0} is not available for mentions.").format(user))
	doc = frappe.get_doc({
		"doctype": "Automation Workflow Comment",
		"workflow": workflow.name,
		"step_id": str(step_id or "").strip() or None,
		"content": content,
		"mention_users_json": json.dumps(users),
	}).insert()
	for user in users:
		if user == frappe.session.user:
			continue
		frappe.get_doc({
			"doctype": "Notification Log",
			"subject": _("You were mentioned in workflow {0}").format(workflow.title),
			"email_content": str(content or ""),
			"for_user": user,
			"type": "Alert",
			"document_type": "Automation Workflow",
			"document_name": workflow.name,
			"from_user": frappe.session.user,
		}).insert(ignore_permissions=True)
	return {"name": doc.name}


def set_comment_resolved(comment_id: str, resolved: bool) -> dict:
	doc = frappe.get_doc("Automation Workflow Comment", comment_id)
	_workflow(doc.workflow, "write")
	doc.resolved = cint(resolved)
	doc.resolved_by = frappe.session.user if doc.resolved else None
	doc.resolved_at = now_datetime() if doc.resolved else None
	doc.save()
	return {"name": doc.name, "resolved": bool(doc.resolved)}


def delete_comment(comment_id: str) -> dict:
	doc = frappe.get_doc("Automation Workflow Comment", comment_id)
	_workflow(doc.workflow, "write")
	if doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
		raise AutomationPermissionError(_("Only the comment author or a System Manager can delete this comment."))
	doc.delete()
	return {"deleted": True}


def workflow_connections(workflow_id: str) -> dict:
	workflow = _workflow(workflow_id)
	draft = frappe.db.get_value(
		"Automation Workflow Draft",
		{"workflow": workflow.name},
		["graph_json", "settings_json"],
		as_dict=True,
	) or frappe._dict()
	graph_value = draft.get("graph_json") or "{}"
	graph = json.loads(graph_value) if isinstance(graph_value, str) else graph_value
	settings_value = draft.get("settings_json") or "{}"
	settings = json.loads(settings_value) if isinstance(settings_value, str) else settings_value
	connections: dict[tuple[str, str], dict] = {}

	def add(kind: str, name, node_id: str, detail: str = ""):
		value = str(name or "").strip()
		if not value:
			return
		key = (kind, value)
		row = connections.setdefault(key, {"kind": kind, "name": value, "detail": detail, "node_ids": []})
		if node_id not in row["node_ids"]:
			row["node_ids"].append(node_id)

	add("ERP DocType", graph.get("primary_doctype"), "enrollment", "Enrolled record")
	communication = settings.get("communication") or {}
	add("Email Account", communication.get("default_sender_email"), "workflow-settings", "Workflow default sender")
	for node in graph.get("nodes") or []:
		node_id = str(node.get("id") or "")
		kind = str(node.get("type") or "")
		config = node.get("config") or {}
		if kind == "action.send_email":
			add("Email Template", config.get("email_template"), node_id)
			add("Email Account", config.get("sender_email") or config.get("email_account") or config.get("sender"), node_id)
			add("Subscription Topic", config.get("subscription_topic"), node_id, "Finbyz Reach preference")
		elif kind == "action.call_subflow":
			add("Subflow", config.get("subflow_id"), node_id)
		elif kind in {"action.create_record", "action.copy_record", "action.manage_association"}:
			add("ERP DocType", config.get("target_doctype"), node_id)
		elif kind == "action.webhook":
			add("External endpoint", config.get("url"), node_id)
			add("Integration secret", config.get("integration_secret"), node_id)
		elif kind == "action.instagram_message":
			add("External endpoint", config.get("url"), node_id, "Meta messaging endpoint")
			add("Integration secret", config.get("integration_secret"), node_id)
		elif kind == "action.send_sms":
			add("Frappe setting", "SMS Settings", node_id)
		elif kind == "action.asana" or kind.startswith("action.asana_"):
			add("Installed integration", "Asana Integration", node_id)
		elif kind == "trigger.event":
			add("Business event", config.get("event_topic"), node_id)
		elif kind == "trigger.any":
			for entry in config.get("triggers") or []:
				entry_config = entry.get("config") or {}
				add("Business event", entry_config.get("event_topic"), node_id)
	return {"rows": sorted(connections.values(), key=lambda row: (row["kind"], row["name"]))}
