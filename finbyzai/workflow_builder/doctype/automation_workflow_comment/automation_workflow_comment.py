from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.model.document import Document


class AutomationWorkflowComment(Document):
	def validate(self):
		workflow = frappe.get_doc("Automation Workflow", self.workflow)
		workflow.check_permission("read")
		self.content = str(self.content or "").strip()
		if not self.content:
			frappe.throw(_("Comment content is required."))
		if self.step_id:
			graph_value = frappe.db.get_value("Automation Workflow Draft", {"workflow": workflow.name}, "graph_json") or "{}"
			graph = json.loads(graph_value) if isinstance(graph_value, str) else graph_value
			if str(self.step_id) not in {str(node.get("id")) for node in graph.get("nodes") or []}:
				frappe.throw(_("The selected workflow step no longer exists."))
		if not self.resolved:
			self.resolved_by = None
			self.resolved_at = None
