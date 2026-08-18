// Copyright (c) 2026, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Enrollment Attempt Explorer"] = {
	"filters": [
		{
			"fieldname": "workflow",
			"label": __("Workflow"),
			"fieldtype": "Link",
			"options": "Automation Workflow"
		},
		{
			"fieldname": "record_doctype",
			"label": __("Record DocType"),
			"fieldtype": "Link",
			"options": "DocType"
		},
		{
			"fieldname": "record_name",
			"label": __("Record Name"),
			"fieldtype": "Data"
		},
		{
			"fieldname": "decision",
			"label": __("Decision"),
			"fieldtype": "Select",
			"options": "\nENROLLED\nSUPPRESSED\nDUPLICATE\nREJECTED\nGOAL_ALREADY_MET\nELIGIBILITY_LOST"
		},
		{
			"fieldname": "source",
			"label": __("Source"),
			"fieldtype": "Data"
		},
		{
			"fieldname": "date_from",
			"label": __("From Date"),
			"fieldtype": "Date"
		},
		{
			"fieldname": "date_to",
			"label": __("To Date"),
			"fieldtype": "Date"
		}
	],
	"formatter": function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname == "decision") {
			if (data.decision == "ENROLLED") {
				value = "<span style='color:green'>" + value + "</span>";
			} else if (data.decision == "REJECTED" || data.decision == "ELIGIBILITY_LOST") {
				value = "<span style='color:red'>" + value + "</span>";
			} else {
				value = "<span style='color:orange'>" + value + "</span>";
			}
		}
		return value;
	}
};
