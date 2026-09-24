import frappe

ROLE = "AI Automation"


def execute() -> None:
    if frappe.db.exists("Role", ROLE):
        return

    frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": ROLE,
            "desk_access": 1,
        }
    ).insert(ignore_permissions=True)
