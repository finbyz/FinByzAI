"""Guarded end-to-end UAT helpers for configured production integrations.

These functions are intentionally not whitelisted and are not part of the
workflow runtime API.  A System Manager can invoke them with ``bench execute``
to create uniquely-prefixed records, exercise real provider boundaries, gather
receipts, and remove only those records again.
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlsplit

import frappe
import pyotp
import requests
from frappe.utils import cint, getdate, get_url, now_datetime
from frappe.utils.verified_command import get_signed_params

from . import engine
from .authoring import (
	change_workflow_state,
	create_workflow_record,
	delete_workflow_record,
	publish_workflow,
	save_workflow_draft,
)


CONFIRMATION = "RUN_LIVE_WORKFLOW_UAT"
PREFIX = "WF-UAT-"


def _require_live_confirmation(confirmation: str) -> None:
	frappe.only_for("System Manager")
	if str(confirmation or "") != CONFIRMATION:
		frappe.throw(
			f"Live UAT is disabled unless confirmation is exactly {CONFIRMATION}.",
			frappe.PermissionError,
		)


def _tag() -> str:
	return f"{now_datetime():%Y%m%d-%H%M%S}-{frappe.generate_hash(length=6).upper()}"


def _literal(value):
	return {"kind": "literal", "value": value}


def _create_action_workflow(tag: str, record, suffix: str, node_type: str, config: dict) -> dict:
	created = create_workflow_record(
		f"{PREFIX}{tag}-{suffix}",
		record.doctype,
		description=f"Disposable live UAT workflow {tag}",
		execution_user="Administrator",
		trigger_type="trigger.manual",
	)
	graph = created["graph"]
	graph["nodes"].append(
		{"id": "action", "type": node_type, "type_version": 1, "config": config}
	)
	graph["edges"] = [
		{
			"id": "trigger-action",
			"source": graph["start_node_id"],
			"source_handle": "default",
			"target": "action",
		}
	]
	saved = save_workflow_draft(created["workflow"], created["draft_revision"], graph)
	if not saved["valid"]:
		frappe.throw(f"Live UAT workflow {suffix} is invalid: {saved['validation']}")
	published = publish_workflow(
		created["workflow"], saved["draft_revision"], activate=True, reenrollment="ALWAYS"
	)
	run_name = engine.enroll(
		created["workflow"],
		record.doctype,
		record.name,
		source="MANUAL",
		occurrence_key=f"{tag}:{suffix}",
	)
	if not run_name:
		frappe.throw(f"Live UAT workflow {suffix} did not enroll its test record.")
	return {
		"workflow": created["workflow"],
		"version": published["version"],
		"run": run_name,
	}


def _create_event_workflow(tag: str, record, suffix: str, topic: str, event_filter=None) -> dict:
	created = create_workflow_record(
		f"{PREFIX}{tag}-{suffix}",
		record.doctype,
		description=f"Disposable live event UAT workflow {tag}",
		execution_user="Administrator",
		trigger_type="trigger.event",
	)
	graph = created["graph"]
	trigger = next(node for node in graph["nodes"] if node["id"] == graph["start_node_id"])
	trigger["type_version"] = 2
	trigger["config"] = {
		"events": [
			{
				"id": f"{suffix.lower()}-event",
				"event_topic": topic,
				"event_filter": event_filter,
			}
		],
		"condition": None,
	}
	graph["nodes"].append(
		{
			"id": "receipt",
			"type": "action.add_comment",
			"type_version": 1,
			"config": {"content": f"{PREFIX}{tag} received {topic}"},
		}
	)
	graph["edges"] = [
		{
			"id": "trigger-receipt",
			"source": graph["start_node_id"],
			"source_handle": "default",
			"target": "receipt",
		}
	]
	saved = save_workflow_draft(created["workflow"], created["draft_revision"], graph)
	if not saved["valid"]:
		frappe.throw(f"Live event UAT workflow {suffix} is invalid: {saved['validation']}")
	published = publish_workflow(
		created["workflow"], saved["draft_revision"], activate=True, reenrollment="ALWAYS"
	)
	return {"workflow": created["workflow"], "version": published["version"]}


def _create_portal_fixture(tag: str, password: str):
	user_email = f"wf-uat-{tag.lower()}@example.invalid"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": user_email,
			"first_name": f"Workflow UAT {tag}",
			"enabled": 1,
			"user_type": "Website User",
			"send_welcome_email": 0,
			"new_password": password,
		}
	).insert(ignore_permissions=True)

	customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")
	if not customer_group:
		customer_group = frappe.db.get_value(
			"Customer Group", {"is_group": 0}, "name", order_by="lft asc"
		)
	territory = frappe.db.get_single_value("Selling Settings", "territory")
	if not territory:
		territory = frappe.db.get_value("Territory", {"is_group": 0}, "name", order_by="lft asc")
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": f"{PREFIX}{tag}-Customer",
			"customer_type": "Company",
			"customer_group": customer_group,
			"territory": territory,
			"portal_users": [{"user": user.name}],
		}
	).insert(ignore_permissions=True)
	return user, customer


def _create_reach_fixture(tag: str):
	from finbyzreach.email_marketing import SCHEDULER_RECIPIENT_INSERT_FLAG

	topic = frappe.get_doc(
		{
			"doctype": "Subscription Topic",
			"topic_name": f"{PREFIX}{tag}-Topic",
			"description": f"Disposable workflow UAT topic {tag}",
			"disabled": 0,
		}
	).insert(ignore_permissions=True)
	lead = frappe.get_doc(
		{
			"doctype": "Lead",
			"first_name": f"{PREFIX}{tag}-Reach",
			"email_id": f"wf-uat-{tag.lower()}@example.invalid",
		}
	).insert(ignore_permissions=True)
	campaign = frappe.get_doc(
		{
			"doctype": "Campaign",
			"campaign_name": f"{PREFIX}{tag}-Campaign",
			"custom_subscription_topic": topic.name,
			"custom_broadcast_status": "Draft",
		}
	).insert(ignore_permissions=True)
	previous = frappe.flags.get(SCHEDULER_RECIPIENT_INSERT_FLAG)
	frappe.flags[SCHEDULER_RECIPIENT_INSERT_FLAG] = True
	try:
		recipient = frappe.get_doc(
			{
				"doctype": "Email Campaign",
				"campaign_name": campaign.name,
				"email_campaign_for": "Lead",
				"recipient": lead.name,
				"start_date": getdate(),
				"custom_recipient_email": lead.email_id,
				"custom_normalized_email": lead.email_id.lower(),
				"custom_delivery_status": "Planned",
				"custom_scheduled_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
	finally:
		frappe.flags[SCHEDULER_RECIPIENT_INSERT_FLAG] = previous
	return topic, lead, campaign, recipient


def start_live_uat(
	confirmation: str,
	recipient_email: str,
	webhook_url: str,
) -> dict:
	"""Create and start isolated email, Asana, webhook, portal, and Reach UAT flows."""
	_require_live_confirmation(confirmation)
	password = os.environ.get("WF_UAT_PORTAL_PASSWORD", "")
	if len(password) < 12:
		frappe.throw("WF_UAT_PORTAL_PASSWORD must contain at least 12 characters.")
	recipient_email = str(recipient_email or "").strip().lower()
	email_account = frappe.db.get_value(
		"Email Account",
		{"email_id": recipient_email, "enable_incoming": 1, "enable_outgoing": 1},
		"name",
	)
	if not email_account:
		frappe.throw("Live email UAT requires a configured incoming and outgoing Email Account.")
	parts = urlsplit(str(webhook_url or "").strip())
	if parts.scheme != "https" or not parts.hostname:
		frappe.throw("Live webhook UAT requires an HTTPS receiver URL.")

	tag = _tag()
	lead = frappe.get_doc(
		{
			"doctype": "Lead",
			"first_name": f"{PREFIX}{tag}-External",
			# The actual recipient is explicit in the email action. Keep the
			# disposable ERP record isolated from any existing mailbox owner.
			"email_id": f"wf-uat-external-{tag.lower()}@example.invalid",
		}
	).insert(ignore_permissions=True)
	secret = frappe.get_doc(
		{
			"doctype": "Automation Integration Secret",
			"title": f"{PREFIX}{tag}-Webhook",
			"enabled": 1,
			"auth_type": "None",
			"allowed_hosts": parts.hostname,
			"requests_per_minute": 10,
		}
	).insert(ignore_permissions=True)
	subject = f"{PREFIX}{tag} actual mailbox delivery"
	flows = {
		"email": _create_action_workflow(
			tag,
			lead,
			"Email",
			"action.send_email",
			{
				"content_mode": "inline",
				"recipient": _literal(recipient_email),
				"subject": _literal(subject),
				"message": _literal(
					f"<p>Disposable FinbyzAI workflow UAT receipt {tag}.</p>"
				),
				"raw_html": 1,
				"sender_email": recipient_email,
			},
		),
		"asana": _create_action_workflow(
			tag,
			lead,
			"Asana",
			"action.asana",
			{
				"operation": "create_task",
				"target_gid": _literal(""),
				"payload": {
					"name": _literal(f"{PREFIX}{tag} workflow action"),
					"notes": _literal(f"Disposable FinbyzAI live UAT task {tag}"),
				},
			},
		),
		"webhook": _create_action_workflow(
			tag,
			lead,
			"Webhook",
			"action.webhook",
			{
				"integration_secret": secret.name,
				"url": webhook_url,
				"payload": {
					"uat_tag": _literal(tag),
					"record_doctype": _literal(lead.doctype),
					"record_name": _literal(lead.name),
				},
				"purpose": "live-uat",
				"require_consent": 0,
			},
		),
	}
	portal_user, customer = _create_portal_fixture(tag, password)
	flows["portal"] = _create_event_workflow(
		tag, customer, "Portal", "commerce.store.login"
	)
	topic, reach_lead, campaign, recipient = _create_reach_fixture(tag)
	flows["reach"] = _create_event_workflow(
		tag,
		reach_lead,
		"Reach",
		"email.unsubscribed",
		{
			"kind": "predicate",
			"field": "subscription_topic",
			"operator": "eq",
			"value": topic.name,
		},
	)
	frappe.db.commit()
	return {
		"tag": tag,
		"subject": subject,
		"email_account": email_account,
		"portal_user": portal_user.name,
		"customer": customer.name,
		"reach_lead": reach_lead.name,
		"reach_topic": topic.name,
		"reach_campaign": campaign.name,
		"reach_recipient": recipient.name,
		"integration_secret": secret.name,
		"flows": flows,
	}


def exercise_public_flows(confirmation: str, tag: str) -> dict:
	"""Use public HTTPS routes for a real portal login and Reach preference save."""
	_require_live_confirmation(confirmation)
	password = os.environ.get("WF_UAT_PORTAL_PASSWORD", "")
	user = frappe.db.get_value("User", {"email": f"wf-uat-{tag.lower()}@example.invalid"}, "name")
	recipient = frappe.db.get_value(
		"Email Campaign", {"owner": "Administrator", "recipient": ["like", f"%{PREFIX}{tag}-Reach%"]}, "name"
	)
	if not recipient:
		recipient = frappe.db.get_value(
			"Email Campaign",
			{"campaign_name": ["like", f"%{PREFIX}{tag}-Campaign%"]},
			"name",
		)
	if not user or not recipient:
		frappe.throw("The requested live UAT fixtures do not exist.")
	topic = frappe.db.get_value(
		"Campaign", frappe.db.get_value("Email Campaign", recipient, "campaign_name"), "custom_subscription_topic"
	)
	email = frappe.db.get_value("Email Campaign", recipient, "custom_normalized_email")
	base_url = get_url().rstrip("/")

	portal_session = requests.Session()
	login = portal_session.post(
		f"{base_url}/api/method/login",
		data={"usr": user, "pwd": password},
		timeout=(5, 30),
	)
	login_payload = login.json() if login.headers.get("content-type", "").startswith("application/json") else {}
	tmp_id = str(login_payload.get("tmp_id") or "")
	second_factor_status = None
	if tmp_id:
		# The production site requires 2FA. Complete the real HTTP login for this
		# disposable account using the short-lived server-side secret created for
		# the first step; no permanent 2FA secret or bypass is introduced.
		otp_secret = frappe.safe_decode(frappe.cache.get(f"{tmp_id}_otp_secret"))
		if not otp_secret:
			frappe.throw("The live portal login did not create a usable 2FA challenge.")
		second_factor = portal_session.post(
			f"{base_url}/api/method/login",
			data={"otp": pyotp.TOTP(otp_secret).now(), "tmp_id": tmp_id},
			timeout=(5, 30),
		)
		second_factor_status = second_factor.status_code
		if second_factor.headers.get("content-type", "").startswith("application/json"):
			login_payload = second_factor.json()
	portal = portal_session.get(f"{base_url}/portal", timeout=(5, 30), allow_redirects=False)
	portal_session.get(f"{base_url}/api/method/logout", timeout=(5, 30))

	signed_query = get_signed_params({"campaign_recipient": recipient})
	preference_page = requests.get(
		f"{base_url}/manage_subscriptions?{signed_query}", timeout=(5, 30)
	)
	preference_save = requests.post(
		f"{base_url}/api/method/finbyzreach.email_marketing.update_subscription_preferences",
		data={
			"campaign_recipient": recipient,
			"email": email,
			"unsubscribed_topics": json.dumps([topic]),
			"signed_query_string": signed_query,
		},
		timeout=(5, 30),
	)
	return {
		"portal_login_status": login.status_code,
		"portal_two_factor_required": bool(tmp_id),
		"portal_two_factor_status": second_factor_status,
		"portal_login_message": login_payload.get("message"),
		"portal_page_status": portal.status_code,
		"portal_redirect": portal.headers.get("location"),
		"preference_page_status": preference_page.status_code,
		"preference_page_contains_topic": topic in preference_page.text,
		"preference_save_status": preference_save.status_code,
		"preference_save_message": (preference_save.json().get("message") if preference_save.headers.get("content-type", "").startswith("application/json") else None),
	}


def _workflow_rows(tag: str) -> list[dict]:
	workflows = frappe.get_all(
		"Automation Workflow",
		filters={"title": ["like", f"{PREFIX}{tag}-%"]},
		fields=["name", "title", "status"],
		order_by="title asc",
		limit_page_length=0,
	)
	for workflow in workflows:
		workflow["runs"] = frappe.get_all(
			"Automation Run",
			filters={"workflow": workflow.name},
			fields=["name", "status", "error_code", "error_message", "record_doctype", "record_name"],
			order_by="creation asc",
			limit_page_length=0,
		)
		for run in workflow["runs"]:
			run["effects"] = frappe.get_all(
				"Automation Effect Ledger",
				filters={"run": run.name},
				fields=["name", "node_id", "status", "result_json"],
				order_by="creation asc",
				limit_page_length=0,
			)
	return workflows


def _email_receipt(tag: str, workflows: list[dict]) -> dict:
	email_flow = next((row for row in workflows if row.title.endswith("-Email")), None)
	if not email_flow or not email_flow["runs"]:
		return {"queue": None, "queue_status": None, "mailbox_matches": 0}
	effects = email_flow["runs"][0].get("effects") or []
	result = json.loads(effects[0].result_json or "{}") if effects else {}
	queue_name = result.get("email_queue")
	if not queue_name or not frappe.db.exists("Email Queue", queue_name):
		return {"queue": queue_name, "queue_status": None, "mailbox_matches": 0}
	queue_status = frappe.db.get_value("Email Queue", queue_name, "status")
	if queue_status not in {"Sent", "Sending"}:
		from frappe.email.doctype.email_queue.email_queue import send_now

		send_now(queue_name, force_send=True)
		queue_status = frappe.db.get_value("Email Queue", queue_name, "status")

	subject = f"{PREFIX}{tag} actual mailbox delivery"
	recipient = result.get("recipient")
	account_name = frappe.db.get_value(
		"Email Account", {"email_id": recipient, "enable_incoming": 1}, "name"
	)
	matches = 0
	if account_name:
		account = frappe.get_doc("Email Account", account_name)
		server = account.get_incoming_server(in_receive=True, email_sync_rule="ALL")
		try:
			for _attempt in range(10):
				server.imap.select("INBOX", readonly=True)
				status, data = server.imap.search(None, "SUBJECT", f'"{subject}"')
				if status == "OK" and data and data[0]:
					matches = len(data[0].split())
					break
				time.sleep(3)
		finally:
			server.logout()
	return {"queue": queue_name, "queue_status": queue_status, "mailbox_matches": matches}


def _asana_receipt(workflows: list[dict]) -> dict:
	asana_flow = next((row for row in workflows if row.title.endswith("-Asana")), None)
	if not asana_flow or not asana_flow["runs"]:
		return {"gid": None, "provider_found": False}
	effects = asana_flow["runs"][0].get("effects") or []
	result = json.loads(effects[0].result_json or "{}") if effects else {}
	gid = result.get("gid")
	if not gid:
		return {"gid": None, "provider_found": False}
	from asana_integration import client as asana_client
	import asana

	response = asana.TasksApi(asana_client.get_api_client()).get_task(
		gid,
		{"opt_fields": "gid,name,permalink_url,completed,modified_at"},
	)
	if hasattr(response, "to_dict"):
		response = response.to_dict()
	if isinstance(response, dict) and isinstance(response.get("data"), dict):
		response = response["data"]
	return {
		"gid": gid,
		"provider_found": bool(isinstance(response, dict) and response.get("gid") == gid),
		"name": response.get("name") if isinstance(response, dict) else None,
		"completed": response.get("completed") if isinstance(response, dict) else None,
		"permalink_url": response.get("permalink_url") if isinstance(response, dict) else None,
	}


def collect_live_uat(confirmation: str, tag: str, verify_mailbox: int = 1) -> dict:
	"""Collect durable workflow/provider evidence; optionally send and search IMAP."""
	_require_live_confirmation(confirmation)
	workflows = _workflow_rows(tag)
	result = {"tag": tag, "workflows": workflows}
	result["asana"] = _asana_receipt(workflows)
	if cint(verify_mailbox):
		result["email"] = _email_receipt(tag, workflows)
	reach_lead = frappe.db.get_value("Lead", {"first_name": f"{PREFIX}{tag}-Reach"}, "name")
	topic = frappe.db.get_value("Subscription Topic", {"topic_name": f"{PREFIX}{tag}-Topic"}, "name")
	result["reach"] = {
		"lead": reach_lead,
		"topic": topic,
		"topic_unsubscribed": bool(
			reach_lead
			and topic
			and frappe.db.exists(
				"Unsubscribe Topic Multi Select",
				{
					"parent": reach_lead,
					"parentfield": "custom_unsubscribe_topics",
					"subscription_topic": topic,
				},
			)
		),
	}
	return result


def provision_browser_uat(confirmation: str, tag: str) -> dict:
	"""Create an API-authenticated System Manager and a multi-trigger draft."""
	_require_live_confirmation(confirmation)
	user_email = f"wf-uat-browser-{tag.lower()}@example.invalid"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": user_email,
			"first_name": f"Workflow Browser UAT {tag}",
			"enabled": 1,
			"user_type": "System User",
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	user.add_roles("System Manager")
	user.api_key = frappe.generate_hash(length=15)
	api_secret = frappe.generate_hash(length=24)
	user.api_secret = api_secret
	user.save(ignore_permissions=True)

	created = create_workflow_record(
		f"{PREFIX}{tag}-Browser",
		"Lead",
		description=f"Disposable real-browser UAT workflow {tag}",
		execution_user="Administrator",
		trigger_type="trigger.any",
	)
	graph = created["graph"]
	trigger = next(node for node in graph["nodes"] if node["id"] == graph["start_node_id"])
	trigger["config"] = {
		"triggers": [
			{
				"id": "created",
				"type": "trigger.document_insert",
				"config": {"condition": None},
			},
			{
				"id": "changed",
				"type": "trigger.document_change",
				"config": {"watched_fields": ["status"], "condition": None},
			},
			{
				"id": "unsubscribed",
				"type": "trigger.event",
				"config": {
					"event_topic": "email.unsubscribed",
					"event_filter": None,
					"condition": None,
				},
			},
		],
	}
	saved = save_workflow_draft(created["workflow"], created["draft_revision"], graph)
	if not saved["valid"]:
		frappe.throw(f"Live browser UAT workflow is invalid: {saved['validation']}")
	frappe.db.commit()
	return {
		"tag": tag,
		"user": user.name,
		"api_key": user.api_key,
		"api_secret": api_secret,
		"workflow": created["workflow"],
		"title": f"{PREFIX}{tag}-Browser",
	}


def cleanup_live_uat(confirmation: str, tag: str) -> dict:
	"""Remove only resources carrying the exact disposable UAT tag."""
	_require_live_confirmation(confirmation)
	if not tag or not all(character.isalnum() or character == "-" for character in tag):
		frappe.throw("Invalid live UAT tag.")
	removed: dict[str, list[str]] = {}
	errors: list[str] = []

	def remove(doctype: str, name: str | None, *, force: bool = False):
		if not name or not frappe.db.exists(doctype, name):
			return
		try:
			frappe.delete_doc(doctype, name, ignore_permissions=True, force=force)
			removed.setdefault(doctype, []).append(name)
		except Exception as exc:
			errors.append(f"{doctype} {name}: {exc}")

	workflows = _workflow_rows(tag)
	for row in workflows:
		for run in row["runs"]:
			for effect in run.get("effects") or []:
				result = json.loads(effect.result_json or "{}")
				if gid := result.get("gid"):
					try:
						from asana_integration.client import delete_task

						delete_task(gid)
						removed.setdefault("Asana Task", []).append(gid)
					except Exception as exc:
						errors.append(f"Asana Task {gid}: {exc}")
				if queue_name := result.get("email_queue"):
					remove("Email Queue", queue_name, force=True)
		if row.status in {"ACTIVE", "PAUSED"}:
			change_workflow_state(row.name, "DISABLED")
		try:
			delete_workflow_record(row.name, delete_history=True)
			removed.setdefault("Automation Workflow", []).append(row.name)
		except Exception as exc:
			errors.append(f"Automation Workflow {row.name}: {exc}")

	for recipient in frappe.get_all(
		"Email Campaign",
		filters={"campaign_name": ["like", f"%{PREFIX}{tag}-Campaign%"]},
		pluck="name",
		limit_page_length=0,
	):
		remove("Email Campaign", recipient, force=True)
	for campaign in frappe.get_all(
		"Campaign", filters={"campaign_name": f"{PREFIX}{tag}-Campaign"}, pluck="name", limit_page_length=0
	):
		remove("Campaign", campaign, force=True)
	for lead in frappe.get_all(
		"Lead", filters={"first_name": ["in", [f"{PREFIX}{tag}-External", f"{PREFIX}{tag}-Reach"]]}, pluck="name", limit_page_length=0
	):
		remove("Lead", lead, force=True)
	for customer in frappe.get_all(
		"Customer", filters={"customer_name": f"{PREFIX}{tag}-Customer"}, pluck="name", limit_page_length=0
	):
		remove("Customer", customer, force=True)
	remove("User", f"wf-uat-{tag.lower()}@example.invalid", force=True)
	remove("User", f"wf-uat-browser-{tag.lower()}@example.invalid", force=True)
	for topic in frappe.get_all(
		"Subscription Topic", filters={"topic_name": f"{PREFIX}{tag}-Topic"}, pluck="name", limit_page_length=0
	):
		remove("Subscription Topic", topic, force=True)
	remove("Automation Integration Secret", f"{PREFIX}{tag}-Webhook", force=True)
	frappe.db.commit()
	return {"tag": tag, "removed": removed, "errors": errors}
