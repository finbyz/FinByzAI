# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Sending mail — the answer to "can you email this to me".

One tool, `send_email`, registered with confirm=True like every other write: the user
sees the exact recipients, subject and body and approves it before anything leaves
the site. It goes through `frappe.sendmail`, the same primitive
`AIDigestSchedule.build_and_send` already uses for the scheduled briefing, so a
message sent from the chat and a message sent by the digest go out through identical
plumbing and land in the same Email Queue.

`attach_from_call` is the same idea `visualize` is built on: the model writes the
words, the data comes back out of a call it actually made. Asked to "email the table
too", a model can only retype what it already summarized — the three rows it chose
to mention, not the other twenty-one — so the full table goes out as a CSV read back
from the tool call's own persisted result instead.
"""

import csv
import io

import frappe

from finbyzai.copilot.registry import tool
from finbyzai.copilot.tools.visualize import rows_from_call

MAX_RECIPIENTS = 20
MAX_ATTACHMENT_ROWS = 5000

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
    timeline in the desk, not only in the recipient's inbox.

    `attach_from_call` attaches the *complete* table behind an earlier tool call as a
    CSV — every row it produced, not the handful you mentioned in `body`. Use it
    whenever the user asks for "the table too" / "the full report" / "the data, not
    just the summary": write "last" for the most recent call that produced a table,
    or the exact id of an earlier one. Leave it out entirely for no attachment. You
    cannot attach numbers you typed yourself — only a real call's own result.""",
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
    attach_from_call: str | None = None,
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
    attachment = _csv_attachment(attach_from_call) if attach_from_call else None

    queue = frappe.sendmail(
        recipients=recipients,
        cc=cc_list or None,
        subject=(subject or "").strip() or "Message from Copilot",
        message=body,
        as_markdown=True,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        attachments=[attachment] if attachment else None,
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
        "attachment": attachment["fname"] if attachment else None,
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


def _csv_attachment(call_id: str) -> dict:
    """The full rows an earlier tool call produced, as a CSV attachment — not the
    sample the model saw, the same persisted result `visualize` draws its own
    second view from. Refuses to fabricate a spreadsheet out of anything the model
    could have invented: no rows, or rows that aren't flat records, is an error the
    model can read and act on, not a blank or malformed attachment.
    """
    rows, source = rows_from_call(call_id)
    if not rows:
        raise frappe.ValidationError(
            f"Call {call_id!r} produced no rows to attach. Fetch the data first, "
            "then email it."
        )
    if not all(isinstance(row, dict) for row in rows):
        raise frappe.ValidationError(
            f"Call {call_id!r} did not return a table — nothing to attach as a CSV."
        )
    truncated = len(rows) > MAX_ATTACHMENT_ROWS
    rows = rows[:MAX_ATTACHMENT_ROWS]

    # One column order for every row: every key, first seen first — matches the
    # panel's own table so the attachment reads in the same order the user saw it.
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # A nested value (a dict/list inside a cell) has no cell to live in; written
        # as JSON text rather than dropped, so the column still lines up across rows.
        writer.writerow(
            {k: frappe.as_json(v) if isinstance(v, dict | list) else v for k, v in row.items()}
        )

    # The same title the table already carries in the panel (see blocks.table's
    # `title` and the doctype fallback) — "Reorder Recommendations.csv" rather than
    # a filename built out of the call's own internal id.
    label = (source or {}).get("title") or (source or {}).get("doctype") or "table"
    slug = frappe.scrub(label).replace("_", "-")
    name = f"{slug}{'-truncated' if truncated else ''}.csv"
    return {"fname": name, "fcontent": buffer.getvalue().encode("utf-8")}


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
