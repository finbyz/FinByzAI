from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

import certifi
import frappe
import requests
import urllib3
from frappe import _
from frappe.utils import cint, now_datetime, validate_email_address

from .configuration import external_actions_enabled
from .emailing import resolve_email_content
from .errors import AutomationError, AutomationTransientError
from .schema import resolve_value


MAX_WEBHOOK_RESPONSE_BYTES = 1024 * 1024


class AutomationUnknownCommitError(AutomationError):
	code = "WF_UNKNOWN_COMMIT"
	http_status_code = 409


def transport_readiness() -> dict:
	"""Report configuration readiness without sending or exposing credentials.

	A configured provider is not claimed as live-verified. Delivery verification
	must run through a controlled UAT workflow so consent, queueing, idempotency,
	provider responses, and observability are exercised together.
	"""
	outgoing_accounts = frappe.get_all(
		"Email Account",
		filters={"enable_outgoing": 1},
		fields=["name", "default_outgoing"],
		limit_page_length=0,
	)
	email_configured = bool(outgoing_accounts)
	sms_hook = bool(frappe.get_hooks("send_sms"))
	sms_settings = frappe.get_single("SMS Settings")
	sms_configured = sms_hook or bool(
		sms_settings.sms_gateway_url and sms_settings.message_parameter and sms_settings.receiver_parameter
	)
	webhook_secrets = frappe.get_all(
		"Automation Integration Secret",
		filters={"enabled": 1, "allowed_hosts": ["is", "set"]},
		fields=["name"],
		limit_page_length=0,
	)
	webhook_configured = bool(webhook_secrets)
	return {
		"email": {
			"configured": email_configured,
			"provider_count": len(outgoing_accounts),
			"live_verified": False,
			"message": _("Outgoing email is configured; verify one queued delivery in UAT.")
			if email_configured
			else _("Configure an enabled outgoing Email Account."),
		},
		"sms": {
			"configured": sms_configured,
			"provider_count": 1 if sms_configured else 0,
			"live_verified": False,
			"message": _("An SMS provider is configured; verify one accepted submission in UAT.")
			if sms_configured
			else _("Configure Frappe SMS Settings or a send_sms hook."),
		},
		"webhook": {
			"configured": webhook_configured,
			"provider_count": len(webhook_secrets),
			"live_verified": False,
			"message": _("An enabled allowlisted webhook secret exists; verify one controlled 2xx request in UAT.")
			if webhook_configured
			else _("Create an enabled integration secret with at least one exact allowed hostname."),
		},
	}


def _resolve_tree(value: Any, *, record, outputs: dict[str, Any]) -> Any:
	if isinstance(value, dict) and value.get("kind") in {"literal", "record_field", "node_output"}:
		return resolve_value(value, record=record, outputs=outputs)
	if isinstance(value, dict):
		return {str(key): _resolve_tree(item, record=record, outputs=outputs) for key, item in value.items()}
	if isinstance(value, list):
		return [_resolve_tree(item, record=record, outputs=outputs) for item in value]
	return value


def _require_consent(run, *, channel: str, purpose: str, recipient: str | None, required: bool) -> None:
	if not frappe.db.table_exists("Automation Consent Record"):
		if required:
			raise AutomationError(_("Consent storage is unavailable."))
		return
	rows = frappe.get_list(
		"Automation Consent Record",
		filters={
			"record_doctype": run.record_doctype,
			"record_name": run.record_name,
			"channel": channel,
			"purpose": purpose,
			"recipient": recipient or "",
			"effective_at": ["<=", now_datetime()],
		},
		fields=["status", "expires_at"],
		order_by="effective_at desc, creation desc",
		ignore_permissions=True,
		limit=1,
	)
	row = rows[0] if rows else None
	if row and row.status in {"DENIED", "REVOKED"}:
		raise AutomationError(_("The recipient is suppressed for this channel and purpose."))
	if required and (not row or row.status != "GRANTED" or (row.expires_at and row.expires_at <= now_datetime())):
		raise AutomationError(_("No current consent grant exists for this external action."))


