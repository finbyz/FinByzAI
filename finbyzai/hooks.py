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

# Frappe resolves these bundle names to content-hashed assets.
app_include_js = ["finbyzai_copilot.bundle.js"]
app_include_css = ["finbyzai_copilot.bundle.css"]

after_migrate = [
	"finbyzai.install.after_migrate",
	"finbyzai.copilot.setup.ensure_defaults",
]
