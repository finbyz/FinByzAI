app_name = "finbyzai"
app_title = "FinByz AI"
app_publisher = "Finbyz Tech Pvt Ltd"
app_description = "AI-Powered Agents, Tools, and Knowledge Base Platform"
app_email = "info@finbyz.tech"
app_license = "MIT"

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
# after_install = "finbyzai.install.after_install"

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

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": [
		"finbyzai.ai.doctype.knowledge_base.knowledge_base.process_queued_knowledge_bases"
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
    "LLM",
    "LLM Provider",
    {
        "doctype": "Custom Field",
        "filters": [
            ["module", "=", "FinByz AI"]
        ]
    }
]