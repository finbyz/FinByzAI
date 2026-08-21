import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

app_name = "finbyzai"
app_title = "FinByz AI"
app_publisher = "Finbyz Tech Pvt Ltd"
app_description = "AI-Powered Agents, Tools, and Knowledge Base Platform"
app_email = "info@finbyz.tech"
app_license = "gpl-3.0"

on_session_creation = "finbyzai.workflow_builder.integrations.capture_customer_portal_login"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/finbyzai/css/finbyzai.css"
# app_include_js = "/assets/finbyzai/js/finbyzai.js"

# include js, css files in header of web template
# web_include_css = "/assets/finbyzai/css/finbyzai.css"
# web_include_js = "/assets/finbyzai/js/finbyzai.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "finbyzai/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "finbyzai.utils.jinja_methods",
# 	"filters": "finbyzai.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "finbyzai.install.before_install"
after_install = "finbyzai.workflow_builder.setup.after_install"

after_migrate = "finbyzai.install.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "finbyzai.uninstall.before_uninstall"
# after_uninstall = "finbyzai.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "finbyzai.utils.before_app_install"
# after_app_install = "finbyzai.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "finbyzai.utils.before_app_uninstall"
# after_app_uninstall = "finbyzai.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "finbyzai.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

permission_query_conditions = {
    "AI Conversation": (
        "finbyzai.ai.doctype.ai_conversation.ai_conversation."
        "get_permission_query_conditions"
    ),
}

has_permission = {
    "AI Conversation": (
        "finbyzai.ai.doctype.ai_conversation.ai_conversation.has_permission"
    ),
}

# Workflow history intentionally keeps the original DocType/name after a source
# record is deleted. These audit and runtime rows must not become artificial
# blockers for normal Frappe deletion (including action.delete_record). Frappe
# will continue to reject deletion when any non-automation business document is
# linked to the source record.
ignore_links_on_delete = [
	"Automation Consent Record",
	"Automation Enrollment Decision",
	"Automation Enrollment Ledger",
	"Automation Outbox Event",
	"Automation Policy Evaluation",
	"Automation Run",
]

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

doc_events = {
	"*": {
		"after_insert": "finbyzai.workflow_builder.events.capture_after_insert",
		"on_update": "finbyzai.workflow_builder.events.capture_on_update",
	},
	"Call Log": {
		"after_insert": "finbyzai.workflow_builder.integrations.capture_aircall_inbound_call",
		"on_update": "finbyzai.workflow_builder.integrations.capture_aircall_inbound_call",
	},
	"Email Group Member": {
		"after_insert": "finbyzai.workflow_builder.integrations.capture_email_group_membership",
		"on_update": "finbyzai.workflow_builder.integrations.capture_email_group_membership",
	},
	"Communication": {
		"after_insert": "finbyzai.workflow_builder.integrations.capture_communication_event",
		"on_update": "finbyzai.workflow_builder.integrations.capture_communication_event",
	},
	"Email Unsubscribe": {
		"after_insert": "finbyzai.workflow_builder.integrations.capture_email_unsubscribe",
	},
	"Lead": {
		"on_update": "finbyzai.workflow_builder.integrations.capture_lead_qualified",
	},
	"Sales Order": {
		"after_insert": "finbyzai.workflow_builder.integrations.capture_sales_order_created",
	},
	"Web Page": {
		"on_update": "finbyzai.ai.doctype.knowledge_base.knowledge_base.update_ai_links_on_route_change"
	},
	"Blog Post": {
		"on_update": "finbyzai.ai.doctype.knowledge_base.knowledge_base.update_ai_links_on_route_change"
	},
	"Website Item": {
		"on_update": "finbyzai.ai.doctype.knowledge_base.knowledge_base.update_ai_links_on_route_change"
	},
	"Website Item Group": {
		"on_update": "finbyzai.ai.doctype.knowledge_base.knowledge_base.update_ai_links_on_route_change"
	}
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"finbyzai.workflow_builder.events.dispatch_pending_outbox",
		"finbyzai.workflow_builder.engine.recover_stale_external_effects",
		"finbyzai.workflow_builder.engine.recover_orphaned_active_runs",
		"finbyzai.workflow_builder.engine.release_due_timers",
		"finbyzai.workflow_builder.engine.dispatch_ready_tokens",
		"finbyzai.workflow_builder.bulk.dispatch_ready_backfills",
		"finbyzai.workflow_builder.bulk.dispatch_due_schedules",
	],
	"hourly": [
		"finbyzai.ai.doctype.knowledge_base.knowledge_base.process_queued_knowledge_bases",
		"finbyzai.workflow_builder.integrations.capture_abandoned_shopping_carts",
		"finbyzai.workflow_builder.maintenance.run_scheduled_log_cleanup",
	],
}

# Testing
# -------

# before_tests = "finbyzai.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "finbyzai.event.get_events"
# }
override_whitelisted_methods = {
	"finbyzreach.email_marketing.update_subscription_preferences": (
		"finbyzai.workflow_builder.integrations.update_reach_subscription_preferences"
	),
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "finbyzai.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["finbyzai.utils.before_request"]
# after_request = ["finbyzai.utils.after_request"]

# Job Events
# ----------
# before_job = ["finbyzai.utils.before_job"]
# after_job = ["finbyzai.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"finbyzai.auth.validate"
# ]

fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["module", "=", "FinByz AI"]
        ]
    }
]

website_route_rules = [
	{"from_route": "/workflow/<path:app_path>", "to_route": "workflow"},
]
