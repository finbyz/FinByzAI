import frappe

from finbyzai.workflow_builder.setup import (
	ensure_automation_indexes,
	ensure_automation_roles,
	ensure_module_ownership,
)


WORKFLOW_DOCTYPES = (
	"automation_action_attempt",
	"automation_audit_event",
	"automation_backfill_job",
	"automation_consent_record",
	"automation_dead_letter",
	"automation_drip_cursor",
	"automation_effect_ledger",
	"automation_enrollment_decision",
	"automation_enrollment_ledger",
	"automation_incident",
	"automation_integration_secret",
	"automation_metric_daily",
	"automation_outbox_event",
	"automation_policy_evaluation",
	"automation_round_robin_cursor",
	"automation_run",
	"automation_run_event",
	"automation_run_token",
	"automation_schedule",
	"automation_settings",
	"automation_suppression_rule",
	"automation_timer",
	"automation_trigger_subscription",
	"automation_workflow",
	"automation_workflow_draft",
	"automation_workflow_template",
	"automation_workflow_version",
)


def execute() -> None:
	"""Transfer the installed module, sync its schema, and install its indexes."""
	ensure_module_ownership()
	# Migration resolves DocType modules immediately after pre-model patches.
	# Discard the process-local and Redis maps so Workflow Builder is resolved
	# from finbyzai/modules.txt during the same migration, not on the next restart.
	frappe.clear_cache()
	frappe.local.app_modules = None
	frappe.local.module_app = None
	frappe.setup_module_map()

	# This is deliberately one self-contained migration for the already-installed
	# development app. Reloading is idempotent and ensures every JSON field/table
	# exists before the custom composite and unique indexes are applied.
	for doctype_name in WORKFLOW_DOCTYPES:
		frappe.reload_doc("workflow_builder", "doctype", doctype_name, force=True)

	ensure_automation_roles()
	ensure_automation_indexes()