def queue_email(run, config: dict, *, record, outputs: dict[str, Any], workflow_settings: dict | None = None) -> dict:
	if not external_actions_enabled():
		raise AutomationError(_("External workflow actions are disabled in Automation Settings."))
	recipient = str(_resolve_tree(config.get("recipient"), record=record, outputs=outputs) or "").strip()
	if not validate_email_address(recipient, throw=False):
		raise AutomationError(_("Email action resolved to an invalid recipient."))
	content = resolve_email_content(
		config, record=record, outputs=outputs, primary_doctype=run.record_doctype
	)
	subject = content["subject"]
	message = content["message"]
	if not subject or not message:
		raise AutomationError(_("Email subject and message are required."))
	communication = (workflow_settings or {}).get("communication") or {}
	sender_email = str(config.get("sender_email") or communication.get("default_sender_email") or "").strip()
	sender_name = str(config.get("sender_name") or communication.get("default_sender_name") or "").strip()
	if sender_email and not frappe.db.exists(
		"Email Account", {"email_id": sender_email, "enable_outgoing": 1}
	):
		raise AutomationError(_("Choose the address of an enabled outgoing Email Account."))
	sender = f"{sender_name} <{sender_email}>" if sender_email and sender_name else sender_email
	reply_to = str(config.get("reply_to") or "").strip()
	if reply_to and not validate_email_address(reply_to, throw=False):
		raise AutomationError(_("Email Reply-To address is invalid."))
	queue = frappe.sendmail(
		recipients=[recipient],
		sender=sender,
		reply_to=reply_to or None,
		subject=subject,
		content=message,
		delayed=True,
		reference_doctype=run.record_doctype,
		reference_name=run.record_name,
		add_unsubscribe_link=1,
		raw_html=bool(content["raw_html"]),
		add_css=not bool(content["raw_html"]),
	)
	if not queue or not getattr(queue, "name", None):
		raise AutomationError(_("Frappe did not create an Email Queue record."))
	return {
		"email_queue": queue.name,
		"recipient": recipient,
		"sender": sender or None,
		"reply_to": reply_to or None,
		"email_template": content.get("email_template"),
		"content_hash": content.get("content_hash"),
	}


def _safe_webhook_url(url: str, allowed_hosts: set[str]) -> tuple[str, str, tuple[str, ...]]:
	parts = urlsplit(str(url or "").strip())
	if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
		raise AutomationError(_("Webhook URL must be an HTTPS URL without embedded credentials."))
	host = parts.hostname.rstrip(".").lower()
	try:
		ipaddress.ip_address(host)
	except ValueError:
		pass
	else:
		raise AutomationError(_("Webhook URL must use an allowlisted DNS hostname, not an IP literal."))
	if host not in allowed_hosts:
		raise AutomationError(_("Webhook hostname is not allowed by the selected integration secret."))
	try:
		addresses = {item[4][0] for item in socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)}
	except OSError as exc:
		raise AutomationTransientError(_("Webhook hostname could not be resolved.")) from exc
	if not addresses:
		raise AutomationTransientError(_("Webhook hostname did not resolve to an address."))
	for address in addresses:
		ip = ipaddress.ip_address(address)
		if not ip.is_global:
			raise AutomationError(_("Webhook hostname resolves to a non-public network address."))
	return parts.geturl(), host, tuple(sorted(addresses))


