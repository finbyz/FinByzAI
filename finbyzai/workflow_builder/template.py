from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

from .authoring import validate_bindings, validate_settings
from .constants import MAX_GRAPH_BYTES
from .errors import AutomationError, AutomationPermissionError
from .registry import doctype_eligibility
from .schema import canonical_json, parse_object, validate_graph


PACKAGE_TYPE = "Automation Workflow Template"
PACKAGE_VERSION = 1
MAX_PACKAGE_BYTES = MAX_GRAPH_BYTES + 256 * 1024
CATEGORIES = {"Sales", "Marketing", "Operations", "Support"}


def _required_text(value: Any, label: str, maximum: int) -> str:
	text = str(value or "").strip()
	if not text:
		raise AutomationError(_("{0} is required.").format(label))
	if len(text) > maximum:
		raise AutomationError(_("{0} is too long.").format(label))
	return text


def validate_template_values(
	*,
	title: Any,
	category: Any,
	description: Any,
	primary_doctype: Any,
	graph_value: Any,
	settings_value: Any,
	execution_user: str | None = None,
) -> dict:
	"""Validate the complete unsigned, site-local template contract."""
	title = _required_text(title, _("Template title"), 140)
	category = _required_text(category, _("Template category"), 40)
	if category not in CATEGORIES:
		raise AutomationError(_("Unsupported template category."))
	description = str(description or "").strip()
	if len(description) > 2000:
		raise AutomationError(_("Template description is too long."))
	primary_doctype = _required_text(primary_doctype, _("Primary DocType"), 140)
	user = execution_user or frappe.session.user
	access = doctype_eligibility(primary_doctype, permission_type="read", user=user)
	if not access["available"]:
		raise AutomationPermissionError(access["explanation"])
	validation = validate_graph(graph_value, primary_doctype=primary_doctype, publish=True)
	issues = list(validation["issues"])
	issues.extend(validate_bindings(validation["graph"], user))
	settings, setting_issues = validate_settings(settings_value or {}, primary_doctype, user)
	issues.extend(setting_issues)
	if issues:
		raise AutomationError(_("Template is invalid: {0}").format(issues[0]["message"]))
	return {
		"title": title,
		"category": category,
		"description": description,
		"primary_doctype": primary_doctype,
		"graph": validation["graph"],
		"settings": settings,
		"graph_hash": validation["graph_hash"],
	}


def package_from_template(template_name: str) -> dict:
	template = frappe.get_doc("Automation Workflow Template", template_name)
	template.check_permission("read")
	values = validate_template_values(
		title=template.title,
		category=template.category,
		description=template.description,
		primary_doctype=template.primary_doctype,
		graph_value=template.graph_json,
		settings_value=template.settings_json or {},
	)
	return {
		"package_version": PACKAGE_VERSION,
		"type": PACKAGE_TYPE,
		"metadata": {
			"title": values["title"],
			"category": values["category"],
			"description": values["description"],
			"primary_doctype": values["primary_doctype"],
		},
		"graph": values["graph"],
		"settings": values["settings"],
	}


def export_template(template_name: str) -> str:
	return json.dumps(package_from_template(template_name), indent=2, ensure_ascii=False)


def parse_template_package(json_data: Any) -> dict:
	if isinstance(json_data, bytes):
		raw = json_data
	elif isinstance(json_data, str):
		raw = json_data.encode()
	else:
		raw = canonical_json(json_data).encode()
	if len(raw) > MAX_PACKAGE_BYTES:
		raise AutomationError(_("Template package exceeds the {0} byte limit.").format(MAX_PACKAGE_BYTES))
	try:
		package = json.loads(raw)
	except (TypeError, ValueError, UnicodeDecodeError) as exc:
		raise AutomationError(_("Invalid template JSON package.")) from exc
	if not isinstance(package, dict) or set(package) != {"package_version", "type", "metadata", "graph", "settings"}:
		raise AutomationError(_("Template package must contain only version, type, metadata, graph, and settings."))
	if package.get("package_version") != PACKAGE_VERSION or package.get("type") != PACKAGE_TYPE:
		raise AutomationError(_("Unsupported template package version or type."))
	metadata = parse_object(package.get("metadata"), "template metadata")
	if set(metadata) != {"title", "category", "description", "primary_doctype"}:
		raise AutomationError(_("Template metadata has missing or unsupported fields."))
	return validate_template_values(
		title=metadata.get("title"),
		category=metadata.get("category"),
		description=metadata.get("description"),
		primary_doctype=metadata.get("primary_doctype"),
		graph_value=package.get("graph"),
		settings_value=package.get("settings"),
	)


def import_template(json_data: Any) -> dict:
	values = parse_template_package(json_data)
	template = frappe.get_doc(
		{
			"doctype": "Automation Workflow Template",
			"title": values["title"],
			"category": values["category"],
			"description": values["description"],
			"primary_doctype": values["primary_doctype"],
			"graph_json": canonical_json(values["graph"]),
			"settings_json": canonical_json(values["settings"]),
		}
	).insert()
	return {"name": template.name, "title": template.title}


def load_template(template_name: str) -> tuple[Any, dict]:
	template = frappe.get_doc("Automation Workflow Template", template_name)
	template.check_permission("read")
	values = validate_template_values(
		title=template.title,
		category=template.category,
		description=template.description,
		primary_doctype=template.primary_doctype,
		graph_value=template.graph_json,
		settings_value=template.settings_json or {},
	)
	return template, values
