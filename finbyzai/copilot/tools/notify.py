# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Sending mail — the answer to "can you email this to me".

One tool, `send_email`, registered with confirm=True like every other write: the user
sees the exact recipients, subject and body and approves it before anything leaves
the site. It goes through `frappe.sendmail`, the same primitive
`AIDigestSchedule.build_and_send` already uses for the scheduled briefing, so a
message sent from the chat and a message sent by the digest go out through identical
plumbing and land in the same Email Queue.
"""

import frappe

from finbyzai.copilot.registry import tool

MAX_RECIPIENTS = 20

# What "email me this" resolves to — the signed-in user's own address, and only that.
# A model that could send to any name it heard in the conversation would be a data
# exfiltration path; this alias can never become anyone else's inbox.
SELF_ALIASES = {"me", "myself", "my email", "my own email", "my email address"}


@tool(
    "send_email",
    """Send a real email through this site's own mail settings. `to` and the optional
    `cc` are lists of addresses — write "me" for any of them to mean the signed-in
    user's own address, rather than asking them what it is. `body` is markdown; it
    renders as a normal email, the way your other answers render as text.

    The user is shown the exact recipients, subject and body and approves it before
    anything is sent — treat that approval as the send, and never tell the user a
    message went out before they have answered it.

    Link the email to a record the user is discussing by passing `reference_doctype`
    and `reference_name` (e.g. a Sales Invoice) — it will show up on that record's own
    timeline in the desk, not only in the recipient's inbox.""",
    confirm=True,
    tags=["write"],
    label="Sending Email",
)
def send_email(
    to: list,
    subject: str,
    body: str,
    cc: list | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
) -> dict:
    recipients = _resolve(to, "to")
    cc_list = _resolve(cc or [], "cc")
    if not recipients:
        raise frappe.ValidationError("`to` needs at least one recipient.")
    if len(recipients) + len(cc_list) > MAX_RECIPIENTS:
        raise frappe.ValidationError(
            f"{len(recipients) + len(cc_list)} recipients is more than one email should "
            f"go to at once (limit {MAX_RECIPIENTS}). Send it in smaller batches."
        )
    if not (body or "").strip():
        raise frappe.ValidationError("`body` is required — what should the email say?")

    reference_doctype, reference_name = _resolved_reference(reference_doctype, reference_name)

    queue = frappe.sendmail(
        recipients=recipients,
        cc=cc_list or None,
        subject=(subject or "").strip() or "Message from Copilot",
        message=body,
        as_markdown=True,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        now=True,
    )
    # frappe.sendmail can process a call with no error and still queue nothing: every
    # address it was given may have unsubscribed from mail on this site (this bench's
    # own seed data does exactly that to admin@example.com), and it says so by
    # returning a falsy queue rather than raising. Reporting "queued": True regardless
    # would be exactly the wrong-answer-presented-as-fact this app exists to avoid, so
    # the absence of a real queue entry is treated as the failure it is.
    if not queue:
        raise frappe.ValidationError(
            "No email was actually queued. Every address in `to`/`cc` may have "
            "unsubscribed from mail on this site — ask for a different address."
        )

    return {
        "to": recipients,
        "cc": cc_list,
        "subject": subject,
        "queued": True,
        "queue": queue.name,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
    }


def _resolve(addresses, field: str) -> list:
    """Expand "me"/"myself" to the signed-in user's address, then check every
    address is real. One bad entry should say which, not fail the whole list with
    a Python traceback the model cannot act on."""
    out = []
    for raw in addresses or []:
        text = str(raw or "").strip()
        if text.lower() in SELF_ALIASES:
            text = frappe.db.get_value("User", frappe.session.user, "email")
            if not text:
                raise frappe.ValidationError(
                    f"Your account ({frappe.session.user}) has no email address on file, "
                    f"so `{field}` cannot resolve \"{raw}\". Ask for a real address instead."
                )
        try:
            frappe.utils.validate_email_address(text, throw=True)
        except frappe.InvalidEmailAddressError:
            raise frappe.ValidationError(f"`{field}` contains {raw!r}, which is not a valid email address.")
        if text not in out:
            out.append(text)
    return out


def _resolved_reference(doctype: str | None, name: str | None):
    """Only link the email to a record the user can actually see — the tool must not
    become a way to attach an email to a document you have no read access to."""
    if not doctype or not name:
        return None, None
    if not frappe.db.exists(doctype, name):
        raise frappe.ValidationError(f"{doctype} {name!r} does not exist.")
    if not frappe.has_permission(doctype, "read", doc=name):
        raise frappe.PermissionError(f"No permission to read {doctype} {name!r}.")
    return doctype, name
