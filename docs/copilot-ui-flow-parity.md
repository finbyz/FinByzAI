# Copilot panel — Flow parity spec

Goal: the panel should be indistinguishable from Frappe Flow's, minus the branding.
This spec is written from Flow's *rendered behaviour*, measured in the running app.

**Do not copy Flow's source.** Flow is AGPL-3.0 (`flow/pyproject.toml: license =
"AGPL-3.0-or-later"`). Copying its components into finbyzai would put this app's
combined source under AGPL obligations the moment it is served to a client. Matching
a layout, a label, a spacing scale is fine; pasting the file is not. Everything below
is a description to build from.

## 1. Panel shell

- `position: fixed`, anchored right, full height, `z-index` above the desk.
- Default width **420px**, min **360px**, max `window.innerWidth - 80`. Persist width,
  open state and the active conversation across reloads (localStorage).
- A **6px** drag handle on the left edge resizes it. Drop the width transition while
  dragging so it tracks the cursor exactly.
- Fullscreen toggle expands to `100vw` and restores the previous half-width.
- Shell: `border-left`, white/surface background, ink-gray-9 text. No shadow, no
  rounded corners, no floating gap — it is flush to the viewport edge.
- Font: `"Inter", "InterVariable", ui-sans-serif, system-ui`. Type scale as CSS vars:
  `--text-xs: 13px; --text-sm: 14px; --text-base: 15px; --text-lg: 17px`.

## 2. Header

One row, `border-bottom`, `px-3 py-2`, `gap-1.5`:

    [brand mark 18px] [Copilot]            [conversations ▾] [+] [maximize] [×]

- Title is `text-sm font-medium`. Ghost icon buttons, `3.5` icons, `stroke-width 2`.
- The `×` tooltip names the shortcut: "Close (Ctrl+Shift+K)".
- Conversation switcher is a **dropdown** in Flow. We keep the sidebar as well — see §7.

## 3. Message list

- Scroll container: `flex-1 overflow-y-auto px-5 pt-4`, bottom padding
  `calc(composer-height + 24px)` so the last message clears the floating composer.
- Inner column: `mx-auto w-full max-w-3xl`, messages separated by `gap-5` (20px).
- **User message**: right-aligned bubble, `max-width 88%`, `rounded-2xl`,
  `bg-surface-gray-2`, `px-3.5 py-2.5`, `whitespace-pre-wrap`, base text size.
  Attachment chips sit above it, right-aligned.
- **Assistant message**: no bubble, no avatar — plain text on the panel background,
  full width. Markdown rendered. This asymmetry is most of Flow's look.
- Vertical rhythm inside an assistant turn: 10px between blocks; ~22px above a
  text blob that follows a card, tightened by the text's own half-leading
  (`calc(22px - 0.36em)`). Text-to-text is `calc(16px - 0.72em)`.

## 4. Activity lines — the part in the screenshot

Consecutive tool calls inside one assistant turn collapse into **one clickable line**,
not a list of cards. That line's text changes as the turn progresses:

| State | Line reads |
|---|---|
| a tool is running | that tool's label, e.g. `Reading DocType Meta` |
| finished, one call | that call's label |
| finished, N>1 calls, and text or an approval follows | `Ran 3 steps` |

- Label styling: `text-sm`, `text-ink-gray-5` when closed, `font-medium text-ink-gray-8`
  when open, `hover:text-ink-gray-7` when expandable.
- A muted context suffix distinguishes steps where the backend sends `context`
  (doctype / report / action).
- Failure or resolved approval appends a muted `· Failed`, `· Denied`,
  `· Changes requested` at `text-xs`.
- Trailing `chevron-right`, `3.5`, rotates 90° when open. No chevron when there is
  nothing to expand.
- **While the turn is live and unsealed the label shimmers** — a highlight sweeping
  across the text — and the label **cross-fades** when it changes, with the container
  animating its width so the chevron glides rather than jumps. This animation is what
  makes Flow feel alive; do not skip it.
- Expanded: a bordered card (`rounded-lg border border-outline-gray-1 px-3 py-2.5`,
  `mt-1.5 mb-2`). One call shows its arguments; several show a connected numbered
  timeline, one row per step. Reveal is a 0.15s opacity fade.

### Argument rendering inside an expanded step

Values are typed, not dumped as JSON:

- scalar → inline `key  value`; empty/null/`""` → `—`; booleans → `Yes` / `No`
- a Frappe filter tuple `["like", "%x%"]` / `["in", [...]]` → rendered as an operator
  pair, not an array
- list of scalars → chips
- list of objects → a compact record list, titled by the first title-ish key
  (`title`, `name`, `subject`, `label`, `slug`), else the first non-empty scalar field
- multi-line, or a single line over **120 chars** → full-width code block
- `execute`'s `code` is **always** a code block, even one short line
- unparseable argument JSON is shown verbatim rather than dropped
- errors render in their own muted error row under the arguments

## 5. Composer

Floating card, not docked: `absolute inset-x-5 bottom-3.5 mx-auto max-w-3xl`,
`rounded-xl border bg-surface-white px-2.5 py-2 shadow-sm`. Border brightens on
focus-within; the whole card is a drag-and-drop target and turns
`border-outline-gray-4 bg-surface-gray-1` while a file is over it.

    ┌──────────────────────────────────────────┐
    │ [attachment chips, if any]               │
    │ Ask Copilot…                             │
    │ 📎   Copilot / gpt-4.1-mini ▾        (↑) │
    └──────────────────────────────────────────┘

- Textarea: `rows=1`, auto-grows, `min-height 50px`, `max-height 160px`, no border,
  transparent, base size, `placeholder:text-ink-gray-4`. Placeholder is
  **`Ask {agent}…`** — for us, `Ask Copilot…`.
- Bottom row, `h-6` controls, `gap-1.5`: paperclip icon button → agent name (medium
  ink-gray-8) → a muted `/` separator → model name with `chevron-down` → spacer →
  send. Both pickers are `text-[12.5px]`, searchable dropdowns.
- Send is a solid round icon button with `arrow-up`, disabled until there is input.
- While running it is replaced by a **red stop button** with a small square glyph.
- Enter sends, Shift+Enter newlines. Both pickers lock while a turn is running.

## 6. Empty state

Brand mark, one line of copy, and a few one-click example prompts. Ours:
"Top 10 customers by sales this year", "Items not sold in 60–90 days",
"Outstanding receivables for a customer", "Run the Stock Ageing report".

## 7. Sidebar (ours, not Flow's)

Flow uses a header dropdown; we keep the sidebar already built, restyled to these
tokens: 240px, collapsible to an icon rail, grouped Today / Yesterday / Earlier,
rename and delete on hover, search past ~10 conversations, overlay under 640px panel
width, state in localStorage. Keep the header dropdown too so both routes work.

## 8. What the backend now sends for all of this

`tool_started` and `tool_ended` carry `label` and `context`, so a live run and a
reloaded conversation always show the same line:

    {"type": "tool_started", "id", "name", "label": "Reading DocType Meta",
     "context": "Sales Invoice", "arguments": {...}}

Labels, from `registry.label_for()`:

    find_doctypes        Finding relevant DocTypes
    describe             Reading DocType Meta
    read                 Reading DocType Records
    aggregate            Grouping Records
    count                Counting Records
    list_reports         Finding Reports
    describe_report      Reading Report Filters
    run_report           Running Report
    create               Creating Records
    update               Updating Records
    run_action           Running Document Actions
    delete               Deleting Records
    execute              Executing
    run_query            Running Query
    extract_file_content Reading Attachment

Use `label` when present and fall back to humanizing `name`, so a tool added later
still renders. `approval_required.summary` is now phrased Flow-style —
`Create 1 ToDo record`, `Delete 3 Sales Invoice records`,
`Run "Submit" on 2 Sales Invoice` — and `delete` should be styled as destructive.

## 9. Definition of done

Open Flow (Ctrl+I) and the Copilot (Ctrl+Shift+K) side by side at the same width.
Ignoring brand name and colour of the mark, these must match: panel width and edge
treatment, header row, user bubble shape and inset, assistant text with no bubble,
the collapsed activity line with its shimmer and chevron, the expanded step card,
the floating composer with its `📎 agent / model ▾ (↑)` row, and the empty state.
Screenshot both at 420px and 850px and compare.