def _post_pinned(url: str, host: str, addresses: tuple[str, ...], body: bytes, headers: dict[str, str]):
	"""Connect only to a DNS answer already checked as public, while verifying TLS for the hostname."""
	parts = urlsplit(url)
	port = parts.port or 443
	path = parts.path or "/"
	if parts.query:
		path = f"{path}?{parts.query}"
	headers = {**headers, "Host": host if port == 443 else f"{host}:{port}"}
	last_error = None
	for address in addresses:
		pool = urllib3.HTTPSConnectionPool(
			address,
			port=port,
			timeout=urllib3.Timeout(connect=5, read=20),
			retries=False,
			cert_reqs=ssl.CERT_REQUIRED,
			ca_certs=certifi.where(),
			assert_hostname=host,
			server_hostname=host,
			maxsize=1,
			block=True,
		)
		response = None
		try:
			response = pool.request("POST", path, body=body, headers=headers, redirect=False, preload_content=False)
			data = response.read(MAX_WEBHOOK_RESPONSE_BYTES + 1, cache_content=False)
			if len(data) > MAX_WEBHOOK_RESPONSE_BYTES:
				raise AutomationError(_("Webhook response exceeded the 1 MiB safety limit."))
			return response.status, data
		except (urllib3.exceptions.ConnectTimeoutError, urllib3.exceptions.NewConnectionError) as exc:
			last_error = exc
			continue
		finally:
			if response is not None:
				response.release_conn()
			pool.close()
	if last_error:
		raise AutomationTransientError(_("Webhook connection failed before a response was received.")) from last_error
	raise AutomationTransientError(_("Webhook endpoint has no usable public address."))


def _consume_rate_limit(secret_name: str, limit: int) -> None:
	limit = min(max(cint(limit), 1), 10000)
	key = frappe.cache.make_key(f"automation:webhook-rate:{secret_name}")
	try:
		value = cint(
			frappe.cache.eval(
				"""
				local value = redis.call('INCR', KEYS[1])
				if value == 1 then
					redis.call('EXPIRE', KEYS[1], ARGV[1])
				end
				return value
				""",
				1,
				key,
				60,
			)
		)
	except Exception as exc:
		raise AutomationTransientError(_("Webhook rate-limit storage is unavailable.")) from exc
	if value > limit:
		raise AutomationTransientError(_("Webhook provider rate limit reached."))


def send_webhook(run, config: dict, *, record, outputs: dict[str, Any], effect_key: str) -> dict:
	if not external_actions_enabled():
		raise AutomationError(_("External workflow actions are disabled in Automation Settings."))
	secret_name = str(config.get("integration_secret") or "")
	secret = frappe.get_doc("Automation Integration Secret", secret_name)
	if not secret.enabled:
		raise AutomationError(_("The selected integration secret is disabled."))
	allowed_hosts = {line.strip().lower() for line in (secret.allowed_hosts or "").splitlines() if line.strip()}
	url, host, addresses = _safe_webhook_url(str(config.get("url") or ""), allowed_hosts)
	_consented_recipient = str(config.get("consent_recipient") or "")
	_require_consent(
		run,
		channel=str(config.get("_consent_channel") or "WEBHOOK"),
		purpose=str(config.get("purpose") or "workflow")[:140],
		recipient=_consented_recipient,
		required=bool(cint(config.get("require_consent", 0))),
	)
	_consume_rate_limit(secret.name, secret.requests_per_minute or 60)
	payload = _resolve_tree(config.get("payload") or {}, record=record, outputs=outputs)
	body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode()
	headers = {
		"Content-Type": "application/json",
		"Accept": "application/json",
		"Idempotency-Key": effect_key,
		"User-Agent": "Frappe-Automation/1.0",
	}
	secret_value = secret.get_password("secret_value", raise_exception=False) or ""
	if secret.auth_type == "Bearer":
		headers["Authorization"] = f"Bearer {secret_value}"
	elif secret.auth_type == "API Key":
		headers[str(secret.header_name)] = secret_value
	elif secret.auth_type == "HMAC SHA256":
		headers[str(secret.header_name or "X-Automation-Signature")] = hmac.new(secret_value.encode(), body, hashlib.sha256).hexdigest()
	try:
		status, response_data = _post_pinned(url, host, addresses, body, headers)
	except (urllib3.exceptions.ReadTimeoutError, urllib3.exceptions.ProtocolError) as exc:
		raise AutomationUnknownCommitError(_("Webhook timed out after transmission; delivery state is unknown.")) from exc
	if status == 429 or status >= 500:
		raise AutomationTransientError(_("Webhook provider returned HTTP {0}.").format(status))
	if not 200 <= status < 300:
		raise AutomationError(_("Webhook provider rejected the request with HTTP {0}.").format(status))
	return {"status_code": status, "response_hash": hashlib.sha256(response_data).hexdigest()}


