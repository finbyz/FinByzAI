from __future__ import annotations

import json

import frappe
from frappe.model.document import Document


class AutomationAIProfileVersion(Document):
	"""Immutable, non-secret AI configuration used by published workflows."""

	_IMMUTABLE_FIELDS = {
		"source_agent",
		"config_hash",
		"provider",
		"model",
		"knowledge_base",
		"snapshot_json",
	}

	def validate(self):
		from finbyzai.workflow_builder.ai_support import canonical_profile_hash

		try:
			snapshot = json.loads(self.snapshot_json or "{}")
		except (TypeError, ValueError):
			frappe.throw("AI profile snapshot must be valid JSON", exc=frappe.ValidationError)
		if canonical_profile_hash(snapshot) != self.config_hash:
			frappe.throw("AI profile snapshot does not match its immutable hash")
		if not self.is_new() and any(self.has_value_changed(field) for field in self._IMMUTABLE_FIELDS):
			frappe.throw("Published AI profile versions cannot be changed")
