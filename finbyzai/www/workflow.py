from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import get_system_timezone

from finbyzai.workflow_builder.constants import AUTOMATION_ROLES

no_cache = 1


def _require_access() -> None:
	if frappe.session.user == "Guest":
		_redirect(f"/login?{urlencode({'redirect-to': frappe.local.request.full_path or '/workflow'})}")
	allowed_roles = set().union(*AUTOMATION_ROLES.values())
	if not allowed_roles.intersection(frappe.get_roles()):
		frappe.throw(_("An Automation role is required."), frappe.PermissionError)


def get_context():
	_require_access()
	csrf_token = frappe.sessions.get_csrf_token()
	boot = get_boot()
	boot.csrf_token = csrf_token
	context = frappe._dict(boot=boot, boot_json=frappe.as_json(boot))
	return context


@frappe.whitelist(methods=["POST"])
def get_context_for_dev():
	_require_access()
	if not frappe.conf.developer_mode:
		frappe.throw(_("This method is only available in developer mode."))
	boot = get_boot()
	boot.csrf_token = frappe.sessions.get_csrf_token()
	return boot


def get_boot():
	favicon = (
		frappe.get_cached_value("Website Settings", "Website Settings", "favicon")
		or "/assets/frappe/images/frappe-favicon.svg"
	)
	return frappe._dict(
		{
			"frappe_version": frappe.__version__,
			"site_name": frappe.local.site,
			"user": frappe.session.user,
			"roles": frappe.get_roles(),
			"desk_theme": frappe.get_cached_value("User", frappe.session.user, "desk_theme") or "Light",
			"read_only_mode": frappe.flags.read_only,
			"system_timezone": get_system_timezone(),
			"socketio_port": frappe.conf.get("socketio_port"),
			"favicon": favicon,
		}
	)


def _redirect(location: str):
	frappe.local.flags.redirect_location = location
	raise frappe.Redirect
