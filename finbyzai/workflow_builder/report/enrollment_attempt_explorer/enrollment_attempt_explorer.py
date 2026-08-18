import frappe
from finbyzai.workflow_builder.observability import list_enrollment_decisions

def execute(filters=None):
	if not filters:
		filters = {}

	columns = [
		{"fieldname": "name", "label": "ID", "fieldtype": "Link", "options": "Automation Enrollment Decision", "width": 120},
		{"fieldname": "workflow", "label": "Workflow", "fieldtype": "Link", "options": "Automation Workflow", "width": 150},
		{"fieldname": "workflow_version", "label": "Version", "fieldtype": "Data", "width": 80},
		{"fieldname": "record_doctype", "label": "Record DocType", "fieldtype": "Link", "options": "DocType", "width": 140},
		{"fieldname": "record_name", "label": "Record Name", "fieldtype": "Dynamic Link", "options": "record_doctype", "width": 140},
		{"fieldname": "decision", "label": "Decision", "fieldtype": "Data", "width": 120},
		{"fieldname": "reason_code", "label": "Reason", "fieldtype": "Data", "width": 150},
		{"fieldname": "source", "label": "Source", "fieldtype": "Data", "width": 120},
		{"fieldname": "run", "label": "Run", "fieldtype": "Link", "options": "Automation Run", "width": 120},
		{"fieldname": "decided_at", "label": "Decided At", "fieldtype": "Datetime", "width": 150},
		{"fieldname": "evidence_json", "label": "Evidence", "fieldtype": "Code", "width": 300},
	]

	result = list_enrollment_decisions(
		workflow=filters.get("workflow"),
		record_doctype=filters.get("record_doctype"),
		record_name=filters.get("record_name"),
		decision=filters.get("decision"),
		source=filters.get("source"),
		date_from=filters.get("date_from"),
		date_to=filters.get("date_to"),
		page_length=5000  # Large enough for a report view
	)

	return columns, result.get("rows", [])
