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
	"condition.if_else": ["matched"],
	"condition.switch": ["value", "matched_handle"],
	"condition.deduplicate": ["duplicate_name", "is_duplicate"],
	"delay.fixed": ["due_at", "released"],
	"delay.until_date": ["due_at", "released"],
	"delay.until_event": ["event_payload", "timed_out", "released", "matched_handle"],
	"delay.business_hours": ["released", "due_at", "timezone"],
	"transform.value": ["value"],
	"transform.associated_record": ["value", "linked_name"],
	"transform.child_records": ["values", "count"],
	"action.update_record": ["doctype", "name", "updated_fields"],
	"action.numeric_adjust": ["doctype", "name", "field", "previous", "new_value"],
	"action.delete_record": ["doctype", "name", "deleted"],
	"action.create_record": ["doctype", "name"],
	"action.manage_association": ["doctype", "name", "operation", "target_name"],
	"action.round_robin": ["doctype", "name", "assigned_to", "group", "assignment_version"],
	"action.create_todo": ["allocated_to"],
	"action.add_comment": ["comment"],
	"action.notify_user": ["for_user"],
	"action.send_email": ["email_queue", "recipient"],
	"action.send_sms": ["recipient", "status", "status_code", "consent_check"],
	"action.webhook": ["status_code", "response_hash"],
	"action.call_subflow": ["run_id", "status"],
}


NODE_CATALOG = [
	{"type": "trigger.manual", "label": "Manual enrollment", "category": "Triggers", "description": "Enroll selected records from the operator UI.", "default_config": {}},
	{"type": "trigger.document_insert", "label": "Record created", "category": "Triggers", "description": "Enroll after a new document is committed.", "default_config": {"condition": None}},
	{"type": "trigger.document_change", "label": "Record changed", "category": "Triggers", "description": "Enroll after a relevant document field changes.", "default_config": {"condition": None}},
	{"type": "trigger.schedule", "label": "Scheduled", "category": "Triggers", "description": "Enroll records through a durable schedule configured after publishing.", "default_config": {}},
	{"type": "condition.if_else", "label": "If / else", "category": "Logic", "description": "Choose a true or false path using typed conditions.", "default_config": {"condition": None}},
	{"type": "condition.switch", "label": "Value branch", "category": "Logic", "description": "Branch into multiple paths based on a single field's value.", "default_config": {"field": "", "cases": []}},
	{"type": "condition.deduplicate", "label": "Deduplicate", "category": "Logic", "description": "Branch if an existing record has the same value.", "default_config": {"match_field": ""}},
	{"type": "delay.fixed", "label": "Fixed delay", "category": "Logic", "description": "Wait durably without sleeping a worker.", "default_config": {"seconds": 3600}},
	{"type": "delay.until_date", "label": "Wait until date", "category": "Logic", "description": "Resume when a record date or datetime field is reached.", "default_config": {"field": ""}},
	{"type": "delay.until_event", "label": "Wait for event", "category": "Logic", "description": "Wait until an event occurs or a timeout is reached.", "default_config": {"event_topic": "", "timeout_seconds": 86400}},
	{"type": "delay.business_hours", "label": "Business hours", "category": "Logic", "description": "Wait until the next allowed execution window.", "default_config": {"calendar": "", "timezone": "UTC", "start_time": "09:00", "end_time": "17:00", "weekdays": [0, 1, 2, 3, 4]}},
	{"type": "transform.value", "label": "Transform value", "category": "Logic", "description": "Create a reusable value without changing the record.", "default_config": {"operation": "coalesce", "values": []}},
	{"type": "transform.associated_record", "label": "Associated record", "category": "Data", "description": "Fetch a property from a linked record.", "default_config": {"reference_field": "", "fetch_field": ""}},
	{"type": "transform.child_records", "label": "Child records", "category": "Data", "description": "Fetch properties from child table records.", "default_config": {"child_table_field": "", "fetch_field": ""}},
	{"type": "action.call_subflow", "label": "Call subflow", "category": "Logic", "description": "Execute another workflow as a subflow.", "default_config": {"subflow_id": "", "wait_for_completion": 1}},
	{"type": "action.update_record", "label": "Update record", "category": "Actions", "description": "Update writable fields on the enrolled record.", "default_config": {"assignments": []}},
	{"type": "action.numeric_adjust", "label": "Numeric adjust", "category": "Actions", "description": "Increase or decrease a numeric property.", "default_config": {"field": "", "operation": "add", "amount": 1}},
	{"type": "action.manage_association", "label": "Manage association", "category": "Actions", "description": "Idempotently link or unlink associated records.", "default_config": {"target_doctype": "", "target_name": "", "link_field": "", "operation": "link"}},
	{"type": "action.round_robin", "type_version": 2, "label": "Round robin assignment", "category": "Actions", "description": "Atomically rotate ownership across currently enabled group members.", "default_config": {"group": ""}},
	{"type": "action.delete_record", "label": "Delete record", "category": "Actions", "description": "Delete the enrolled record permanently.", "default_config": {}},
	{"type": "action.create_record", "label": "Create record", "category": "Actions", "description": "Create another permitted Frappe document.", "default_config": {"target_doctype": "", "assignments": []}},
	{"type": "action.create_todo", "label": "Create ToDo", "category": "Actions", "description": "Assign a ToDo linked to the enrolled record.", "default_config": {"allocated_to": "", "description": "", "priority": "Medium"}},
	{"type": "action.add_comment", "label": "Add comment", "category": "Actions", "description": "Add a timeline comment to the enrolled record.", "default_config": {"content": ""}},
	{"type": "action.notify_user", "label": "Notify user", "category": "Actions", "description": "Create an in-app notification.", "default_config": {"for_user": "", "subject": "", "message": ""}},
	{"type": "action.send_email", "label": "Send email", "category": "External", "description": "Queue a consent-aware email through Frappe Email Queue.", "default_config": {"recipient": {"kind": "literal", "value": ""}, "subject": {"kind": "literal", "value": ""}, "message": {"kind": "literal", "value": ""}, "purpose": "workflow", "require_consent": 1}},
	{"type": "action.send_sms", "label": "Send SMS", "category": "External", "description": "Send a text message via Frappe SMS Settings.", "default_config": {"recipient": {"kind": "literal", "value": ""}, "message": {"kind": "literal", "value": ""}, "purpose": "workflow", "require_consent": 1}},
	{"type": "action.webhook", "label": "Send webhook", "category": "External", "description": "POST signed JSON to an allowlisted HTTPS endpoint.", "default_config": {"integration_secret": "", "url": "", "payload": {}, "purpose": "workflow", "require_consent": 0}},
	{"type": "end.complete", "label": "Complete", "category": "Logic", "description": "Complete the workflow run.", "default_config": {}},
]


