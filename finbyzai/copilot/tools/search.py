# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""One tool, two places to look.

`scope="internal"` is Frappe's own global search — every DocType a word could be
in, not one you have to guess first. It already checks permission twice over
(`frappe.utils.global_search.search`: doctype-level, then a per-record
`has_permission()` on each hit) and runs as whichever user is having this
conversation, never as Administrator, so a result is exactly what that person
could have found themselves by typing into the desk's own search bar.

`scope="external"` is the open web — for "what's the news on X" and anything
this ERP would never contain. It rides on the OpenRouter key this site already
has configured for chat models, using OpenRouter's own web-search plugin
(backed by Exa) rather than a second API key and a second bill to manage. Each
call is a small but real charge against that account — see EXTERNAL_MODEL and
the module docstring below before raising EXTERNAL_RESULTS.
"""

import frappe
import requests

from finbyzai.copilot import blocks
from finbyzai.copilot.registry import tool

INTERNAL_LIMIT = 20
EXTERNAL_RESULTS = 5
EXTERNAL_TIMEOUT = 20
SNIPPET_WIDTH = 220

# A fixed model for the external call, independent of whatever the conversation's
# own model is set to — search results read the same regardless of which model
# assembled the request, and the free tier keeps this at just the search
# backend's own fee (~$0.007/call via Exa, OpenRouter's default web-search
# backend, as of writing) rather than also paying for a paid completion.
EXTERNAL_MODEL = "cohere/north-mini-code:free"
EXTERNAL_PROVIDER = "OpenRouter"


@tool(
    "search",
    """Search for something, on this site or on the open web.

    scope="internal" (default): every DocType you have permission to read on this
    site, not one doctype at a time — use it to find a record when you do not know
    which DocType holds it, or to find something by a word inside it rather than
    its exact name or code.

    scope="external": the open web. Use it only for something genuinely outside
    this ERP — news, a current fact, general knowledge — never as a substitute
    for reading this site's own data. Costs a small real amount per call, so
    don't call it speculatively.

    scope="both" runs both and returns each separately. `doctype` narrows an
    internal search to one DocType.""",
    tags=["read"],
    label="Searching",
)
def search(query: str, scope: str = "internal", doctype: str | None = None, limit: int = 10) -> dict:
    scope = (scope or "internal").strip().lower()
    if scope not in ("internal", "external", "both"):
        raise frappe.ValidationError('`scope` must be "internal", "external" or "both".')
    if not (query or "").strip():
        raise frappe.ValidationError("`query` is required.")

    payload = {"query": query, "scope": scope}
    ui_blocks = []

    if scope in ("internal", "both"):
        rows = _internal(query, doctype, limit)
        payload["internal_results"] = rows
        payload["internal_count"] = len(rows)
        if rows:
            ui_blocks.append(blocks.table(rows, title=f'Search results: "{query}"'))

    if scope in ("external", "both"):
        payload["external"] = _external(query)

    return blocks.attach(payload, *ui_blocks)


def _internal(query: str, doctype: str | None, limit: int) -> list:
    """Frappe's own `global_search.search()` does this, but its row-enrichment
    step crashes outright (`UnboundLocalError`, not caught) the moment the index
    holds one stale entry — a renamed or deleted record whose old search-index
    row was never cleaned up. Found live on this site: a "Sarkeez" Customer that
    no longer exists. Rather than depend on a call that a site's own ordinary
    data can take down, this reimplements the same two permission checks
    directly — doctype-level (`get_can_read`) and row-level (`has_permission`) —
    and simply skips a hit that no longer resolves to a real record.
    """
    from frappe.desk.doctype.global_search_settings.global_search_settings import (
        get_doctypes_for_global_search,
    )
    from frappe.query_builder.functions import Match

    limit = max(1, min(int(limit or 10), INTERNAL_LIMIT))
    allowed = set(get_doctypes_for_global_search()) & set(frappe.get_user().get_can_read())
    if doctype:
        allowed &= {doctype}
    if not allowed:
        return []

    table = frappe.qb.Table("__global_search")
    rank = Match(table.content).Against(query)
    # Overfetch: some hits will be stale index rows or fail the row-level check
    # and get skipped below, so asking for exactly `limit` would under-fill.
    candidates = (
        frappe.qb.from_(table)
        .select(table.doctype, table.name, table.content, rank.as_("rank"))
        .where(rank)
        .where(table.doctype.isin(list(allowed)))
        .orderby("rank", order=frappe.qb.desc)
        .limit(limit * 3)
        .run(as_dict=True)
    )

    out = []
    for row in candidates:
        if len(out) >= limit:
            break
        if not frappe.db.exists(row.doctype, row.name):
            continue  # a stale index entry — renamed or deleted since it was indexed
        if not frappe.has_permission(row.doctype, "read", doc=row.name):
            continue  # allowed at the doctype level, refused for this specific record
        out.append(
            {
                "doctype": row.doctype,
                "name": row.name,
                "title": _title_of(row.doctype, row.name),
                "snippet": _snippet(row.content, query),
            }
        )
    return out


def _title_of(doctype: str, name: str) -> str:
    try:
        title_field = frappe.get_meta(doctype).title_field
        return (title_field and frappe.db.get_value(doctype, name, title_field)) or name
    except Exception:
        return name


def _snippet(content: str, query: str, width: int = SNIPPET_WIDTH) -> str:
    """The bit of the matched record around the first query word, not the whole
    (often very long) indexed blob `__global_search` stores."""
    text = (content or "").replace("\n", " ").replace("\t", " ")
    first_word = (query or "").split()[0].lower() if query else ""
    idx = text.lower().find(first_word) if first_word else -1
    if idx < 0:
        return text[:width].strip()
    start = max(0, idx - width // 3)
    return text[start : start + width].strip()


def _external(query: str) -> dict:
    """One call to OpenRouter's web-search plugin: a model's own answer, grounded
    in real, current pages, plus the pages themselves as `sources` — read those
    when answering, the way any other tool's rows are the ground truth and the
    model's own prose is only ever an interpretation of them.

    Gated on a site-level setting, not a per-call approval. A search here sends
    the query to a third party and costs a small real amount — a decision an
    admin makes once in Copilot Settings, not one worth an Approve/Reject card
    on every curious question, which is friction enough that it either trains
    the user to click through without reading or talks them out of the whole
    feature. Off until turned on.
    """
    from finbyzai.copilot.runner import get_settings

    if not get_settings().enable_external_search:
        raise frappe.PermissionError(
            "External search is turned off. An administrator can enable it in "
            "Copilot Settings — it sends the query to a third party and costs a "
            "small real amount per search, so it is off until someone decides that "
            "trade-off deliberately."
        )

    api_key = frappe.get_doc("LLM Provider", EXTERNAL_PROVIDER).get_password("api_key")
    if not api_key:
        raise frappe.ValidationError(
            f"No API key configured on the {EXTERNAL_PROVIDER} LLM Provider — "
            "external search needs one."
        )

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": EXTERNAL_MODEL,
                "messages": [{"role": "user", "content": query}],
                "plugins": [{"id": "web", "max_results": EXTERNAL_RESULTS}],
            },
            timeout=EXTERNAL_TIMEOUT,
        )
    except requests.RequestException as e:
        raise frappe.ValidationError(f"External search could not reach OpenRouter: {e}")

    if response.status_code != 200:
        raise frappe.ValidationError(
            f"External search failed ({response.status_code}): {response.text[:300]}"
        )

    message = ((response.json().get("choices") or [{}])[0].get("message")) or {}
    sources = [
        {
            "title": a["url_citation"].get("title"),
            "url": a["url_citation"].get("url"),
            "snippet": (a["url_citation"].get("content") or "")[:400],
        }
        for a in message.get("annotations") or []
        if a.get("type") == "url_citation" and a.get("url_citation")
    ]
    return {"answer": message.get("content") or "", "sources": sources}
