from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from .authoring import create_audit
from .configuration import automation_enabled
from .errors import AutomationConflictError, AutomationError
from .events import _register_dispatch_wake
from .registry import field_catalog
from .schema import event_filter_matches


def _payload_value(payload: dict, path: str) -> Any:
	value: Any = payload
	for segment in str(path or "").split("."):
		if not segment or not isinstance(value, dict) or segment not in value:
			return None
		value = value[segment]
	return value


def _secret(definition) -> str:
	return str(definition.get_password("secret_value", raise_exception=False) or "")


def _public_error(message: str, status_code: int):
	frappe.local.response.http_status_code = status_code
	raise AutomationError(message)


def _check_rate_limit(definition) -> None:
	limit = min(max(cint(definition.requests_per_minute), 1), 10000)
	bucket = int(time.time() // 60)
	key = f"automation-inbound-webhook:{definition.name}:{bucket}"
	try:
		count = cint(frappe.cache.incr(key))
		if count == 1:
			frappe.cache.expire(key, 120)
	except Exception:
		# Database idempotency still protects effects when cache is unavailable.
		return
	if count > limit:
		_public_error(_("Webhook rate limit exceeded."), 429)


def _authenticate(definition, raw: bytes) -> None:
	secret = _secret(definition)
	if not secret:
		_public_error(_("Webhook authentication is not configured."), 503)
	if definition.auth_type == "Bearer":
		provided = str(frappe.get_request_header("Authorization") or "")
		expected = f"Bearer {secret}"
	elif definition.auth_type == "HMAC SHA256":
		provided = str(frappe.get_request_header("X-Automation-Signature") or "")
		digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
		expected = f"sha256={digest}"
		if not provided.startswith("sha256="):
			expected = digest
	else:
		_public_error(_("Unsupported webhook authentication."), 503)
	if not hmac.compare_digest(provided.encode(), expected.encode()):
		_public_error(_("Webhook authentication failed."), 401)


def _validate_identity(definition, payload: dict) -> str:
	identity = _payload_value(payload, definition.payload_record_path)
	if identity is None or isinstance(identity, (dict, list)) or not str(identity).strip():
		_public_error(_("The payload does not contain the configured record identity."), 422)
	fieldname = str(definition.record_identity_field or "name")
	if fieldname == "name":
		name = str(identity).strip()
		if not frappe.db.exists(definition.record_doctype, name, cache=False):
			_public_error(_("No matching workflow record was found."), 422)
		return name
	meta = frappe.get_meta(definition.record_doctype)
	field = meta.get_field(fieldname)
	if not field or not getattr(field, "unique", False):
		_public_error(_("The configured identity field is no longer a permitted unique field."), 503)
	rows = frappe.get_all(definition.record_doctype, filters={fieldname: identity}, pluck="name", limit=2)
	if len(rows) != 1:
		_public_error(_("The payload identity must resolve to exactly one workflow record."), 422)
	return rows[0]


def create_definition(
	workflow_name: str,
	title: str,
	*,
	auth_type: str = "HMAC SHA256",
	record_identity_field: str = "name",
	payload_record_path: str = "record_id",
	payload_fields: Any = None,
	payload_filters: Any = None,
	idempotency_path: str = "event_id",
	max_request_bytes: int = 262144,
	requests_per_minute: int = 60,
) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("write")
	if not workflow.active_version or workflow.status != "ACTIVE":
		raise AutomationConflictError(_("Publish and activate the Webhook-mode workflow first."))
	from .engine import published_trigger_type

	if published_trigger_type(workflow.active_version) != "trigger.webhook":
		raise AutomationConflictError(_("Only a workflow published in Webhook enrollment mode can create an endpoint."))
	auth_type = str(auth_type or "").strip()
	if auth_type not in {"HMAC SHA256", "Bearer"}:
		raise AutomationError(_("Choose HMAC SHA256 or Bearer authentication."))
	identity_field = str(record_identity_field or "name").strip()
	if identity_field != "name":
		catalog = {field["fieldname"]: field for field in field_catalog(workflow.primary_doctype, user=workflow.execution_user)}
		meta_field = frappe.get_meta(workflow.primary_doctype).get_field(identity_field)
		if identity_field not in catalog or not meta_field or not getattr(meta_field, "unique", False):
			raise AutomationError(_("Record mapping may use name or a permitted field marked Unique."))
	endpoint_key = frappe.generate_hash(length=40)
	secret = frappe.generate_hash(length=48)
	doc = frappe.get_doc(
		{
			"doctype": "Automation Inbound Webhook",
			"title": str(title or "Inbound workflow webhook")[:140],
			"workflow": workflow.name,
			"workflow_version": workflow.active_version,
			"enabled": 0,
			"endpoint_key": endpoint_key,
			"auth_type": auth_type,
			"secret_value": secret,
			"record_doctype": workflow.primary_doctype,
			"record_identity_field": identity_field,
			"payload_record_path": str(payload_record_path or "record_id")[:140],
			"payload_fields_json": json.dumps(frappe.parse_json(payload_fields) if isinstance(payload_fields, str) else payload_fields or []),
			"payload_filters_json": json.dumps(frappe.parse_json(payload_filters) if isinstance(payload_filters, str) else payload_filters),
			"idempotency_path": str(idempotency_path or "event_id")[:140],
			"max_request_bytes": min(max(cint(max_request_bytes), 1024), 1048576),
			"requests_per_minute": min(max(cint(requests_per_minute), 1), 10000),
		}
	).insert()
	create_audit(workflow.name, "INBOUND_WEBHOOK_CREATED", {"webhook": doc.name, "auth_type": auth_type})
	return {
		"name": doc.name,
		"endpoint": f"/api/method/finbyzai.workflow_builder.api.receive_inbound_webhook?endpoint_key={endpoint_key}",
		"secret": secret,
		"enabled": False,
	}


def rotate_secret(name: str) -> dict:
	doc = frappe.get_doc("Automation Inbound Webhook", name)
	doc.check_permission("write")
	secret = frappe.generate_hash(length=48)
	doc.secret_value = secret
	doc.save()
	create_audit(doc.workflow, "INBOUND_WEBHOOK_SECRET_ROTATED", {"webhook": doc.name})
	return {"name": doc.name, "secret": secret}


def set_enabled(name: str, enabled: bool) -> dict:
	doc = frappe.get_doc("Automation Inbound Webhook", name)
	doc.check_permission("write")
	workflow = frappe.get_doc("Automation Workflow", doc.workflow)
	if enabled and (workflow.status != "ACTIVE" or workflow.active_version != doc.workflow_version):
		raise AutomationConflictError(_("The endpoint is pinned to an inactive workflow version. Create a new endpoint for the active version."))
	doc.enabled = bool(enabled)
	doc.save()
	create_audit(doc.workflow, "INBOUND_WEBHOOK_ENABLED" if enabled else "INBOUND_WEBHOOK_DISABLED", {"webhook": doc.name})
	return {"name": doc.name, "enabled": bool(doc.enabled)}


def list_definitions(workflow_name: str) -> dict:
	workflow = frappe.get_doc("Automation Workflow", workflow_name)
	workflow.check_permission("read")
	rows = frappe.get_list(
		"Automation Inbound Webhook",
		filters={"workflow": workflow.name},
		fields=["name", "title", "workflow_version", "enabled", "auth_type", "record_doctype", "record_identity_field", "payload_record_path", "payload_fields_json", "payload_filters_json", "idempotency_path", "max_request_bytes", "requests_per_minute", "last_received_at", "last_result", "modified"],
		order_by="creation desc",
		limit=100,
	)
	for row in rows:
		row.endpoint = f"/api/method/finbyzai.workflow_builder.api.receive_inbound_webhook?endpoint_key={frappe.db.get_value('Automation Inbound Webhook', row.name, 'endpoint_key')}"
	return {"rows": rows}


def receive(endpoint_key: str) -> dict:
	if not automation_enabled():
		_public_error(_("Automation is unavailable."), 503)
	definition_name = frappe.db.get_value("Automation Inbound Webhook", {"endpoint_key": str(endpoint_key or "")}, "name")
	if not definition_name:
		_public_error(_("Webhook endpoint was not found."), 404)
	definition = frappe.get_doc("Automation Inbound Webhook", definition_name)
	if not definition.enabled:
		_public_error(_("Webhook endpoint is disabled."), 404)
	raw = frappe.request.get_data(cache=True) or b""
	if len(raw) > min(max(cint(definition.max_request_bytes), 1024), 1048576):
		_public_error(_("Webhook request is too large."), 413)
	_authenticate(definition, raw)
	_check_rate_limit(definition)
	try:
		payload = json.loads(raw.decode("utf-8"))
	except (UnicodeDecodeError, json.JSONDecodeError):
		_public_error(_("Webhook body must be a JSON object."), 400)
	if not isinstance(payload, dict):
		_public_error(_("Webhook body must be a JSON object."), 400)
	filters = frappe.parse_json(definition.payload_filters_json) if definition.payload_filters_json else None
	if filters and not event_filter_matches(filters, payload):
		_public_error(_("Webhook payload did not match the configured filters."), 422)
	record_name = _validate_identity(definition, payload)
	idempotency = _payload_value(payload, definition.idempotency_path)
	if idempotency is None or isinstance(idempotency, (dict, list)) or not str(idempotency).strip():
		_public_error(_("The payload must contain the configured idempotency key."), 422)
	receipt_source = f"{definition.name}:{str(idempotency).strip()}"
	event_id = f"webhook:{hashlib.sha256(receipt_source.encode()).hexdigest()}"
	existing = frappe.db.get_value("Automation Outbox Event", {"event_id": event_id}, ["name", "status"], as_dict=True)
	if existing:
		return {"receipt": hashlib.sha256(event_id.encode()).hexdigest()[:24], "accepted": True, "deduplicated": True}
	workflow = frappe.db.get_value("Automation Workflow", definition.workflow, ["status", "active_version"], as_dict=True)
	if not workflow or workflow.status != "ACTIVE" or workflow.active_version != definition.workflow_version:
		_public_error(_("Webhook endpoint is pinned to an inactive workflow version."), 409)
	event = frappe.get_doc(
		{
			"doctype": "Automation Outbox Event",
			"event_id": event_id,
			"event_type": "WEBHOOK",
			"object_doctype": definition.record_doctype,
			"object_name": record_name,
			"changed_fields_json": "[]",
			"changed_values_json": "{}",
			"decision_json": json.dumps({"webhook": {"definition": definition.name, "workflow": definition.workflow, "workflow_version": definition.workflow_version}}),
			"status": "PENDING",
			"attempts": 0,
			"available_at": now_datetime(),
			"trace_id": frappe.generate_hash(length=20),
			"recursion_depth": 0,
		}
	).insert(ignore_permissions=True)
	receipt = hashlib.sha256(event_id.encode()).hexdigest()[:24]
	frappe.db.set_value("Automation Inbound Webhook", definition.name, {"last_received_at": now_datetime(), "last_result": "Accepted", "last_receipt_hash": receipt}, update_modified=False)
	_register_dispatch_wake()
	frappe.local.response.http_status_code = 202
	return {"receipt": receipt, "accepted": True, "deduplicated": False, "event": event.name}
