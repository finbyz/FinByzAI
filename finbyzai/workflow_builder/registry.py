from __future__ import annotations

import json

import frappe
from frappe import _

from .constants import (
	AUTOMATION_PREFIX,
	AUTOMATION_ROLES,
	BLOCKED_DOCTYPES,
	SUPPORTED_COLLECTION_FIELD_TYPES,
	SUPPORTED_FIELD_TYPES,
	SUPPORTED_SCALAR_FIELD_TYPES,
)
from .errors import AutomationPermissionError

DOCTYPE_PERMISSION_TYPES = {"read", "write", "create", "delete"}


# Stable outputs form part of the public node-authoring contract. Keep them
# beside the node catalog so both graph validation and the frontend consume the
# same definition.
NODE_OUTPUT_PATHS = {
	"condition.if_else": ["matched", "selected_handle", "branch_name"],
	"condition.random_split": ["selected_handle", "branch_name", "bucket"],
	"condition.switch": ["value", "matched_handle"],
	"condition.deduplicate": ["duplicate_name", "is_duplicate", "matched_fields"],
	"delay.fixed": ["due_at", "released"],
	"delay.drip": ["due_at", "released", "batch_size", "position"],
	"delay.until_date": ["due_at", "released"],
	"delay.until_event": ["event_payload", "timed_out", "released", "matched_handle", "event_source_id", "event_source_doctype", "event_source_type", "wait_indefinitely"],
	"delay.business_hours": ["released", "due_at", "timezone"],
	"transform.value": ["value"],
	"transform.associated_record": ["value", "linked_name"],
	"transform.child_records": ["values", "count"],
	"action.update_record": ["doctype", "name", "updated_fields"],
	"action.numeric_adjust": ["doctype", "name", "field", "previous", "new_value"],
	"action.delete_record": ["doctype", "name", "deleted"],
	"action.create_record": ["doctype", "name"],
	"action.manage_association": ["doctype", "name", "operation", "target_name"],
	"action.round_robin": ["doctype", "name", "assigned_to", "group", "assignment_type", "assignment_version"],
	"action.create_todo": ["doctype", "name", "allocated_to"],
	"action.add_comment": ["comment"],
	"action.notify_user": ["for_user", "recipients", "recipient_count"],
	"action.send_email": ["email_queue", "communication", "recipient", "sender", "reply_to", "email_template", "content_hash", "subscription_topic", "suppressed", "suppression_reason"],
	"action.send_sms": ["recipient", "status", "status_code", "consent_check"],
	"action.webhook": ["status_code", "response_hash"],
	"action.instagram_message": ["recipient_id", "status_code", "response_hash"],
	"action.asana": ["gid", "name", "permalink_url", "operation"],
	"action.call_subflow": ["run_id", "status"],
	"action.copy_record": ["doctype", "name"],
	"action.merge_contact": ["canonical_contact", "merged_contact", "matched_fields", "deleted"],
	"action.unassign_record": ["closed_assignments"],
	"action.create_note": ["note"],
	"action.verify_email": ["email", "valid", "reason"],
	"action.mark_communications_read": ["updated"],
	"action.remove_from_workflow": ["cancelled_runs", "target_workflow", "terminate_path"],
	"action.complete_goal": ["goal", "terminate_path"],
	"action.go_to": ["target_node_id"],
}


BUSINESS_EVENT_CATALOG = [
	{
		"topic": "crm.contact.list.joined",
		"label": "Contact joined a list",
		"category": "Contact",
		"description": "A contact became a member of a configured list or segment.",
		"filter_fields": [
			{"fieldname": "list_name", "label": "List", "fieldtype": "Link", "options": "Email Group"},
		],
	},
	{
		"topic": "crm.form.submitted",
		"label": "Form submitted",
		"category": "Contact",
		"description": "A supported form was submitted and resolved to a contact.",
		"filter_fields": [
			{"fieldname": "form_name", "label": "Form", "fieldtype": "Link", "options": "Web Form"},
		],
	},
	{
		"topic": "crm.call.inbound",
		"label": "Inbound Aircall matched to CRM record",
		"category": "Contact",
		"description": "A completed inbound Aircall call was matched to an enrolled CRM record.",
		"filter_fields": [
			{"fieldname": "phone_number", "label": "Called number", "fieldtype": "Data"},
			{"fieldname": "outcome", "label": "Call outcome", "fieldtype": "Data"},
			{"fieldname": "call_log", "label": "Call Log", "fieldtype": "Link", "options": "Call Log"},
		],
	},
	{
		"topic": "communication.responded",
		"label": "Record replied",
		"category": "Communication",
		"description": "An inbound Communication was linked to the enrolled record.",
		"filter_fields": [
			{"fieldname": "communication_medium", "label": "Channel", "fieldtype": "Data"},
			{"fieldname": "sender", "label": "Sender", "fieldtype": "Data"},
		],
	},
	{
		"topic": "record.updated",
		"label": "Record updated",
		"category": "Record activity",
		"description": "The enrolled record or a record created by an earlier workflow action was updated while waiting.",
		"filter_fields": [
			{"fieldname": "changed_fields", "label": "Changed fields", "fieldtype": "Table MultiSelect"},
			{"fieldname": "status", "label": "Current status", "fieldtype": "Data"},
			{"fieldname": "docstatus", "label": "Document status", "fieldtype": "Int"},
		],
	},
	{
		"topic": "workflow.todo.completed",
		"label": "Task completed",
		"category": "Earlier action activity",
		"description": "A ToDo created by an earlier Create ToDo action was closed while this workflow was waiting.",
		"filter_fields": [
			{"fieldname": "todo", "label": "ToDo", "fieldtype": "Link", "options": "ToDo"},
			{"fieldname": "allocated_to", "label": "Assigned user", "fieldtype": "Link", "options": "User"},
			{"fieldname": "status", "label": "Status", "fieldtype": "Data"},
		],
	},
	{
		"topic": "crm.lead.qualified",
		"label": "Lead qualified",
		"category": "Contact",
		"description": "A Lead's qualification status changed to Qualified.",
		"filter_fields": [
			{"fieldname": "qualification_status", "label": "Qualification status", "fieldtype": "Data"},
			{"fieldname": "score", "label": "Qualification score", "fieldtype": "Float"},
		],
	},
	{
		"topic": "email.hard_bounced",
		"label": "Email hard bounced",
		"category": "Email",
		"description": "An email provider reported a permanent delivery failure.",
		"filter_fields": [
			{"fieldname": "email_queue", "label": "Workflow email message", "fieldtype": "Data"},
			{"fieldname": "email_id", "label": "Specific email", "fieldtype": "Data"},
			{"fieldname": "email_type", "label": "Email type", "fieldtype": "Data"},
		],
	},
	{
		"topic": "email.soft_bounced",
		"label": "Email soft bounced",
		"category": "Email",
		"description": "An email provider reported a temporary delivery failure.",
		"filter_fields": [
			{"fieldname": "email_queue", "label": "Workflow email message", "fieldtype": "Data"},
			{"fieldname": "email_id", "label": "Specific email", "fieldtype": "Data"},
			{"fieldname": "email_type", "label": "Email type", "fieldtype": "Data"},
		],
	},
	{
		"topic": "email.clicked",
		"label": "Email link clicked",
		"category": "Email",
		"description": "A tracked link in an email was clicked.",
		"filter_fields": [
			{"fieldname": "email_queue", "label": "Workflow email message", "fieldtype": "Data"},
			{"fieldname": "email_id", "label": "Specific email", "fieldtype": "Data"},
			{"fieldname": "email_type", "label": "Email type", "fieldtype": "Data"},
			{"fieldname": "link_url", "label": "Clicked URL", "fieldtype": "Data"},
		],
	},
	{
		"topic": "email.opened",
		"label": "Email opened",
		"category": "Email",
		"description": "A tracked email-open event was received.",
		"filter_fields": [
			{"fieldname": "email_queue", "label": "Workflow email message", "fieldtype": "Data"},
			{"fieldname": "email_id", "label": "Specific email", "fieldtype": "Data"},
			{"fieldname": "email_type", "label": "Email type", "fieldtype": "Data"},
		],
	},
	{
		"topic": "email.unsubscribed",
		"label": "Email unsubscribed",
		"category": "Email",
		"description": "An email recipient unsubscribed globally, from a record, or from a Lead topic.",
		"filter_fields": [
			{"fieldname": "email_queue", "label": "Workflow email message", "fieldtype": "Data"},
			{"fieldname": "email_id", "label": "Specific email", "fieldtype": "Data"},
			{"fieldname": "email_type", "label": "Unsubscribe scope", "fieldtype": "Select", "options": "global\nrecord\ntopic"},
			{"fieldname": "subscription_topic", "label": "Reach subscription topic", "fieldtype": "Link", "options": "Subscription Topic"},
		],
	},
	{
		"topic": "commerce.store.login",
		"label": "Customer signed in to portal",
		"category": "Commerce",
		"description": "A website user linked to a Customer created a new authenticated portal session.",
		"filter_fields": [
			{"fieldname": "portal", "label": "Portal", "fieldtype": "Data"},
		],
	},
	{
		"topic": "commerce.order.created",
		"label": "Order created",
		"category": "Commerce",
		"description": "ERPNext created a Sales Order for the enrolled Customer.",
		"filter_fields": [
			{"fieldname": "source", "label": "Order source", "fieldtype": "Data"},
			{"fieldname": "order_type", "label": "Order type", "fieldtype": "Data"},
			{"fieldname": "sales_order", "label": "Sales Order", "fieldtype": "Link", "options": "Sales Order"},
		],
	},
	{
		"topic": "commerce.order.abandoned",
		"label": "Order abandoned",
		"category": "Commerce",
		"description": "A connected store marked a contact's order or cart as abandoned.",
		"filter_fields": [
			{"fieldname": "store_id", "label": "Store", "fieldtype": "Data"},
			{"fieldname": "cart_id", "label": "Cart", "fieldtype": "Data"},
			{"fieldname": "abandoned_after_hours", "label": "Idle threshold (hours)", "fieldtype": "Int"},
		],
	},
]


