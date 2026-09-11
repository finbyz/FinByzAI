# UI build prompt — finbyzai Copilot chat panel

Give this file to the LLM that will write the frontend. The backend contract below is
fixed; build against it exactly. Do not change event names or block shapes.

## What to build

A chat panel for a Frappe v16 desk, opened with a keyboard shortcut and as a slide-over
from the right (about 420px wide, full height, resizable). It must look native to the
Frappe desk — use **frappe-ui** components and Frappe's own CSS variables for colour,
radius and spacing. No custom brand colours, no other component library, no rounded
"AI product" styling. If a Frappe desk user cannot tell it was added by a third-party
app, the styling is right.

## Where the code goes

    apps/finbyzai/frontend/                 # new Vite + Vue 3 + frappe-ui project
      src/main.js
      src/App.vue
      src/components/...
      src/lib/socket.js
      src/lib/api.js
    apps/finbyzai/finbyzai/public/copilot/  # vite build output (js + css)

Build config: Vite with base `/assets/finbyzai/copilot/`, output to
`finbyzai/public/copilot/`, and `postcss-prefix-selector` so the panel's CSS cannot
leak into the desk. Mount into a container the desk injects; do not take over `#app`.
Add `yarn build` under `apps/finbyzai/frontend`. Use `frappe-ui` 1.0.0-beta.3 or newer,
Tailwind only if frappe-ui needs it, and prefix every utility class.

## Backend it talks to

All endpoints are `POST /api/method/finbyzai.copilot.api.<name>` with
`X-Frappe-CSRF-Token: frappe.csrf_token`.

    start_run({input, conversation?, agent?, model?, attachments?})  -> {run, conversation}
    approve_run({run, call_id, decision, values?})                   -> {status}   decision: "approve" | "reject"
    answer_question({run, call_id, answer})                          -> {status}
    stop_run({run})                                                  -> {status}
    get_conversation({conversation})                                 -> {messages, blocks}
    list_conversations({limit})                                      -> [{name, title, modified}]

Attachments upload through Frappe's normal file API (`/api/method/upload_file`), then
pass the returned File names in `attachments`.

**Streaming is socketio, not SSE.** After `start_run` returns, subscribe to the channel
`copilot:<run>` using the desk's existing socket (`frappe.socketio` / `frappe.realtime`)
and render events as they arrive:

    {"type": "run_started",       "run", "conversation"}
    {"type": "text",              "delta"}                      append to the current bubble, markdown
    {"type": "tool_started",      "id", "name", "arguments"}    collapsed activity row
    {"type": "tool_ended",        "id", "name", "ok", "result"|"error"}
    {"type": "block",             "block": {...}}               render, see below
    {"type": "approval_required", "id", "name", "arguments", "summary"}
    {"type": "question",          "id", "text", "options"}
    {"type": "error",             "message"}
    {"type": "done",              "status", "output"}

The run keeps going if the tab closes. On reopen, call `get_conversation` and rebuild
from `messages` + `blocks`; if the last message has no assistant reply yet, subscribe to
the channel again rather than showing it as failed.

## Blocks to render

    {"type": "kpi",     "label", "value", "delta"?, "unit"?}
    {"type": "table",   "columns": [{"key","label","align"}], "rows": [{...}], "doctype"?}
    {"type": "line",    "x", "series": [{"key","label"}], "rows": [{...}]}
    {"type": "bar",     "x", "series": [{"key","label"}], "rows": [{...}], "horizontal"}
    {"type": "records", "doctype", "rows": [{...}]}

- Use frappe-ui's chart and list components. Numbers formatted with the site's currency
  and number format; no invented precision.
- `table` with a `doctype`, and every `records` row, links to `/app/<doctype>/<name>`.
- Tables scroll horizontally inside the block. The panel itself never scrolls sideways.
- A block belongs to the assistant message it arrived in and must survive reload.

## Approval card — the important one

On `approval_required`, render an inline card in the assistant message:

- Tool name in plain words ("Create 3 Sales Orders", "Submit ACC-SINV-2026-00133").
- The arguments, readable: doctype, record count, and the field/value pairs. Long values
  collapse. JSON is a fallback, not the default view.
- **Approve** and **Reject** buttons, plus a text input for "reject with a reason" that is
  sent as `values.reason`.
- While pending: input disabled, clear "waiting for your approval" state. After the
  answer: the card stays visible showing what was decided, then streaming resumes on the
  same channel.

## Other states that must exist

- **Working indicator** with elapsed time and the current step's label; runs can take
  minutes. Collapse consecutive tool rows into one "Ran N steps" group that expands.
- **Composer**: textarea (Enter sends, Shift+Enter newline), attachment button with
  chips showing filename and size, agent picker, model picker, and a **Stop** button
  while running that calls `stop_run`.
- **Conversation list** in the header: new chat, switch, rename, and a relative
  timestamp per conversation.
- **Empty state** with 4 example prompts, taken from what the tools actually do:
  "Top 10 customers by sales this year", "Items not sold in 60-90 days",
  "Outstanding receivables for <customer>", "Run the Stock Ageing report".
- **Error state**: `error` events render as a plain inline error in the bubble, never a
  toast that can be missed and never a raw traceback.
- Full keyboard use: the shortcut opens with focus in the composer, Esc closes, arrow
  keys move through the conversation list.

## Rules

1. Light and dark both, driven by the desk's theme — no separate toggle.
2. Works at 400px wide (mobile desk) and as a wide panel; nothing with a min-width
   larger than the panel.
3. Never render a number the backend did not send. No client-side aggregation.
4. Never `v-html` model output. Markdown through a sanitizing renderer, code in a
   copyable block.
5. Every list needs a real empty state; every async action a real pending state.

## Addendum — approval cards for `execute` and `run_query`

Two tools ask the user to approve *code they can read*, so the approval card needs a
code view, not a key/value list. Same `approval_required` event, same Approve / Reject
buttons — only the body differs.

    {"type": "approval_required", "id": "...", "name": "execute",
     "arguments": {"code": "rows = frappe.get_list(...)\nresult = ...",
                   "description": "Total invoiced per customer this quarter"},
     "summary": "Run a Python script"}

    {"type": "approval_required", "id": "...", "name": "run_query",
     "arguments": {"sql": "select customer, count(*) as n from `tabSales Invoice` ... LIMIT 500",
                   "description": "Invoice count per customer"},
     "summary": "Run a read-only SQL query"}

Requirements:

- Render `description` as the card's one-line title, and `code` / `sql` in a monospace
  block with syntax highlighting (python / sql), horizontal scroll, and a copy button.
  Long scripts collapse past ~15 lines behind "Show all".
- The user must be able to read every line before approving. Never truncate the code
  with an ellipsis and no way to expand it.
- For `run_query`, show a subdued one-line caution under the SQL: *"Raw SQL is not
  filtered by record-level permissions."* The backend sends the same note in the result.
- After approval, the tool result arrives as a normal `tool_ended` plus a `block`
  (a table). Keep the code visible in the collapsed activity row so the run stays
  auditable after the fact.
- Rejection sends `decision: "reject"` with the optional reason — the agent then
  explains or tries another approach, so the card must stay on screen showing
  "Rejected", with the reason if one was given.
