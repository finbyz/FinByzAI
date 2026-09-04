from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from .constants import AUTOMATION_ROLES, RUN_TERMINAL_STATUSES
from .errors import AutomationConflictError, AutomationError, AutomationPermissionError
from .principal import current_execution_user
from .schema import canonical_json, parse_object, resolve_value


TERMINAL_APPROVAL_STATUSES = {"APPROVED", "EDITED", "REJECTED", "CANCELLED", "EXPIRED"}


def _approval_key(run_name: str, node_id: str, occurrence: int) -> str:
	return frappe.utils.sha256_hash(f"{run_name}\0{node_id}\0{occurrence}")


def _enabled_system_user(user: str) -> bool:
	row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	return bool(row and cint(row.enabled) and row.user_type == "System User")


def _compact_evidence(value: Any) -> str:
	if value in (None, ""):
		return "{}"
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			value = {"summary": value[:4000]}
	if not isinstance(value, (dict, list)):
		value = {"value": value}
	encoded = canonical_json(value)
	if len(encoded.encode("utf-8")) > 64 * 1024:
		raise AutomationError(_("Approval evidence is larger than 64 KB."))
	return encoded


def create_approval(run, token, node: dict, *, record, outputs: dict[str, Any]) -> dict:
	"""Create one durable review request and pause the current token.

	The stable key makes worker retries idempotent. A recovered token reuses the
	existing review instead of assigning a second ToDo.
	"""
	config = node.get("config") or {}
	reviewer = str(config.get("reviewer") or "").strip()
	if not _enabled_system_user(reviewer):
		raise AutomationError(_("The human-approval reviewer must be an enabled System User."))
	draft_text = str(resolve_value(config.get("draft_text"), record=record, outputs=outputs) or "").strip()
	if not draft_text:
		raise AutomationError(_("The human-approval draft resolved to an empty value."))
	if len(draft_text) > 20000:
		raise AutomationError(_("The human-approval draft cannot exceed 20,000 characters."))
	evidence = resolve_value(config.get("evidence"), record=record, outputs=outputs)
	ai_attempt = str(resolve_value(config.get("ai_attempt"), record=record, outputs=outputs) or "").strip()
	if ai_attempt:
		attempt_run = frappe.db.get_value("Automation AI Attempt", ai_attempt, "run")
		if attempt_run != run.name:
			raise AutomationError(_("The selected AI evidence does not belong to this workflow run."))
	key = _approval_key(run.name, node["id"], cint(token.occurrence))
	existing_name = frappe.db.get_value("Automation Human Approval", {"approval_key": key}, "name")
	if existing_name:
		existing = frappe.get_doc("Automation Human Approval", existing_name)
		output = approval_output(existing)
		if existing.status == "PENDING":
			return {"status": "WAIT_APPROVAL", "output": output}
		return {
			"status": "COMPLETE",
			"handle": "approved" if existing.status in {"APPROVED", "EDITED"} else "rejected",
			"output": output,
		}

	now = now_datetime()
	approval = frappe.get_doc(
		{
			"doctype": "Automation Human Approval",
			"approval_key": key,
			"workflow": run.workflow,
			"run": run.name,
			"token": token.name,
			"node_id": node["id"],
			"ai_attempt": ai_attempt or None,
			"record_doctype": run.record_doctype,
			"record_name": run.record_name,
			"reviewer": reviewer,
			"status": "PENDING",
			"allow_edit": cint(config.get("allow_edit", 1)),
			"title": str(config.get("title") or _("Review workflow draft"))[:140],
			"instructions": str(config.get("instructions") or "")[:2000],
			"draft_text": draft_text,
			"evidence_json": _compact_evidence(evidence),
			"created_at": now,
			"expires_at": add_to_date(now, days=min(max(cint(config.get("expires_days") or 7), 1), 30)),
		}
	).insert(ignore_permissions=True)
	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": reviewer,
			"assigned_by": current_execution_user(),
			"reference_type": run.record_doctype,
			"reference_name": run.record_name,
			"description": _("{0} — workflow {1}, run {2}").format(approval.title, run.workflow, run.name),
			"priority": "High",
			"status": "Open",
			"date": approval.expires_at.date() if approval.expires_at else None,
		}
	).insert(ignore_permissions=True)
	approval.db_set("todo", todo.name, update_modified=False)
	frappe.publish_realtime(
		"automation_approval_updated",
		{"approval_id": approval.name, "workflow_id": run.workflow, "status": "PENDING"},
		user=reviewer,
		after_commit=True,
	)
	return {"status": "WAIT_APPROVAL", "output": approval_output(approval)}