# A workflow always enrolls one Frappe DocType. This is the same boundary that
# HubSpot calls the workflow object type: an event is only useful when its
# producer can resolve the occurrence back to that enrolled record. Keep this
# context separate from the stable topic definitions above so existing saved
# workflows and integrations continue to use the same topic keys.
BUSINESS_EVENT_CONTEXT = {
	"crm.contact.list.joined": {
		"trigger_doctypes": {"Contact", "Lead"},
		"wait_doctypes": {"Contact", "Lead"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "Frappe Email Group",
		"setup_note": "Email Group Member additions and re-subscriptions are matched by email_id to Contact or Lead records.",
	},
	"crm.form.submitted": {
		"trigger_traits": {"record"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "Frappe Web Form",
		"setup_note": "Frappe Web Form submissions emit the event for the exact target record after its authoritative save.",
	},
	"crm.call.inbound": {
		"trigger_doctypes": {"Contact", "Lead", "Opportunity", "Customer"},
		"wait_doctypes": {"Contact", "Lead", "Opportunity", "Customer"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "Aircall Integration",
		"setup_note": "Completed inbound Aircall Call Logs use stored Lead, Opportunity, and Customer links plus Aircall's normalized phone matching for Contact.",
	},
	"communication.responded": {
		"trigger_traits": {"record"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "Frappe Communication",
		"setup_note": "Received email, chat, phone, SMS, and other Communication records emit this event for their exact reference document.",
	},
	"record.updated": {
		"trigger_doctypes": set(),
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record", "action_output"],
		"source_node_types": ["action.create_record", "action.copy_record"],
		"producer_status": "native",
		"source_app": "Frappe document lifecycle",
		"setup_note": "A committed update releases only waits indexed to that exact enrolled record or earlier created/copied record.",
	},
	"workflow.todo.completed": {
		"trigger_doctypes": set(),
		"wait_traits": {"record"},
		"source_modes": ["action_output"],
		"source_node_types": ["action.create_todo"],
		"producer_status": "native",
		"source_app": "Frappe ToDo",
		"setup_note": "A ToDo created by the selected earlier workflow action releases the wait when its status changes to Closed.",
	},
	"crm.lead.qualified": {
		"trigger_doctypes": {"Lead"},
		"wait_doctypes": {"Lead"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "ERPNext Lead",
		"setup_note": "Emitted when Qualification status changes from another value to Qualified.",
		"trigger_alternative": "For enrollment, prefer “When filter criteria is met” with Qualification status = Qualified; it is native and needs no event integration.",
	},
	"email.hard_bounced": {
		"trigger_traits": {"email_recipient"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record", "action_output"],
		"source_node_types": ["action.send_email"],
		"producer_status": "native",
		"source_app": "Frappe Communication",
		"setup_note": "Emitted when a provider updates a linked Communication to Bounced or an imported failure delivery report correlates to the exact sent Communication.",
	},
	"email.soft_bounced": {
		"trigger_traits": {"email_recipient"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record", "action_output"],
		"source_node_types": ["action.send_email"],
		"producer_status": "native",
		"source_app": "Frappe Communication",
		"setup_note": "Emitted when a provider updates a linked Communication to Soft-Bounced or an imported delay delivery report correlates to the exact sent Communication.",
	},
	"email.clicked": {
		"trigger_traits": {"email_recipient"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record", "action_output"],
		"source_node_types": ["action.send_email"],
		"producer_status": "native",
		"source_app": "Frappe Communication",
		"setup_note": "Emitted when an email provider updates a linked Communication delivery status to Clicked.",
	},
	"email.opened": {
		"trigger_traits": {"email_recipient"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record", "action_output"],
		"source_node_types": ["action.send_email"],
		"producer_status": "native",
		"source_app": "FinbyzAI tracking / Frappe Communication",
		"setup_note": "Emitted once when the tracked open pixel loads, when a tracked link records the first open, or when an installed tracker creates an Opened event for the exact Communication.",
	},
	"email.unsubscribed": {
		"trigger_traits": {"email_recipient"},
		"wait_traits": {"record"},
		"source_modes": ["enrolled_record", "action_output"],
		"source_node_types": ["action.send_email"],
		"producer_status": "native",
		"source_app": "Frappe Email Unsubscribe / Finbyz Reach",
		"setup_note": "Global and record-specific opt-outs come from Frappe Email Unsubscribe; Lead topic opt-outs come from Finbyz Reach subscription preferences.",
	},
	"commerce.store.login": {
		"trigger_doctypes": {"Customer"},
		"wait_doctypes": {"Customer"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "Customer Portal",
		"setup_note": "A new Customer Portal website session is mapped through Portal User or the linked Contact.",
	},
	"commerce.order.created": {
		"trigger_doctypes": {"Customer"},
		"wait_doctypes": {"Customer"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "ERPNext Sales Order",
		"setup_note": "A new ERPNext Sales Order is emitted for its Customer; orders converted from a Shopping Cart are identified as Customer Portal orders.",
		"trigger_alternative": "For a Sales Order workflow, use the native “Record created” trigger instead of this contact/customer event.",
	},
	"commerce.order.abandoned": {
		"trigger_doctypes": {"Customer"},
		"wait_doctypes": {"Customer"},
		"source_modes": ["enrolled_record"],
		"producer_status": "native",
		"source_app": "ERPNext Shopping Cart",
		"setup_note": "A Customer Portal Shopping Cart quotation linked to the enrolled Customer emits after the trigger's configured idle time without conversion to a Sales Order. Existing workflows default to 24 hours.",
	},
}


WORKFLOW_OBJECT_TRAITS = {
	"Contact": {"contact", "person"},
	"Lead": {"lead", "person"},
	"Customer": {"customer", "organization"},
	"Opportunity": {"opportunity", "deal"},
	"Sales Order": {"sales_order", "commerce_record"},
	"Quotation": {"quotation", "commerce_record"},
	"User": {"user", "person"},
}

EMAIL_FIELDNAMES = {"email", "email_id", "contact_email", "email_address"}
PHONE_FIELDNAMES = {"phone", "phone_no", "mobile", "mobile_no", "contact_mobile"}


NODE_CATALOG = [
	{"type": "trigger.manual", "label": "Manual enrollment", "category": "Triggers", "description": "Enroll selected records from the operator UI.", "default_config": {}},
	{"type": "trigger.document_insert", "label": "Record created", "category": "Triggers", "description": "Legacy single event-mode trigger retained for existing workflows.", "default_config": {"condition": None}, "authoring_hidden": 1},
	{"type": "trigger.document_change", "label": "Record changed", "category": "Triggers", "description": "Legacy single event-mode trigger retained for existing workflows.", "default_config": {"watch_fields": [], "condition": None}, "authoring_hidden": 1},
	{"type": "trigger.filter_criteria", "label": "When filter criteria is met", "category": "Triggers", "description": "Enroll when a new or updated record meets the configured AND/OR field criteria.", "default_config": {"condition": None}},
	{"type": "trigger.event", "type_version": 2, "label": "Business events (legacy editor)", "category": "Triggers", "description": "Legacy business-event boundary retained for existing workflows; new Event mode uses independent trigger cards.", "default_config": {"events": [{"id": "event-1", "event_topic": "", "event_filter": None}], "condition": None}, "authoring_hidden": 1},
	{"type": "trigger.schedule", "label": "Scheduled", "category": "Triggers", "description": "Enroll records through a durable schedule configured after publishing.", "default_config": {}},
	{"type": "trigger.webhook", "label": "Incoming webhook", "category": "Triggers", "description": "Enroll one exact permitted record through a managed authenticated and idempotent endpoint.", "default_config": {}},
	{"type": "trigger.any", "type_version": 2, "label": "Event triggers", "category": "Triggers", "description": "Enroll when any one of up to twenty independently filtered record or business events occurs.", "default_config": {"triggers": [{"id": "trigger-1", "type": "trigger.event", "config": {"event_topic": "", "event_filter": None, "condition": None}}]}},
	{"type": "condition.if_else", "type_version": 2, "label": "If / else paths", "category": "Logic", "description": "Choose which named path each record follows; everyone unmatched uses None.", "default_config": {"branches": [{"handle": "branch-1", "name": "Path 1", "condition": None}]}},
	{"type": "condition.random_split", "label": "Random percentage split", "category": "Logic", "description": "Distribute records predictably across named percentage paths for controlled experiments.", "default_config": {"branches": [{"handle": "split-a", "name": "Group A", "percentage": 50}, {"handle": "split-b", "name": "Group B", "percentage": 50}]}, "authoring_tier": "advanced"},
	{"type": "condition.switch", "label": "Value branch (legacy)", "category": "Logic", "description": "Legacy exact-value branch retained for existing workflows.", "default_config": {"field": "", "cases": []}, "authoring_hidden": 1},
	{"type": "condition.deduplicate", "type_version": 2, "label": "Deduplicate", "category": "Logic", "description": "Branch when another record matches one or more selected fields.", "default_config": {"match_fields": [], "match_mode": "all"}},
	{"type": "delay.fixed", "label": "Set amount of time", "category": "Delays", "description": "Wait for seconds, minutes, hours, days, weeks, or business days.", "default_config": {"seconds": 3600}},
	{"type": "delay.drip", "label": "Drip / batch interval", "category": "Delays", "description": "Release records in durable batches separated by a readable interval.", "default_config": {"batch_size": 100, "interval_seconds": 3600}, "authoring_tier": "advanced"},
	{"type": "delay.until_date", "label": "Until date or time", "category": "Delays", "description": "Resume at a calendar date/time or a Date/Datetime field on the record.", "default_config": {"mode": "literal", "datetime": "", "field": ""}},
	{"type": "delay.until_event", "type_version": 2, "label": "Until event occurs", "category": "Delays", "description": "Wait for a new typed event on this record or an earlier action output, with an optional timeout path.", "default_config": {"data_source": "enrolled_record", "event_topic": "", "event_filter": None, "event_source": None, "event_source_doctype": None, "timeout_mode": "duration", "timeout_seconds": 86400, "branch_on_timeout": 0}},
	{"type": "delay.business_hours", "label": "Until business window", "category": "Delays", "description": "Wait until the next allowed day and local business time.", "default_config": {"calendar": "", "timezone": "UTC", "start_time": "09:00", "end_time": "17:00", "weekdays": [0, 1, 2, 3, 4]}},
	{"type": "transform.value", "type_version": 2, "label": "Transform value", "category": "Data", "description": "Create a reusable text, number, phone, currency, random, or calculated value without changing the record.", "default_config": {"operation": "coalesce", "values": []}, "authoring_tier": "advanced"},
	{"type": "transform.associated_record", "label": "Read associated record", "category": "Data", "description": "Fetch a property from an explicitly linked record.", "default_config": {"reference_field": "", "fetch_field": ""}, "authoring_tier": "advanced"},
	{"type": "transform.child_records", "label": "Read child records", "category": "Data", "description": "Fetch properties from child-table rows.", "default_config": {"child_table_field": "", "fetch_field": ""}, "authoring_tier": "advanced"},
	{"type": "action.call_subflow", "label": "Run another workflow", "category": "Logic", "description": "Execute another compatible published workflow, optionally waiting for it to finish.", "default_config": {"subflow_id": "", "wait_for_completion": 1}},
	{"type": "action.update_record", "label": "Update record", "category": "Actions", "description": "Update writable fields on the enrolled record.", "default_config": {"assignments": []}},
	{"type": "action.numeric_adjust", "label": "Adjust number", "category": "Actions", "description": "Increase, decrease, multiply, or replace a numeric field.", "default_config": {"field": "", "operation": "add", "amount": 1}, "authoring_tier": "advanced"},
	{"type": "action.manage_association", "label": "Manage association", "category": "Actions", "description": "Idempotently link or unlink associated records.", "default_config": {"target_doctype": "", "target_name": "", "link_field": "", "operation": "link"}, "authoring_tier": "advanced"},
	{"type": "action.round_robin", "type_version": 2, "label": "Round robin assignment", "category": "Actions", "description": "Atomically rotate Frappe assignments across a User Group or an explicit user list.", "default_config": {"assignment_type": "group", "group": "", "users": []}, "authoring_tier": "advanced"},
	{"type": "action.delete_record", "label": "Delete record", "category": "Actions", "description": "Permanently delete the enrolled record and end this path.", "default_config": {}, "authoring_tier": "danger"},
	{"type": "action.create_record", "label": "Create record", "category": "Actions", "description": "Create another permitted Frappe document.", "default_config": {"target_doctype": "", "assignments": []}},
	{"type": "action.create_todo", "label": "Create ToDo", "category": "Actions", "description": "Assign a ToDo linked to the enrolled record.", "default_config": {"allocated_to": "", "description": "", "priority": "Medium"}},
	{"type": "action.add_comment", "label": "Add comment", "category": "Actions", "description": "Add a timeline comment to the enrolled record.", "default_config": {"content": ""}},
	{"type": "action.create_note", "label": "Create Desk note", "category": "Actions", "description": "Create a Desk Note containing a link back to the enrolled record.", "default_config": {"title": "", "content": ""}, "authoring_tier": "advanced"},
	{"type": "action.copy_record", "label": "Copy record", "category": "Actions", "description": "Create a permission-checked copy of the enrolled record.", "default_config": {}, "authoring_tier": "advanced"},
	{"type": "action.merge_contact", "label": "Merge contact", "category": "Actions", "description": "Merge the enrolled Contact into one unambiguous canonical Contact.", "default_config": {"match_fields": ["email_id"], "match_mode": "all"}, "authoring_tier": "advanced"},
	{"type": "action.unassign_record", "label": "Remove assigned users", "category": "Actions", "description": "Close every open Frappe assignment linked to the enrolled record.", "default_config": {}, "authoring_tier": "advanced"},
	{"type": "action.verify_email", "label": "Check email format", "category": "Data", "description": "Check only whether an email address is syntactically valid; this does not verify its mailbox.", "default_config": {"email": {"kind": "record_field", "field": "email_id"}}, "authoring_tier": "advanced"},
	{"type": "action.mark_communications_read", "label": "Mark conversations read", "category": "Actions", "description": "Mark received Communications linked to the enrolled record as seen.", "default_config": {}, "authoring_tier": "advanced"},
	{"type": "action.remove_from_workflow", "label": "Remove from workflow", "category": "Logic", "description": "Cancel this record's active runs in the selected workflow and end this path when targeting the current workflow.", "default_config": {"target_workflow": "current"}, "authoring_tier": "advanced"},
	{"type": "action.complete_goal", "label": "Mark goal and end path", "category": "Logic", "description": "Record a named goal marker and end this path immediately; ordinary paths already complete automatically.", "default_config": {"goal": "Goal reached"}, "authoring_tier": "advanced"},
	{"type": "action.go_to", "label": "Go to existing step", "category": "Logic", "description": "Reuse an existing downstream step in large workflows without manual edge drawing.", "default_config": {"target_node_id": ""}, "authoring_tier": "advanced"},
	{"type": "action.notify_user", "label": "Notify users", "category": "Actions", "description": "Create in-app notifications for a specific user, current assignees, or all enabled system users.", "default_config": {"audience": "specific", "for_user": "", "subject": "", "message": ""}},
	{"type": "action.send_email", "type_version": 2, "label": "Send email", "category": "External", "description": "Send a standard or visual Email Template with preview, test-send, personalization, sender controls, and recipient suppression checks.", "default_config": {"content_mode": "template", "email_template": "", "recipient": {"kind": "literal", "value": ""}, "subject_override": {"kind": "literal", "value": ""}, "sender_name": "", "sender_email": "", "reply_to": "", "subscription_topic": ""}},
	{"type": "action.send_sms", "label": "Send SMS", "category": "External", "description": "Send a text message via Frappe SMS Settings.", "default_config": {"recipient": {"kind": "literal", "value": ""}, "message": {"kind": "literal", "value": ""}, "purpose": "workflow", "require_consent": 1}},
	{"type": "action.webhook", "label": "Send webhook", "category": "External", "description": "POST signed JSON to an allowlisted HTTPS endpoint.", "default_config": {"integration_secret": "", "url": "", "payload": {}, "purpose": "workflow", "require_consent": 0}, "authoring_tier": "advanced"},
	{"type": "action.instagram_message", "label": "Send Instagram message", "category": "External", "description": "Send a consent-aware Instagram Direct message through a controlled Meta endpoint.", "default_config": {"integration_secret": "", "url": "https://graph.facebook.com/v23.0/me/messages", "recipient_id": {"kind": "literal", "value": ""}, "message": {"kind": "literal", "value": ""}, "purpose": "workflow", "require_consent": 1}, "authoring_tier": "advanced"},
	{"type": "action.asana", "label": "Asana task / project", "category": "External", "description": "Create or update Asana tasks, subtasks, and projects through the installed Asana integration.", "default_config": {"operation": "create_task", "target_gid": {"kind": "literal", "value": ""}, "payload": {"name": {"kind": "literal", "value": ""}}}, "authoring_tier": "advanced"},
	{"type": "end.complete", "label": "Complete (legacy)", "category": "Logic", "description": "Legacy explicit completion marker retained for existing workflows.", "default_config": {}, "authoring_hidden": 1},
]


NODE_AUTHORING_SCHEMAS = {
	"condition.if_else": {"required": []},
	"condition.random_split": {"required": [{"path": "branches", "label": "Percentage paths"}]},
	"trigger.event": {"required": []},
	"trigger.any": {"required": [{"path": "triggers", "label": "Enrollment triggers"}]},
	"condition.switch": {"required": [{"path": "field", "label": "Branch field"}, {"path": "cases", "label": "Cases"}]},
	# Version-aware validation in schema.py keeps legacy v1 match_field nodes
	# compatible while v2 uses compound match_fields.
	"condition.deduplicate": {"required": []},
	"delay.fixed": {"required": [{"path": "seconds", "label": "Duration"}]},
	"delay.drip": {"required": [{"path": "batch_size", "label": "Batch size"}, {"path": "interval_seconds", "label": "Batch interval"}]},
	"delay.until_date": {"required": []},
	# timeout_seconds is conditionally required only when timeout_mode=duration;
	# schema.py owns that version-aware rule.
	"delay.until_event": {"required": [{"path": "event_topic", "label": "Event topic"}]},
	"delay.business_hours": {"required": [{"path": "timezone", "label": "Timezone"}]},
	# Transform inputs are operation-dependent. random_number intentionally has
	# no inputs, so schema.py owns this conditional requirement.
	"transform.value": {"required": []},
	"transform.associated_record": {"required": [{"path": "reference_field", "label": "Link field"}, {"path": "fetch_field", "label": "Fetched field"}]},
	"transform.child_records": {"required": [{"path": "child_table_field", "label": "Child table field"}, {"path": "fetch_field", "label": "Child field"}]},
	"action.call_subflow": {"required": [{"path": "subflow_id", "label": "Subflow"}]},
	"action.update_record": {"required": [{"path": "assignments", "label": "Field changes"}]},
	"action.numeric_adjust": {"required": [{"path": "field", "label": "Target field"}, {"path": "amount", "label": "Amount"}]},
	"action.manage_association": {"required": [{"path": "target_doctype", "label": "Target DocType"}, {"path": "target_name", "label": "Target record"}, {"path": "link_field", "label": "Link field"}]},
	# The selected assignment type determines whether group or users is required.
	# schema.py owns this conditional validation while retaining legacy group-only drafts.
	"action.round_robin": {"required": []},
	"action.create_record": {"required": [{"path": "target_doctype", "label": "Target DocType"}, {"path": "assignments", "label": "Field values"}]},
	"action.create_todo": {"required": [{"path": "allocated_to", "label": "Assignee"}, {"path": "description", "label": "Task description"}]},
	"action.add_comment": {"required": [{"path": "content", "label": "Comment"}]},
	"action.create_note": {"required": [{"path": "title", "label": "Title"}, {"path": "content", "label": "Content"}]},
	"action.merge_contact": {"required": [{"path": "match_fields", "label": "Match fields"}]},
	"action.verify_email": {"required": [{"path": "email", "label": "Email"}]},
	"action.remove_from_workflow": {"required": [{"path": "target_workflow", "label": "Workflow"}]},
	"action.complete_goal": {"required": [{"path": "goal", "label": "Goal name"}]},
	"action.go_to": {"required": [{"path": "target_node_id", "label": "Destination step"}]},
	# for_user is required only for the "specific" audience. schema.py validates
	# the audience-specific recipient together with the common text fields.
	"action.notify_user": {"required": [{"path": "subject", "label": "Subject"}, {"path": "message", "label": "Message"}]},
	"action.send_email": {"required": [{"path": "recipient", "label": "Recipient"}]},
	"action.send_sms": {"required": [{"path": "recipient", "label": "Recipient"}, {"path": "message", "label": "Message"}, {"path": "purpose", "label": "Consent purpose"}]},
	"action.webhook": {"required": [{"path": "integration_secret", "label": "Integration secret"}, {"path": "url", "label": "HTTPS endpoint"}, {"path": "payload", "label": "JSON payload"}]},
	"action.instagram_message": {"required": [{"path": "integration_secret", "label": "Integration secret"}, {"path": "url", "label": "Meta HTTPS endpoint"}, {"path": "recipient_id", "label": "Instagram recipient"}, {"path": "message", "label": "Message"}]},
	"action.asana": {"required": [{"path": "operation", "label": "Operation"}, {"path": "payload", "label": "Asana fields"}]},
}


def round_robin_assignment(config: dict | None) -> dict:
	"""Normalize explicit v2 assignment pools while preserving published legacy configs."""
	config = config if isinstance(config, dict) else {}
	assignment_type = str(config.get("assignment_type") or "").strip().lower()
	if assignment_type:
		users_value = config.get("users")
		users = (
			[str(value).strip() for value in users_value if str(value).strip()]
			if isinstance(users_value, list)
			else []
		)
		return {
			"assignment_type": assignment_type,
			"group": str(config.get("group") or "").strip(),
			"users": users,
			"legacy": False,
		}
	return {
		"assignment_type": "legacy",
		"group": str(config.get("group") or "").strip(),
		"users": [],
		"legacy": True,
	}


def require_capability(capability: str) -> None:
	if not AUTOMATION_ROLES.get(capability, set()).intersection(frappe.get_roles()):
		raise AutomationPermissionError(_("You do not have the required Automation {0} permission.").format(capability))


def require_builder() -> None:
	require_capability("builder")


def require_publisher() -> None:
	require_capability("publisher")


def require_operator() -> None:
	require_capability("operator")


def require_viewer() -> None:
	allowed = AUTOMATION_ROLES["builder"] | AUTOMATION_ROLES["operator"]
	if not allowed.intersection(frappe.get_roles()):
		raise AutomationPermissionError(_("You do not have access to Automation workflows."))


def configured_blocked_doctypes() -> set[str]:
	blocked = set(BLOCKED_DOCTYPES)
	if frappe.db.exists("DocType", "Automation Settings"):
		value = frappe.db.get_single_value("Automation Settings", "blocked_doctypes", cache=False) or ""
		blocked.update(line.strip() for line in value.replace(",", "\n").splitlines() if line.strip())
	return blocked


def doctype_eligibility(doctype: str, *, permission_type: str = "read", user: str | None = None) -> dict:
	"""Return a safe, non-throwing capability result for metadata-driven clients."""
	doctype = str(doctype or "").strip()
	permission_type = str(permission_type or "read").strip().lower()
	result = {
		"doctype": doctype,
		"permission_type": permission_type,
		"available": False,
		"reason_code": None,
		"explanation": None,
	}

	def unavailable(code: str, message: str) -> dict:
		result["reason_code"] = code
		result["explanation"] = message
		return result

	if permission_type not in DOCTYPE_PERMISSION_TYPES:
		return unavailable("UNSUPPORTED_PERMISSION_TYPE", _("Unsupported metadata permission type."))
	if not doctype:
		return unavailable("EMPTY_DOCTYPE", _("Choose a DocType first."))
	if doctype.startswith(AUTOMATION_PREFIX):
		return unavailable("AUTOMATION_INTERNAL", _("Automation engine DocTypes cannot be automated."))
	if doctype in configured_blocked_doctypes():
		return unavailable("BLOCKED_DOCTYPE", _("This DocType is blocked by the automation security policy."))
	try:
		meta = frappe.get_meta(doctype)
	except frappe.DoesNotExistError:
		return unavailable("DOCTYPE_NOT_FOUND", _("This DocType no longer exists."))
	if meta.istable:
		return unavailable("CHILD_DOCTYPE", _("Child-table DocTypes cannot be enrolled independently."))
	if meta.issingle:
		return unavailable("SINGLE_DOCTYPE", _("Single DocTypes cannot be enrolled in workflows."))
	if getattr(meta, "is_virtual", False):
		return unavailable("VIRTUAL_DOCTYPE", _("Virtual DocTypes are not supported by the durable workflow engine."))
	if not frappe.has_permission(doctype, ptype=permission_type, user=user):
		return unavailable(
			"PERMISSION_DENIED",
			_("The selected user does not have {0} permission for this DocType.").format(permission_type),
		)
	result["available"] = True
	return result


def is_eligible_doctype(doctype: str, *, permission_type: str = "read", user: str | None = None) -> bool:
	return bool(doctype_eligibility(doctype, permission_type=permission_type, user=user)["available"])


def eligible_doctypes(
	*,
	permission_type: str = "read",
	user: str | None = None,
	search: str | None = None,
) -> list[dict]:
	needle = str(search or "").strip()
	or_filters = None
	if needle:
		like = f"%{needle}%"
		or_filters = [["DocType", "name", "like", like], ["DocType", "module", "like", like]]
	rows = frappe.get_list(
		"DocType",
		filters={"istable": 0, "issingle": 0},
		or_filters=or_filters,
		fields=["name", "module", "is_submittable", "is_virtual"],
		order_by="name asc",
		ignore_permissions=True,
		limit=0,
	)
	return [
		{
			"name": row.name,
			"label": _(row.name),
			"module": row.module,
			"is_submittable": bool(row.is_submittable),
			"permission_type": permission_type,
		}
		for row in rows
		if not row.is_virtual and is_eligible_doctype(row.name, permission_type=permission_type, user=user)
	]


def eligible_doctype_page(
	*,
	permission_type: str = "read",
	user: str | None = None,
	search: str | None = None,
	start: int = 0,
	page_length: int = 20,
) -> dict:
	"""Page after policy/permission filtering without loading the full DocType catalog."""
	needle = str(search or "").strip()
	or_filters = None
	if needle:
		like = f"%{needle}%"
		or_filters = [["DocType", "name", "like", like], ["DocType", "module", "like", like]]
	start = max(int(start), 0)
	page_length = min(max(int(page_length), 1), 100)
	wanted = page_length + 1
	eligible_seen = 0
	result = []
	database_start = 0
	batch_size = 100
	while len(result) < wanted:
		rows = frappe.get_list(
			"DocType",
			filters={"istable": 0, "issingle": 0},
			or_filters=or_filters,
			fields=["name", "module", "is_submittable", "is_virtual"],
			order_by="name asc",
			start=database_start,
			ignore_permissions=True,
			limit=batch_size,
		)
		if not rows:
			break
		for row in rows:
			if row.is_virtual or not is_eligible_doctype(row.name, permission_type=permission_type, user=user):
				continue
			if eligible_seen >= start:
				result.append(
					{
						"name": row.name,
						"label": _(row.name),
						"module": row.module,
						"is_submittable": bool(row.is_submittable),
						"permission_type": permission_type,
					}
				)
				if len(result) >= wanted:
					break
			eligible_seen += 1
		database_start += len(rows)
		if len(rows) < batch_size:
			break
	return {"rows": result[:page_length], "has_more": len(result) > page_length}


def _table_field_details(df, *, parent_doctype: str, permission_type: str, user: str | None) -> dict:
	"""Resolve child metadata without treating a table field as a database column."""
	details = {
		"child_doctype": str(df.options or ""),
		"child_fields": [],
		"link_fieldname": None,
		"link_doctype": None,
		"unsupported_reason": None,
	}
	if not df.options:
		details["unsupported_reason"] = _("Child table DocType is not configured.")
		return details
	try:
		child_meta = frappe.get_meta(df.options)
	except frappe.DoesNotExistError:
		details["unsupported_reason"] = _("Child table DocType no longer exists.")
		return details
	if not child_meta.istable:
		details["unsupported_reason"] = _("Configured table target is not a child DocType.")
		return details
	child_permitted = set(
		child_meta.get_permitted_fieldnames(
			parenttype=parent_doctype,
			user=user,
			permission_type=permission_type,
		)
	)
	details["child_fields"] = [
		{
			"fieldname": child.fieldname,
			"label": _(child.label or child.fieldname),
			"fieldtype": child.fieldtype,
			"options": child.options,
			"required": bool(child.reqd),
		}
		for child in child_meta.fields
		if child.fieldname in child_permitted and child.fieldtype in SUPPORTED_SCALAR_FIELD_TYPES
	]
	if df.fieldtype != "Table MultiSelect":
		return details
	links = [child for child in child_meta.fields if child.fieldtype == "Link" and child.fieldname in child_permitted]
	listed_links = [child for child in links if child.in_list_view]
	link = listed_links[0] if len(listed_links) == 1 else links[0] if len(links) == 1 else None
	if not link:
		details["unsupported_reason"] = _(
			"Table MultiSelect needs one unambiguous permitted Link field in its child DocType."
		)
		return details
	details["link_fieldname"] = link.fieldname
	details["link_doctype"] = link.options
	if not link.options:
		details["unsupported_reason"] = _("Table MultiSelect Link target is not configured.")
	return details


def _permitted_collection_fieldnames(meta, *, parenttype: str | None, permission_type: str, user: str | None) -> set[str]:
	"""Return permitted table fields, which Frappe intentionally omits from DB-column catalogs."""
	permissions = meta.get_permissions(parenttype=parenttype)
	if not permissions:
		return {
			df.fieldname
			for df in meta.fields
			if df.fieldname and df.fieldtype in SUPPORTED_COLLECTION_FIELD_TYPES
		}
	permlevels = set(
		meta.get_permlevel_access(
			permission_type=permission_type,
			parenttype=parenttype,
			user=user,
		)
	)
	if 0 not in permlevels and permission_type in {"read", "select"}:
		check_doctype = parenttype if meta.istable and parenttype else meta.name
		if frappe.share.get_shared(check_doctype, user, rights=["read"], limit=1):
			permlevels.add(0)
	return {
		df.fieldname
		for df in meta.fields
		if df.fieldname
		and df.fieldtype in SUPPORTED_COLLECTION_FIELD_TYPES
		and (df.permlevel or 0) in permlevels
	}


def _field_capabilities(df, details: dict) -> dict[str, bool]:
	scalar = df.fieldtype in SUPPORTED_SCALAR_FIELD_TYPES
	table = df.fieldtype in SUPPORTED_COLLECTION_FIELD_TYPES
	multiselect = df.fieldtype == "Table MultiSelect" and not details.get("unsupported_reason")
	return {
		"scalar_read": scalar,
		"collection_read": table,
		"condition_scalar": scalar,
		"condition_collection": multiselect,
		"assignment_scalar": scalar,
		"assignment_collection": multiselect,
		"child_collection": table,
		"switch": scalar,
		"deduplicate": scalar,
		"snapshot": scalar or multiselect,
	}


def _standard_field_rows(permission_type: str, capability: str | None) -> list[dict]:
	definitions = {
		"name": ("ID", "Data", None),
		"owner": ("Owner", "Link", "User"),
		"creation": ("Created at", "Datetime", None),
		"modified": ("Last modified", "Datetime", None),
		"modified_by": ("Modified by", "Link", "User"),
		"docstatus": ("Document status", "Int", None),
	}
	rows = []
	for fieldname, (label, fieldtype, options) in definitions.items():
		writable = fieldname == "owner" and permission_type == "write"
		capabilities = {
			"scalar_read": permission_type == "read",
			"collection_read": False,
			"condition_scalar": permission_type == "read",
			"condition_collection": False,
			"assignment_scalar": writable,
			"assignment_collection": False,
			"child_collection": False,
			"switch": permission_type == "read",
			"deduplicate": permission_type == "read" and fieldname == "name",
			"snapshot": permission_type == "read",
		}
		if capability and not capabilities.get(capability, False):
			continue
		if permission_type in {"create", "delete"} or (permission_type == "write" and not writable):
			continue
		rows.append({
			"fieldname": fieldname,
			"label": _(label),
			"fieldtype": fieldtype,
			"options": options,
			"description": None,
			"default": None,
			"depends_on": None,
			"mandatory_depends_on": None,
			"required": False,
			"read_only": not writable,
			"allow_on_submit": writable,
			"ignore_user_permissions": False,
			"capabilities": capabilities,
		})
	return rows


def field_catalog_result(
	doctype: str,
	*,
	permission_type: str = "read",
	user: str | None = None,
	parenttype: str | None = None,
	capability: str | None = None,
) -> dict:
	try:
		meta = frappe.get_meta(doctype)
	except frappe.DoesNotExistError:
		access = doctype_eligibility(doctype, permission_type=permission_type, user=user)
		return {**access, "fields": [], "excluded_field_count": 0}
	if meta.istable:
		access = doctype_eligibility(parenttype, permission_type=permission_type, user=user)
		access = {**access, "doctype": doctype, "parenttype": parenttype}
	else:
		access = doctype_eligibility(doctype, permission_type=permission_type, user=user)
	if not access["available"]:
		return {**access, "fields": [], "excluded_field_count": 0}
	permitted = set(
		meta.get_permitted_fieldnames(
			parenttype=parenttype,
			user=user,
			permission_type=permission_type,
		)
	)
	permitted.update(
		_permitted_collection_fieldnames(
			meta,
			parenttype=parenttype,
			permission_type=permission_type,
			user=user,
		)
	)
	rows = [] if meta.istable else _standard_field_rows(permission_type, capability)
	excluded = 0
	seen_fieldnames = set()
	for df in meta.fields:
		if not df.fieldname or df.fieldname in seen_fieldnames:
			excluded += 1
			continue
		seen_fieldnames.add(df.fieldname)
		if df.fieldname not in permitted or df.fieldtype not in SUPPORTED_FIELD_TYPES:
			excluded += 1
			continue
		if df.read_only and permission_type in {"write", "create"}:
			excluded += 1
			continue
		details = _table_field_details(
			df,
			parent_doctype=parenttype or doctype,
			permission_type=permission_type,
			user=user,
		) if df.fieldtype in SUPPORTED_COLLECTION_FIELD_TYPES else {}
		capabilities = _field_capabilities(df, details)
		if capability and not capabilities.get(capability, False):
			excluded += 1
			continue
		rows.append({
			"fieldname": df.fieldname,
			"label": _(df.label or df.fieldname),
			"fieldtype": df.fieldtype,
			"options": df.options,
			"description": _(df.description) if df.description else None,
			"default": df.default,
			"depends_on": df.depends_on,
			"mandatory_depends_on": df.mandatory_depends_on,
			"required": bool(df.reqd),
			"read_only": bool(df.read_only),
			"allow_on_submit": bool(df.allow_on_submit),
			"ignore_user_permissions": bool(df.ignore_user_permissions),
			"capabilities": capabilities,
			**details,
		})
	return {**access, "fields": rows, "excluded_field_count": excluded}


def field_catalog(
	doctype: str,
	*,
	permission_type: str = "read",
	user: str | None = None,
	parenttype: str | None = None,
	capability: str | None = None,
) -> list[dict]:
	if capability is None:
		capability = "scalar_read" if permission_type == "read" else "assignment_scalar"
	result = field_catalog_result(
		doctype,
		permission_type=permission_type,
		user=user,
		parenttype=parenttype,
		capability=capability,
	)
	if not result["available"]:
		raise AutomationPermissionError(result["explanation"] or _("This DocType is unavailable for automation."))
	return result["fields"]


def assert_field_access(
	doctype: str,
	fieldname: str,
	*,
	permission_type: str,
	user: str | None = None,
	parenttype: str | None = None,
	capability: str | tuple[str, ...] | None = None,
) -> dict:
	rows = field_catalog_result(
		doctype,
		permission_type=permission_type,
		user=user,
		parenttype=parenttype,
		capability=capability if isinstance(capability, str) else None,
	)["fields"]
	allowed = {row["fieldname"]: row for row in rows}
	row = allowed.get(fieldname)
	if row and isinstance(capability, tuple) and not any(
		row.get("capabilities", {}).get(item, False) for item in capability
	):
		row = None
	if not row:
		raise AutomationPermissionError(
			_("Field {0}.{1} is unavailable for {2}.").format(doctype, fieldname, permission_type)
		)
	return row


def _get_plugin_nodes() -> list[dict]:
	if not hasattr(frappe.local, "automation_node_catalog_cache"):
		nodes = []
		for method in frappe.get_hooks("automation_nodes") or []:
			try:
				plugin_nodes = frappe.get_attr(method)()
				if isinstance(plugin_nodes, list):
					nodes.extend(plugin_nodes)
			except Exception:
				frappe.log_error(title="Failed to load automation nodes", message=frappe.get_traceback())
		frappe.local.automation_node_catalog_cache = nodes
	return frappe.local.automation_node_catalog_cache


def _authoring_availability(node_type: str, primary_doctype: str | None, execution_user: str | None) -> tuple[bool, str | None]:
	"""Return authoring availability without changing the immutable runtime contract.

	Existing graphs must remain readable and executable even when an integration is
	removed or a permission changes. This hint is therefore used only by the step
	catalogue; publish validation remains authoritative.
	"""
	if node_type == "action.asana" and "asana_integration" not in frappe.get_installed_apps():
		return False, _("Install the Asana Integration app to use this action.")
	if not primary_doctype:
		return True, None
	if node_type == "action.merge_contact" and primary_doctype != "Contact":
		return False, _("This action is available only in Contact workflows.")
	if node_type == "action.copy_record" and not doctype_eligibility(
		primary_doctype, permission_type="create", user=execution_user
	)["available"]:
		return False, _("The workflow execution user cannot create this DocType.")
	if node_type == "action.delete_record" and not doctype_eligibility(
		primary_doctype, permission_type="delete", user=execution_user
	)["available"]:
		return False, _("The workflow execution user cannot delete this DocType.")
	if node_type == "action.create_note" and not frappe.has_permission(
		"Note", ptype="create", user=execution_user or frappe.session.user
	):
		return False, _("The workflow execution user cannot create Desk Notes.")
	return True, None


def node_catalog(*, primary_doctype: str | None = None, execution_user: str | None = None) -> list[dict]:
	catalog = json.loads(json.dumps(NODE_CATALOG))
	for node in catalog:
		node["authoring_schema"] = NODE_AUTHORING_SCHEMAS.get(node["type"], {"required": []})
		node["output_paths"] = NODE_OUTPUT_PATHS.get(node["type"], [])
		node.setdefault("authoring_tier", "core")
		node["available"], node["unavailable_reason"] = _authoring_availability(
			node["type"], primary_doctype, execution_user
		)
	plugin_nodes = json.loads(json.dumps(_get_plugin_nodes()))
	for node in plugin_nodes:
		node.setdefault("authoring_schema", {"required": []})
		node.setdefault("output_paths", [])
		node.setdefault("authoring_tier", "advanced")
		node.setdefault("available", True)
		node.setdefault("unavailable_reason", None)
	catalog.extend(plugin_nodes)
	return catalog


def workflow_object_profile(primary_doctype: str | None) -> dict:
	"""Describe the enrolled object without assuming every record is a Contact."""
	doctype = str(primary_doctype or "").strip()
	traits = {"record"}
	traits.update(WORKFLOW_OBJECT_TRAITS.get(doctype, set()))
	fieldnames: set[str] = set()
	if doctype:
		try:
			fieldnames = {str(df.fieldname) for df in frappe.get_meta(doctype).fields if df.fieldname}
		except frappe.DoesNotExistError:
			fieldnames = set()
	if fieldnames.intersection(EMAIL_FIELDNAMES):
		traits.add("email_recipient")
	if fieldnames.intersection(PHONE_FIELDNAMES):
		traits.add("callable")
	return {
		"primary_doctype": doctype,
		# DocType names are the authoring contract. Translating "Lead" on this
		# site produces "Lead/Contact", which blurs the object boundary again.
		"label": doctype if doctype else _("record"),
		"traits": sorted(traits),
		"native_event_guidance": {
			"created": _("Use Record created; it listens to new {0} records directly.").format(doctype or _("records")),
			"changed": _("Use Record changed for a change event, or filter criteria for a business state such as qualified."),
		},
	}


def _business_event_available(definition: dict, traits: set[str], doctype: str, usage: str) -> bool:
	context = BUSINESS_EVENT_CONTEXT.get(definition["topic"], {})
	doctypes = context.get(f"{usage}_doctypes")
	if doctypes is not None:
		return doctype in doctypes
	required_traits = context.get(f"{usage}_traits", {"record"})
	return bool(traits.intersection(required_traits))


def business_event_catalog(primary_doctype: str | None = None, usage: str = "all") -> list[dict]:
	"""Return object-aware business topics for enrollment or event waits.

	Calling without a DocType preserves the historical complete catalogue for
	API clients. The builder supplies both the immutable primary DocType and the
	usage so it cannot offer contact-only events in an unrelated workflow.
	"""
	usage = str(usage or "all").strip().lower()
	if usage not in {"all", "trigger", "wait"}:
		raise ValueError("Event catalogue usage must be all, trigger, or wait")
	profile = workflow_object_profile(primary_doctype)
	doctype = profile["primary_doctype"]
	traits = set(profile["traits"])
	rows = []
	for raw_definition in BUSINESS_EVENT_CATALOG:
		definition = json.loads(json.dumps(raw_definition))
		context = BUSINESS_EVENT_CONTEXT.get(definition["topic"], {})
		available_for = [
			candidate
			for candidate in ("trigger", "wait")
			if not doctype or _business_event_available(definition, traits, doctype, candidate)
		]
		if usage != "all" and usage not in available_for:
			continue
		if doctype and definition["topic"].startswith("crm."):
			definition["category"] = _("CRM events")
		if doctype and definition["topic"] == "crm.contact.list.joined":
			definition["label"] = _("Joined a list")
		elif doctype and definition["topic"] == "crm.call.inbound":
			definition["label"] = _("Inbound call received")
		elif doctype and definition["topic"] == "crm.lead.qualified":
			definition["label"] = _("Qualification changed to Qualified")
		elif doctype and definition["topic"] == "communication.responded":
			definition["label"] = _("{0} replied").format(profile["label"])
		elif doctype and definition["topic"] == "commerce.store.login":
			definition["label"] = _("Signed in to customer portal")
		elif doctype and definition["topic"] == "commerce.order.created":
			definition["label"] = _("Placed an order")
		elif doctype and definition["topic"] == "commerce.order.abandoned":
			definition["label"] = _("Abandoned a cart")
		if doctype and definition["topic"] == "email.unsubscribed" and doctype != "Lead":
			definition["filter_fields"] = [
				field for field in definition["filter_fields"] if field["fieldname"] != "subscription_topic"
			]
			for field in definition["filter_fields"]:
				if field["fieldname"] == "email_type":
					field["options"] = "global\nrecord"
			context = dict(context)
			context["source_app"] = "Frappe Email Unsubscribe / Communication"
			context["setup_note"] = "Workflow email links create a global opt-out tied to the exact enrolled record. Existing record-specific Frappe opt-outs and linked Communication unsubscribe statuses are also supported."
		definition.update(
			{
				"available_for": available_for,
				"source_modes": list(context.get("source_modes") or ["enrolled_record"]),
				"source_node_types": list(context.get("source_node_types") or []),
				"producer_status": context.get("producer_status", "integration_required"),
				"source_app": context.get("source_app"),
				"setup_note": context.get("setup_note"),
				"trigger_alternative": context.get("trigger_alternative"),
				"record_resolution": _("The event must identify the enrolled {0} record.").format(
					profile["label"]
				),
			}
		)
		rows.append(definition)
	return rows


def business_event_available(topic: str, primary_doctype: str | None, usage: str) -> bool:
	"""Return whether a known topic belongs to this workflow-object context.

	Unknown custom/legacy topics remain valid because their adapter owns the
	contract; this check only constrains topics defined by the core catalogue.
	"""
	definition = get_business_event_definition(topic)
	if not definition or not primary_doctype:
		return True
	profile = workflow_object_profile(primary_doctype)
	return _business_event_available(
		definition,
		set(profile["traits"]),
		profile["primary_doctype"],
		str(usage or "").strip().lower(),
	)


def get_business_event_definition(topic: str) -> dict | None:
	topic = str(topic or "").strip()
	for definition in BUSINESS_EVENT_CATALOG:
		if definition["topic"] == topic:
			return json.loads(json.dumps(definition))
	return None


def get_business_event_context(topic: str) -> dict:
	"""Return authoring/runtime source capabilities for a stable business event."""
	context = BUSINESS_EVENT_CONTEXT.get(str(topic or "").strip()) or {}
	return {
		**context,
		"source_modes": list(context.get("source_modes") or ["enrolled_record"]),
		"source_node_types": list(context.get("source_node_types") or []),
	}


def get_node_definition(node_type: str) -> dict | None:
	for node in node_catalog():
		if node["type"] == node_type:
			return node
	return None
