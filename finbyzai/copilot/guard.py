# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Tool-call guard — the single reason the agent can recover from its own mistakes.

Every tool the agent may call goes through `run_guarded`. It does three things:

1. Runs the tool inside a database savepoint, so a failed call leaves no partial
   writes behind while work that already succeeded earlier in the run survives.
2. Never lets an exception escape. A failure comes back as a plain dict, which the
   runner hands to the model as the tool *result* — so the loop continues and the
   model can read what went wrong and retry with corrected arguments, instead of
   the run dying and dumping a traceback in the chat.
3. Normalizes Frappe's exceptions into something a model can act on: the real
   message (Frappe often puts it only in `frappe.local.message_log`, not in
   `str(e)`), the missing mandatory fieldnames where we can name them, and a
   short hint per error class.

Result shape, always:

    {"ok": True,  "result": <tool return value>}
    {"ok": False, "error": str, "error_type": str, "retryable": bool,
     "fields": [str], "messages": [str], "hint": str}     # last three optional
"""

import re

import frappe
from frappe.utils import strip_html

ERROR_LIMIT = 800
MESSAGE_LIMIT = 5
SAVEPOINT = "copilot_tool"

# What the model should do next, per exception class. These are read by the LLM,
# so they are written as instructions, not as descriptions.
HINTS = {
    "MandatoryError": (
        "Required fields were missing. Add every field listed in `fields` and call the tool again."
    ),
    "LinkValidationError": (
        "A linked record does not exist. Look up a valid name with `read` or `find_doctypes`, then retry."
    ),
    "DoesNotExistError": (
        "That DocType or record does not exist. Confirm the exact name with `find_doctypes` or `read` first."
    ),
    "DuplicateEntryError": (
        "A record with that name already exists. Read the existing record instead of creating it again."
    ),
    "UniqueValidationError": (
        "That value must be unique and is already taken. Read the existing record instead of creating a duplicate."
    ),
    "TimestampMismatchError": (
        "The record changed after you read it. Read it again, then retry the update with fresh values."
    ),
    "PermissionError": (
        "The current user is not allowed to do this. Do not retry — tell the user which permission is missing."
    ),
    "ValidationError": (
        "A server-side validation rejected the values. Read `error` for the rule that failed, fix the values, then retry."
    ),
}

# Retrying these cannot help: the answer will be the same every time.
NO_RETRY = ("PermissionError", "PermissionDenied", "AuthenticationError")

# `[Sales Invoice, new-sales-invoice-1]: customer, company` — see
# frappe/model/document.py::_validate_mandatory
_MANDATORY_RE = re.compile(r"^\[[^\]]*\]:\s*(.+)$")


def run_guarded(name, fn, kwargs=None):
    """Call `fn(**kwargs)` as tool `name`, returning a result dict that never raises.

    Wrapped in a savepoint: on failure the call's own writes are rolled back and
    nothing else is. On success nothing is committed here — the runner owns the
    transaction boundary for the whole turn.
    """
    kwargs = kwargs or {}
    savepoint = f"{SAVEPOINT}_{frappe.generate_hash(length=8)}"
    _reset_messages()

    frappe.db.savepoint(savepoint)
    try:
        result = fn(**kwargs)
    except Exception as e:
        # A tool that committed before it failed has already destroyed the savepoint,
        # and MySQL answers the rollback with 1305 "SAVEPOINT does not exist". That
        # exception must not replace the tool's own error: the contract here is that
        # this function never raises, and the error the model needs to read is `e`.
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        payload = normalize_exception(e)
        payload["tool"] = name
        return payload

    return {"ok": True, "result": result}


def normalize_exception(e):
    """Turn a Frappe/Python exception into an actionable dict for the model."""
    kind = type(e).__name__
    messages = _collected_messages()
    direct = strip_html(str(e) or "").strip()
    message = direct or (messages[0] if messages else kind)

    payload = {
        "ok": False,
        "error": message[:ERROR_LIMIT],
        "error_type": kind,
        "retryable": kind not in NO_RETRY,
    }

    extra = [m for m in messages if m != message][:MESSAGE_LIMIT]
    if extra:
        payload["messages"] = [m[:ERROR_LIMIT] for m in extra]

    fields = _missing_mandatory_fields(e, kind)
    if fields:
        payload["fields"] = fields

    hint = _hint_for(kind, e)
    if hint:
        payload["hint"] = hint

    return payload


def _hint_for(kind, e):
    """Exact class first, then the nearest base class we have advice for."""
    if kind in HINTS:
        return HINTS[kind]
    for base in type(e).__mro__[1:]:
        if base.__name__ in HINTS:
            return HINTS[base.__name__]
    return None


def _missing_mandatory_fields(e, kind):
    """Pull the fieldnames out of a MandatoryError so the model doesn't have to guess."""
    if kind != "MandatoryError":
        return []
    match = _MANDATORY_RE.match(str(e).strip())
    if not match:
        return []
    return [f.strip() for f in match.group(1).split(",") if f.strip()]


def _reset_messages():
    """Drop anything already in the message log so we only read this call's messages."""
    frappe.local.message_log = []


def _collected_messages():
    """Frappe's msgprint/throw messages for the call that just ran, as plain text."""
    out = []
    for entry in frappe.local.message_log or []:
        raw = entry.get("message", "") if isinstance(entry, dict) else str(entry)
        text = strip_html(str(raw)).strip()
        if text and text not in out:
            out.append(text)
    return out