def send_instagram_message(run, config: dict, *, record, outputs: dict[str, Any], effect_key: str) -> dict:
	recipient_id = str(_resolve_tree(config.get("recipient_id"), record=record, outputs=outputs) or "").strip()
	message = str(_resolve_tree(config.get("message"), record=record, outputs=outputs) or "").strip()
	if not recipient_id or len(recipient_id) > 140 or not message or len(message) > 2000:
		raise AutomationError(_("Instagram recipient and a message up to 2,000 characters are required."))
	result = send_webhook(
		run,
		{
			**config,
			"_consent_channel": "INSTAGRAM",
			"consent_recipient": recipient_id,
			"payload": {"recipient": {"id": recipient_id}, "message": {"text": message}},
		},
		record=record,
		outputs=outputs,
		effect_key=effect_key,
	)
	return {**result, "recipient_id": recipient_id}


def execute_asana(config: dict, *, record, outputs: dict[str, Any]) -> dict:
	if "asana_integration" not in frappe.get_installed_apps():
		raise AutomationError(_("The Asana Integration app is not installed."))
	settings = frappe.get_cached_doc("Asana Settings")
	if not settings.enabled or not settings.workspace_gid:
		raise AutomationError(_("Asana Settings are disabled or incomplete."))
	payload = _resolve_tree(config.get("payload") or {}, record=record, outputs=outputs)
	if not isinstance(payload, dict) or not payload or len(json.dumps(payload, default=str)) > 128 * 1024:
		raise AutomationError(_("Asana fields must be a non-empty JSON object no larger than 128 KiB."))
	operation = str(config.get("operation") or "")
	target_gid = str(_resolve_tree(config.get("target_gid"), record=record, outputs=outputs) or "").strip()
	try:
		from asana_integration import client as asana_client

		if operation == "create_task":
			payload.setdefault("workspace", settings.workspace_gid)
			response = asana_client.create_task(payload)
		elif operation == "update_task":
			if not target_gid:
				raise AutomationError(_("Asana task GID is required for update."))
			response = asana_client.update_task(target_gid, payload)
		elif operation == "create_subtask":
			if not target_gid:
				raise AutomationError(_("Parent Asana task GID is required for a subtask."))
			payload["parent"] = target_gid
			response = asana_client.create_task(payload)
		elif operation == "create_project":
			import asana

			payload.setdefault("workspace", settings.workspace_gid)
			api = asana.ProjectsApi(asana_client.get_api_client())
			response = api.create_project({"data": payload}, {"opt_fields": "gid,name,permalink_url"})
			if hasattr(response, "to_dict"):
				response = response.to_dict()
			if isinstance(response, dict) and isinstance(response.get("data"), dict):
				response = response["data"]
		else:
			raise AutomationError(_("Unsupported Asana operation."))
	except AutomationError:
		raise
	except Exception as exc:
		status = cint(getattr(exc, "status", 0) or getattr(getattr(exc, "response", None), "status_code", 0))
		if status == 429 or status >= 500:
			raise AutomationTransientError(_("Asana temporarily rejected the request.")) from exc
		raise AutomationError(_("Asana rejected the request: {0}").format(str(exc)[:500])) from exc
	if not isinstance(response, dict):
		response = {"value": str(response)}
	return {
		"gid": response.get("gid"),
		"name": response.get("name"),
		"permalink_url": response.get("permalink_url"),
		"operation": operation,
	}


def _normalise_sms_recipient(value: Any) -> str:
	recipient = str(value or "").strip()
	for character in (" ", "-", "(", ")"):
		recipient = recipient.replace(character, "")
	if not re.fullmatch(r"\+?[0-9]{3,20}", recipient):
		raise AutomationError(_("SMS action resolved to an invalid recipient."))
	return recipient


