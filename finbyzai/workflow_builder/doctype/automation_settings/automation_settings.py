import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AutomationSettings(Document):
	def validate(self):
		self.history_retention_days = cint(self.history_retention_days)
		self.log_cleanup_interval_hours = cint(self.log_cleanup_interval_hours)
		self.log_cleanup_batch_size = cint(self.log_cleanup_batch_size)
		if not 180 <= self.history_retention_days <= 3650:
			frappe.throw(_("Workflow log retention must be between 180 and 3650 days."))
		if not 1 <= self.log_cleanup_interval_hours <= 168:
			frappe.throw(_("Log cleanup interval must be between 1 and 168 hours."))
		if not 100 <= self.log_cleanup_batch_size <= 5000:
			frappe.throw(_("Log cleanup batch size must be between 100 and 5000 records."))
		ranges = {
			"ai_max_context_characters": (5000, 200000, _("AI context characters")),
			"ai_max_thread_messages": (1, 50, _("AI thread messages")),
			"ai_max_output_tokens": (128, 8192, _("AI output tokens")),
			"ai_default_timeout_seconds": (10, 300, _("AI timeout")),
			"ai_daily_token_budget": (1, 1000000000, _("AI daily token budget")),
			"ai_max_provider_retries": (0, 3, _("AI provider retries")),
			"ai_circuit_failure_threshold": (2, 20, _("AI circuit threshold")),
			"ai_circuit_cooldown_minutes": (1, 120, _("AI circuit cooldown")),
			"ai_test_requests_per_10_minutes": (1, 100, _("AI test rate limit")),
			"ai_evidence_retention_days": (30, 3650, _("AI evidence retention")),
			"ai_authoring_max_output_tokens": (1024, 8192, _("AI authoring output tokens")),
			"ai_authoring_daily_request_budget": (1, 5000, _("AI authoring daily request budget")),
		}
		for fieldname, (minimum, maximum, label) in ranges.items():
			value = cint(self.get(fieldname))
			if not minimum <= value <= maximum:
				frappe.throw(_("{0} must be between {1} and {2}.").format(label, minimum, maximum))
		if cint(self.ai_authoring_enabled):
			# The Generator is the agent that actually runs. The deprecated single
			# agent is still accepted so older sites keep validating.
			agent_name = (
				str(self.ai_authoring_generator_agent or "").strip()
				or str(self.ai_authoring_agent or "").strip()
			)
			if not agent_name or not frappe.db.exists("AI Agent", agent_name):
				frappe.throw(_("Choose an existing AI Workflow Generator Agent."))
			for fieldname, label in (
				("ai_authoring_editor_agent", _("AI Workflow Editor Agent")),
				("ai_authoring_agent", _("AI Workflow Authoring Agent")),
			):
				value = str(self.get(fieldname) or "").strip()
				if value and not frappe.db.exists("AI Agent", value):
					frappe.throw(_("{0} no longer exists. Clear it or choose another.").format(label))
