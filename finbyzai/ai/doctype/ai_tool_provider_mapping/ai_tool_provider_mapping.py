# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import json
import re

import frappe
from frappe.model.document import Document


class AIToolProviderMapping(Document):
	def validate(self):
		if self.model_pattern:
			try:
				re.compile(self.model_pattern)
			except re.error as exc:
				frappe.throw(f"Invalid model pattern: {exc}")

		for fieldname in ("request_template", "response_mapping", "compatibility_rules"):
			value = getattr(self, fieldname, None)
			if not value:
				continue
			try:
				parsed = json.loads(value)
			except (TypeError, json.JSONDecodeError) as exc:
				frappe.throw(f"{self.meta.get_label(fieldname)} must be valid JSON: {exc}")
			if not isinstance(parsed, dict):
				frappe.throw(f"{self.meta.get_label(fieldname)} must be a JSON object")