def _send_sms_via_frappe_gateway(recipient: str, message: str) -> int:
	"""Send one SMS with bounded I/O and an observable delivery result."""
	from frappe.core.doctype.sms_settings.sms_settings import create_sms_log, get_headers

	settings = frappe.get_doc("SMS Settings", "SMS Settings")
	if not settings.sms_gateway_url or not settings.message_parameter or not settings.receiver_parameter:
		raise AutomationError(_("SMS Settings are incomplete. Configure the gateway URL and parameter names."))

	headers = get_headers(settings)
	use_json = headers.get("Content-Type") == "application/json"
	params = {settings.message_parameter: message, settings.receiver_parameter: recipient}
	for parameter in settings.get("parameters"):
		if not parameter.header:
			params[parameter.parameter] = parameter.value

	request_kwargs: dict[str, Any] = {"headers": headers, "timeout": (5, 20)}
	if use_json:
		request_kwargs["json"] = params
	elif settings.use_post:
		request_kwargs["data"] = params
	else:
		request_kwargs["params"] = params

	try:
		response = requests.request("POST" if settings.use_post else "GET", settings.sms_gateway_url, **request_kwargs)
	except requests.ConnectTimeout as exc:
		raise AutomationTransientError(_("SMS gateway connection timed out before transmission.")) from exc
	except requests.ReadTimeout as exc:
		raise AutomationUnknownCommitError(_("SMS gateway timed out after transmission; delivery state is unknown.")) from exc
	except requests.ConnectionError as exc:
		# A connection can be dropped after the provider accepted the request. Retrying
		# automatically could send the same text twice, so require reconciliation.
		raise AutomationUnknownCommitError(_("SMS gateway connection was lost; delivery state is unknown.")) from exc

	if response.status_code == 429 or response.status_code >= 500:
		raise AutomationTransientError(_("SMS gateway returned HTTP {0}.").format(response.status_code))
	if not 200 <= response.status_code < 300:
		raise AutomationError(_("SMS gateway rejected the request with HTTP {0}.").format(response.status_code))

	create_sms_log(
		{"receiver_list": [recipient], "message": message.encode("utf-8"), "success_msg": False},
		[recipient],
	)
	return response.status_code


def send_frappe_sms(run, config: dict, *, record, outputs: dict[str, Any], workflow_settings: dict | None = None) -> dict:
	if not external_actions_enabled():
		raise AutomationError(_("External workflow actions are disabled in Automation Settings."))

	recipient = _normalise_sms_recipient(resolve_value(config.get("recipient"), record=record, outputs=outputs))
	message = str(resolve_value(config.get("message"), record=record, outputs=outputs) or "")
	if not message.strip():
		raise AutomationError(_("Recipient and message are required for sending SMS."))

	_require_consent(
		run,
		channel="SMS",
		purpose=str(config.get("purpose") or "workflow")[:140],
		recipient=recipient,
		required=bool(cint(config.get("require_consent", 1))),
	)

	hook_methods = frappe.get_hooks("send_sms")
	sender_name = str(config.get("sender_name") or ((workflow_settings or {}).get("communication") or {}).get("default_sms_sender") or "")
	if hook_methods:
		# Preserve Frappe's documented extension point. A successful return means the
		# custom provider accepted responsibility for delivery.
		frappe.get_attr(hook_methods[-1])([recipient], message, sender_name, False)
		status_code = None
	else:
		status_code = _send_sms_via_frappe_gateway(recipient, message)

	result = {"recipient": recipient, "status": "SENT", "status_code": status_code, "consent_check": True}
	if sender_name:
		result["sender"] = sender_name
	return result


def execute_external(node_type: str, run, config: dict, *, record, outputs: dict[str, Any], effect_key: str, workflow_settings: dict | None = None) -> dict:
	if node_type == "action.send_email":
		output = queue_email(run, config, record=record, outputs=outputs, workflow_settings=workflow_settings)
	elif node_type == "action.webhook":
		output = send_webhook(run, config, record=record, outputs=outputs, effect_key=effect_key)
	elif node_type == "action.instagram_message":
		output = send_instagram_message(run, config, record=record, outputs=outputs, effect_key=effect_key)
	elif node_type == "action.asana":
		output = execute_asana(config, record=record, outputs=outputs)
	elif node_type == "action.send_sms":
		output = send_frappe_sms(run, config, record=record, outputs=outputs, workflow_settings=workflow_settings)
	else:
		raise AutomationError(_("Unsupported external action."))
	return {"status": "COMPLETE", "output": output}