def approval_output(approval) -> dict:
	return {
		"approval_id": approval.name,
		"status": approval.status,
		"draft_text": approval.draft_text,
		"final_text": approval.final_text or None,
		"edited": approval.status == "EDITED",
		"decision_comment": approval.decision_comment or None,
		"reviewed_by": approval.reviewed_by or None,
		"reviewed_at": str(approval.reviewed_at) if approval.reviewed_at else None,
	}


def _can_review(approval, user: str) -> bool:
	if approval.reviewer == user:
		return True
	roles = set(frappe.get_roles(user))
	return bool(roles & AUTOMATION_ROLES["operator"])


def list_approvals(*, workflow: str | None = None, status: str = "PENDING", start: int = 0, page_length: int = 50) -> dict:
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	is_operator = bool(roles & AUTOMATION_ROLES["operator"])
	filters: dict[str, Any] = {}
	if status:
		filters["status"] = str(status).upper()
	if workflow:
		if is_operator:
			workflow_doc = frappe.get_doc("Automation Workflow", workflow)
			workflow_doc.check_permission("read")
		filters["workflow"] = workflow
	if not is_operator:
		filters["reviewer"] = user
	limit = min(max(cint(page_length), 1), 100)
	start = max(cint(start), 0)
	rows = frappe.get_list(
		"Automation Human Approval",
		filters=filters,
		fields=[
			"name", "workflow", "run", "node_id", "record_doctype", "record_name", "reviewer",
			"status", "allow_edit", "title", "instructions", "draft_text", "final_text", "evidence_json",
			"decision_comment", "created_at", "expires_at", "reviewed_at", "reviewed_by",
		],
		order_by="created_at desc",
		start=start,
		limit=limit + 1,
		ignore_permissions=True,
	)
	return {"rows": rows[:limit], "has_more": len(rows) > limit}


def resolve_approval(approval_name: str, decision: str, *, final_text: str | None = None, comment: str | None = None) -> dict:
	return _resolve_approval(
		approval_name,
		decision,
		final_text=final_text,
		comment=comment,
		reviewer_user=frappe.session.user,
	)


