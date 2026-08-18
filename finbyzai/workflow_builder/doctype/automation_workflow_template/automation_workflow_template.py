# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
import json

from frappe.model.document import Document

from ...template import validate_template_values

class AutomationWorkflowTemplate(Document):
	def validate(self):
		values = validate_template_values(
			title=self.title,
			category=self.category,
			description=self.description,
			primary_doctype=self.primary_doctype,
			graph_value=self.graph_json,
			settings_value=self.settings_json or {},
		)
		self.graph_json = json.dumps(values["graph"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
		self.settings_json = json.dumps(values["settings"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
