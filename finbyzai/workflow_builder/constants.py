from __future__ import annotations

AUTOMATION_PREFIX = "Automation "
AUTOMATION_ROLES = {
	"builder": {"Automation Builder", "Automation Publisher", "System Manager"},
	"publisher": {"Automation Publisher", "System Manager"},
	"operator": {"Automation Operator", "Automation Publisher", "System Manager"},
}

WORKFLOW_STATUSES = {"DRAFT", "ACTIVE", "PAUSED", "DISABLED"}
RUN_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
TOKEN_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}

MAX_GRAPH_BYTES = 2 * 1024 * 1024
MAX_NODES = 250
MAX_EDGES = 500
MAX_PREDICATES = 250
MAX_CONDITION_DEPTH = 12
MAX_RECURSION_DEPTH = 10
MAX_SNAPSHOT_COLLECTION_ROWS = 1000
RETRY_DELAYS_SECONDS = (60, 300, 1800)
OUTBOX_MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1
OUTBOX_LEASE_SECONDS = 300
OUTBOX_DISPATCH_SECONDS = 20

SUPPORTED_SCALAR_FIELD_TYPES = {
	"Data",
	"Small Text",
	"Text",
	"Long Text",
	"Text Editor",
	"Markdown Editor",
	"Code",
	"JSON",
	"Color",
	"Duration",
	"Rating",
	"Attach",
	"Attach Image",
	"Signature",
	"Geolocation",
	"Check",
	"Int",
	"Float",
	"Currency",
	"Percent",
	"Date",
	"Datetime",
	"Time",
	"Select",
	"Link",
}

SUPPORTED_COLLECTION_FIELD_TYPES = {"Table", "Table MultiSelect"}
SUPPORTED_FIELD_TYPES = SUPPORTED_SCALAR_FIELD_TYPES | SUPPORTED_COLLECTION_FIELD_TYPES

BLOCKED_DOCTYPES = {
	"DocType",
	"DocField",
	"Module Def",
	"Custom Field",
	"Property Setter",
	"Custom DocPerm",
	"DocPerm",
	"DocShare",
	"User Permission",
	"Server Script",
	"Client Script",
	"System Console",
	"User",
	"Role",
	"Has Role",
	"System Settings",
	"Session Default Settings",
	"Website Settings",
	"Email Account",
	"Email Domain",
	"Authentication Log",
	"Access Log",
	"Activity Log",
	"Error Log",
	"Route History",
	"Deleted Document",
	"Prepared Report",
	"Integration Request",
	"OAuth Client",
	"OAuth Bearer Token",
	"OAuth Authorization Code",
	"Connected App",
	"Social Login Key",
	"LDAP Settings",
	"Package",
	"Page",
	"Report",
	"Print Format",
	"Workflow",
	"Workflow State",
	"Workflow Action",
	"Workflow Transition",
	"Scheduled Job Type",
	"Scheduled Job Log",
	"RQ Job",
	"RQ Worker",
	"Version",
	"Notification Log",
}

NODE_TYPES = {
	"trigger.manual",
	"trigger.document_insert",
	"trigger.document_change",
	"trigger.filter_criteria",
	"trigger.event",
	"trigger.schedule",
	"trigger.any",
	"condition.if_else",
	"condition.random_split",
	"condition.switch",
	"condition.deduplicate",
	"delay.fixed",
	"delay.drip",
	"delay.until_date",
	"delay.until_event",
	"delay.business_hours",
	"transform.value",
	"transform.associated_record",
	"transform.child_records",
	"action.update_record",
	"action.create_record",
	"action.create_todo",
	"action.add_comment",
	"action.notify_user",
	"action.send_email",
	"action.webhook",
	"action.numeric_adjust",
	"action.delete_record",
	"action.manage_association",
	"action.round_robin",
	"action.call_subflow",
	"action.send_sms",
	"action.instagram_message",
	"action.asana",
	"action.copy_record",
	"action.merge_contact",
	"action.unassign_record",
	"action.create_note",
	"action.verify_email",
	"action.mark_communications_read",
	"action.remove_from_workflow",
	"action.complete_goal",
	"action.go_to",
	"end.complete",
}

TRIGGER_NODE_TYPES = {
	"trigger.manual",
	"trigger.document_insert",
	"trigger.document_change",
	"trigger.filter_criteria",
	"trigger.event",
	"trigger.schedule",
	"trigger.any",
}

ACTION_NODE_TYPES = {
	"action.update_record",
	"action.create_record",
	"action.create_todo",
	"action.add_comment",
	"action.notify_user",
	"action.send_email",
	"action.webhook",
	"action.numeric_adjust",
	"action.delete_record",
	"action.manage_association",
	"action.round_robin",
	"action.call_subflow",
	"action.send_sms",
	"action.instagram_message",
	"action.asana",
	"action.copy_record",
	"action.merge_contact",
	"action.unassign_record",
	"action.create_note",
	"action.verify_email",
	"action.mark_communications_read",
	"action.remove_from_workflow",
	"action.complete_goal",
	"action.go_to",
}

EXTERNAL_ACTION_NODE_TYPES = {"action.send_email", "action.send_sms", "action.webhook", "action.instagram_message", "action.asana"}
