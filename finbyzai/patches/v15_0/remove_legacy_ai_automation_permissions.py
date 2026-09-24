import frappe

ROLE = "AI Automation"
ALLOWED_DOCTYPES = (
    "AI Agent",
    "AI Tool",
    "Knowledge Base",
    "LLM",
    "LLM Provider",
)


def execute() -> None:
    affected_doctypes = set(
        frappe.get_all(
            "Custom DocPerm",
            filters={"role": ROLE},
            pluck="parent",
        )
    )
    affected_doctypes.update(
        frappe.get_all(
            "DocPerm",
            filters={
                "role": ROLE,
                "parent": ["not in", ALLOWED_DOCTYPES],
            },
            pluck="parent",
        )
    )

    frappe.db.delete("Custom DocPerm", {"role": ROLE})
    frappe.db.delete(
        "DocPerm",
        {
            "role": ROLE,
            "parent": ["not in", ALLOWED_DOCTYPES],
        },
    )

    for doctype in affected_doctypes:
        frappe.clear_cache(doctype=doctype)