def _resolve_approval(
	approval_name: str,
	decision: str,
	*,
	final_text: str | None = None,
	comment: str | None = None,
	reviewer_user: str | None,
	automatic_expiry: bool = False,
) -> dict:
	"""Resolve one approval under an explicit authority contract.

	Interactive decisions require an authorised reviewer. The scheduler may only
	expire an already-due pending request; it cannot approve, edit, or reject a
	non-due request. This removes the former Administrator impersonation path.
	"""
	decision = str(decision or "").strip().upper()
	if decision not in {"APPROVE", "EDIT_AND_APPROVE", "REJECT"}:
		raise AutomationError(_("Choose approve, edit and approve, or reject."))
	if automatic_expiry and decision != "REJECT":
		raise AutomationError(_("Automatic approval resolution may only expire a request."))
	identity = frappe.db.get_value(
		"Automation Human Approval", approval_name, ["name", "run", "token"], as_dict=True
	)
	if not identity:
		raise AutomationError(_("Approval request was not found."))
	# Match the runtime's run -> token -> child lock order to avoid a reviewer
	# racing a cancellation or worker recovery into a database deadlock.
	run = frappe.get_doc("Automation Run", identity.run, for_update=True)
	token = frappe.get_doc("Automation Run Token", identity.token, for_update=True)
	frappe.db.get_value("Automation Human Approval", approval_name, "name", for_update=True)
	approval = frappe.get_doc("Automation Human Approval", approval_name)
	if not automatic_expiry and (not reviewer_user or not _can_review(approval, reviewer_user)):
		raise AutomationPermissionError(_("Only the assigned reviewer or an Automation Operator can decide this approval."))
	if approval.status != "PENDING":
		raise AutomationConflictError(_("This approval was already resolved as {0}.").format(approval.status))
	if run.status in RUN_TERMINAL_STATUSES or token.status != "WAITING" or token.node_id != approval.node_id:
		raise AutomationConflictError(_("The workflow run is no longer waiting for this approval."))
	if automatic_expiry and (not approval.expires_at or approval.expires_at > now_datetime()):
		raise AutomationConflictError(_("This approval is not due for automatic expiry."))
	requested_text = str(final_text if final_text is not None else approval.draft_text).strip()
	text_changed = requested_text != str(approval.draft_text or "")
	if (decision == "EDIT_AND_APPROVE" or text_changed) and not cint(approval.allow_edit):
		raise AutomationError(_("This approval does not allow the reviewer to edit the draft."))
	if decision == "APPROVE" and text_changed:
		decision = "EDIT_AND_APPROVE"
	if automatic_expiry or (approval.expires_at and approval.expires_at < now_datetime()):
		decision = "REJECT"
		comment = comment or _("Approval expired before review.")
		status = "EXPIRED"
	else:
		status = {"APPROVE": "APPROVED", "EDIT_AND_APPROVE": "EDITED", "REJECT": "REJECTED"}[decision]
	resolved_text = requested_text
	if status in {"APPROVED", "EDITED"} and not resolved_text:
		raise AutomationError(_("Approved text cannot be empty."))
	if len(resolved_text) > 20000:
		raise AutomationError(_("Approved text cannot exceed 20,000 characters."))
	if status == "EDITED" and resolved_text == str(approval.draft_text or ""):
		status = "APPROVED"
	now = now_datetime()
	approval.status = status
	approval.final_text = resolved_text if status in {"APPROVED", "EDITED"} else None
	approval.decision_comment = str(comment or "")[:2000]
	approval.reviewed_at = now
	approval.reviewed_by = None if automatic_expiry else reviewer_user
	approval.save(ignore_permissions=True)
	if approval.todo:
		frappe.db.set_value("ToDo", approval.todo, {"status": "Closed", "date": now.date()}, update_modified=False)

	version = frappe.get_doc("Automation Workflow Version", run.workflow_version)
	graph = parse_object(version.graph_json, "published workflow graph")
	output = approval_output(approval)
	attempt_name = frappe.db.get_value(
		"Automation Action Attempt", {"token": token.name, "status": "WAITING"}, "name", order_by="attempt_no desc"
	)
	if attempt_name:
		frappe.db.set_value(
			"Automation Action Attempt",
			attempt_name,
			{"status": "COMPLETED", "output_json": json.dumps(output, default=str), "completed_at": now},
			update_modified=False,
		)
	from . import engine

	engine._append_event(
		run.name,
		"APPROVAL_RESOLVED",
		node_id=approval.node_id,
		payload={
			"approval_id": approval.name,
			"status": status,
			"resolved_by": "SYSTEM_EXPIRY" if automatic_expiry else reviewer_user,
		},
	)
	engine._finish_or_continue(
		run,
		token,
		graph,
		{"status": "COMPLETE", "handle": "approved" if status in {"APPROVED", "EDITED"} else "rejected", "output": output},
	)
	frappe.publish_realtime(
		"automation_approval_updated",
		{"approval_id": approval.name, "workflow_id": approval.workflow, "status": status},
		user=approval.reviewer,
		after_commit=True,
	)
	return output


def expire_due_approvals(limit: int = 100) -> int:
	if not frappe.db.table_exists("Automation Human Approval"):
		return 0
	names = frappe.get_all(
		"Automation Human Approval",
		filters={"status": "PENDING", "expires_at": ["<=", now_datetime()]},
		pluck="name",
		order_by="expires_at asc",
		limit=min(max(cint(limit), 1), 500),
	)
	resolved = 0
	for name in names:
		try:
			_resolve_approval(
				name,
				"REJECT",
				comment=_("Approval expired without a decision."),
				reviewer_user=None,
				automatic_expiry=True,
			)
		except AutomationConflictError:
			continue
		resolved += 1
	return resolved
