# finbyzai copilot

Conversational agent for Frappe/ERPNext: answers data questions, runs existing
reports, creates and updates records behind an approval gate, reads attachments,
and returns UI blocks the chat panel renders with frappe-ui.

    guard.py       every tool call: savepoint + errors returned, never raised   [done]
    blocks.py      kpi / table / line / bar / records contract for the panel    [done]
    registry.py    load and sync AI Tools, schemas, confirmation flags
    ai_tools/      one complete implementation per AI Tool record
    access.py      permission checks shared by AI Tools
    sandbox.py     restricted Python namespace and permission-checked reads
    runner.py      agent loop in an RQ worker, socketio events, approval pause  [done]
    api.py         whitelisted endpoints for the panel                          [done]
    doctype/       Copilot Conversation / Message / Run / Settings              [done]

Design notes worth keeping in mind before changing anything here:

- A tool error is a *result*, not an exception. That is the only reason the agent can
  correct its own mistakes. See guard.py.
- Reports before SQL. 222 Report records already exist on this site and are tested
  and permission-aware; rediscovering their logic in generated code is how you get
  wrong numbers.
- `frappe.get_list`, never `frappe.get_all`. get_all ignores permissions.
- Rows in a block come from a tool result. A model asked to retype numbers invents them.

Verified on nayla_innovations.finbyz.com (2026-09-10): all 8 read tools return real
data as Administrator and are blocked with PermissionError as Guest; `run_report`
with no filters returns {"fields": ["company", "to_date"]} instead of a KeyError.

Sandbox escape attempts, all blocked and re-tested after every change to sandbox.py:
import os, __import__, open(), eval(), frappe.get_all, frappe.db.sql,
frappe.db.set_value, doc.save() via get_doc, ().__class__.__bases__ dunder walking,
SQL update / delete / drop / two-statements, and all three read paths as Guest.

Write tools verified the same way (all rolled back afterwards): partial batches keep
their good rows and report the bad one per-row; unknown fieldnames are refused rather
than silently dropped by doc.update(); submitted records refuse deletion; schema
doctypes need developer_mode AND copilot_allow_schema_changes; Guest is refused on
create and update. File extraction read a real 28 KB PDF (2,673 chars) and a customer
xlsx (truncated at 20k with a hint) from this site, and refused Guest.

Runner verified with a scripted model (no API key needed, deterministic):
read question -> run_started/text/tool_started/tool_ended/text/done, Completed in 2
iterations; a write pauses with pending_call + approval_required and only touches the
database after approve_run; a rejection reaches the model as retryable:false with the
user's reason; two identical tool failures stop the loop instead of burning the
iteration budget; a second turn on a busy conversation is refused; Guest cannot read
another user's conversation.

Not yet verified against a real LLM: the OpenRouter key shared by finbyzai and flow is
over its weekly limit, and the OpenAI/Google keys on this site are placeholders.