NODE_AUTHORING_SCHEMAS = {
	"condition.if_else": {"required": [{"path": "condition", "label": "Condition"}]},
	"condition.switch": {"required": [{"path": "field", "label": "Branch field"}, {"path": "cases", "label": "Cases"}]},
	"condition.deduplicate": {"required": [{"path": "match_field", "label": "Match field"}]},
	"delay.fixed": {"required": [{"path": "seconds", "label": "Duration"}]},
	"delay.until_date": {"required": [{"path": "field", "label": "Date field"}]},
	"delay.until_event": {"required": [{"path": "event_topic", "label": "Event topic"}, {"path": "timeout_seconds", "label": "Timeout"}]},
	"delay.business_hours": {"required": [{"path": "timezone", "label": "Timezone"}]},
	"transform.value": {"required": [{"path": "values", "label": "Inputs"}]},
	"transform.associated_record": {"required": [{"path": "reference_field", "label": "Link field"}, {"path": "fetch_field", "label": "Fetched field"}]},
	"transform.child_records": {"required": [{"path": "child_table_field", "label": "Child table field"}, {"path": "fetch_field", "label": "Child field"}]},
	"action.call_subflow": {"required": [{"path": "subflow_id", "label": "Subflow"}]},
	"action.update_record": {"required": [{"path": "assignments", "label": "Field changes"}]},
	"action.numeric_adjust": {"required": [{"path": "field", "label": "Target field"}, {"path": "amount", "label": "Amount"}]},
	"action.manage_association": {"required": [{"path": "target_doctype", "label": "Target DocType"}, {"path": "target_name", "label": "Target record"}, {"path": "link_field", "label": "Link field"}]},
	"action.round_robin": {"required": [{"path": "group", "label": "Assignment group"}]},
	"action.create_record": {"required": [{"path": "target_doctype", "label": "Target DocType"}, {"path": "assignments", "label": "Field values"}]},
	"action.create_todo": {"required": [{"path": "allocated_to", "label": "Assignee"}, {"path": "description", "label": "Task description"}]},
	"action.add_comment": {"required": [{"path": "content", "label": "Comment"}]},
	"action.notify_user": {"required": [{"path": "for_user", "label": "Recipient"}, {"path": "subject", "label": "Subject"}, {"path": "message", "label": "Message"}]},
	"action.send_email": {"required": [{"path": "recipient", "label": "Recipient"}, {"path": "subject", "label": "Subject"}, {"path": "message", "label": "Message"}, {"path": "purpose", "label": "Consent purpose"}]},
	"action.send_sms": {"required": [{"path": "recipient", "label": "Recipient"}, {"path": "message", "label": "Message"}, {"path": "purpose", "label": "Consent purpose"}]},
	"action.webhook": {"required": [{"path": "integration_secret", "label": "Integration secret"}, {"path": "url", "label": "HTTPS endpoint"}, {"path": "payload", "label": "JSON payload"}]},
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


def node_catalog() -> list[dict]:
	catalog = json.loads(json.dumps(NODE_CATALOG))
	for node in catalog:
		node["authoring_schema"] = NODE_AUTHORING_SCHEMAS.get(node["type"], {"required": []})
		node["output_paths"] = NODE_OUTPUT_PATHS.get(node["type"], [])
	plugin_nodes = json.loads(json.dumps(_get_plugin_nodes()))
	for node in plugin_nodes:
		node.setdefault("authoring_schema", {"required": []})
		node.setdefault("output_paths", [])
	catalog.extend(plugin_nodes)
	return catalog


def get_node_definition(node_type: str) -> dict | None:
	for node in node_catalog():
		if node["type"] == node_type:
			return node
	return None
