from __future__ import annotations

import frappe

from .constants import AUTOMATION_ROLES


WORKFLOW_MODULE = "Workflow Builder"


INDEXES = {
	"Automation Workflow": [
		(["status", "modified"], "idx_automation_workflow_status"),
		(["primary_doctype", "status"], "idx_automation_workflow_doctype_status"),
	],
	"Automation Outbox Event": [
		(["status", "available_at", "creation"], "idx_automation_outbox_pending"),
		(["status", "lease_until", "creation"], "idx_automation_outbox_lease"),
		(["object_doctype", "object_name"], "idx_automation_outbox_record"),
	],
	"Automation Run": [
		(["workflow", "status", "creation"], "idx_automation_run_workflow_status"),
		(["record_doctype", "record_name", "creation"], "idx_automation_run_record"),
		(["record_doctype", "record_name", "status", "creation"], "idx_automation_run_active_record"),
	],
	"Automation Run Token": [
		(["status", "available_at", "creation"], "idx_automation_token_ready"),
		(["run", "status"], "idx_automation_token_run"),
	],
	"Automation Run Event": [
		(["run", "sequence_no"], "idx_automation_run_event_sequence"),
	],
	"Automation Timer": [
		(["status", "due_at", "creation"], "idx_automation_timer_due"),
		(["run", "status"], "idx_automation_timer_run"),
	],
	"Automation Trigger Subscription": [
		(["primary_doctype", "event_type", "active"], "idx_automation_subscription_match"),
	],
	"Automation Backfill Job": [
		(["status", "modified"], "idx_automation_backfill_status"),
		(["workflow", "status", "creation"], "idx_automation_backfill_workflow_status"),
		(["status", "next_batch_at"], "idx_automation_backfill_due"),
	],
	"Automation Schedule": [
		(["enabled", "next_run_at"], "idx_automation_schedule_due"),
	],
	"Automation Consent Record": [
		(["record_doctype", "record_name", "channel", "purpose", "recipient", "effective_at"], "idx_automation_consent_lookup"),
	],
	"Automation Enrollment Decision": [
		(["workflow", "record_key", "creation"], "idx_automation_decision_record"),
		(["workflow", "decision", "creation"], "idx_automation_decision_outcome"),
		(["run", "creation"], "idx_automation_decision_run"),
	],
	"Automation Incident": [
		(["status", "severity", "last_seen_at"], "idx_automation_incident_open"),
		(["workflow", "status", "last_seen_at"], "idx_automation_incident_workflow"),
	],
	"Automation Dead Letter": [
		(["status", "source_type", "creation"], "idx_automation_dead_letter_open"),
		(["workflow", "status", "creation"], "idx_automation_dead_letter_workflow"),
	],
	"Automation Suppression Rule": [
		(["workflow", "enabled", "priority"], "idx_automation_suppression_match"),
	],
	"Automation Metric Daily": [
		(["workflow", "metric_date"], "idx_automation_metric_workflow_date"),
	],
	"Automation Policy Evaluation": [
		(["workflow", "outcome", "creation"], "idx_automation_policy_outcome"),
		(["event_id", "creation"], "idx_automation_policy_event"),
	],
}

UNIQUES = {
	"Automation Workflow": [(["creation_key"], "uq_automation_workflow_creation_key")],
	"Automation Workflow Draft": [(["workflow"], "uq_automation_draft_workflow")],
	"Automation Workflow Version": [(["workflow", "version_no"], "uq_automation_version_number")],
	"Automation Outbox Event": [(["event_id"], "uq_automation_outbox_event_id")],
	"Automation Enrollment Ledger": [(["workflow", "record_key", "occurrence_key"], "uq_automation_enrollment_occurrence")],
	"Automation Run Token": [(["run", "node_id", "occurrence"], "uq_automation_run_token")],
	"Automation Run Event": [(["run", "sequence_no"], "uq_automation_run_event_sequence")],
	"Automation Effect Ledger": [(["effect_key"], "uq_automation_effect_key")],
	"Automation Incident": [(["fingerprint"], "uq_automation_incident_fingerprint")],
	"Automation Dead Letter": [(["source_type", "source_name"], "uq_automation_dead_letter_source")],
	"Automation Metric Daily": [(["metric_date", "workflow", "workflow_version"], "uq_automation_metric_day")],
	"Automation Policy Evaluation": [(["run", "event_id"], "uq_automation_policy_run_event")],
	"Automation Round Robin Cursor": [(["cursor_key"], "uq_automation_round_robin_cursor")],
}


def ensure_automation_roles() -> None:
	role_names = set().union(*AUTOMATION_ROLES.values())
	for role_name in sorted(role_names):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def ensure_module_ownership() -> None:
	"""Keep Frappe's database module map aligned with the owning app."""
	if frappe.db.exists("Module Def", WORKFLOW_MODULE):
		frappe.db.set_value("Module Def", WORKFLOW_MODULE, "app_name", "finbyzai", update_modified=False)


def ensure_automation_indexes() -> None:
	for doctype, definitions in UNIQUES.items():
		if not frappe.db.table_exists(doctype):
			continue
		for fields, name in definitions:
			frappe.db.add_unique(doctype, fields, name)
	for doctype, definitions in INDEXES.items():
		if not frappe.db.table_exists(doctype):
			continue
		for fields, name in definitions:
			if not frappe.db.has_index(f"tab{doctype}", name):
				frappe.db.add_index(doctype, fields, name)


def quarantine_invalid_active_versions() -> None:
	"""Pause active versions that no longer satisfy the current graph contract."""
	if not frappe.db.table_exists("Automation Workflow Version"):
		return

	from .authoring import create_audit, validate_published_version

	for row in frappe.get_all(
		"Automation Workflow",
		filters={"status": "ACTIVE", "active_version": ["is", "set"]},
		fields=["name", "active_version"],
		limit_page_length=0,
	):
		try:
			validation = validate_published_version(row.name, row.active_version)
		except Exception as exc:
			validation = {
				"valid": False,
				"issues": [{"code": "VERSION_VALIDATION_FAILED", "message": str(exc)[:500]}],
			}
		if validation["valid"]:
			continue
		frappe.db.set_value("Automation Workflow", row.name, "status", "PAUSED", update_modified=False)
		frappe.db.set_value(
			"Automation Trigger Subscription", {"workflow": row.name}, "active", 0, update_modified=False
		)
		codes = list(dict.fromkeys(issue.get("code") for issue in validation["issues"] if issue.get("code")))
		create_audit(
			row.name,
			"WORKFLOW_AUTO_PAUSED_INVALID_VERSION",
			{"version": row.active_version, "validation_codes": codes[:20]},
		)


def after_install() -> None:
	ensure_module_ownership()
	ensure_automation_roles()
	ensure_automation_indexes()


def after_migrate() -> None:
	ensure_module_ownership()
	ensure_automation_roles()
	quarantine_invalid_active_versions()
