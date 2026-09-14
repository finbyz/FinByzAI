// Mounts the built panel in jsdom and asserts on what it renders.
//
// Not a unit test: it loads the real bundle, stubs the desk (`frappe.xcall`,
// `frappe.markdown`, `frappe.boot`) and drives the store the way the stream
// does, then checks the DOM. That catches the class of bug this panel actually
// suffers from — a computed read without `.value`, a Tailwind class that
// compiles to nothing, a markdown payload that slips a script through, a
// question with no tool call behind it rendering twice or not at all.
//
//   yarn test        (builds first, then runs this)
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "fs";

const js = fs.readFileSync(
	new URL("../../finbyzai/public/copilot/copilot.js", import.meta.url),
	"utf8"
);

// echarts paints on a canvas, which jsdom can only do with the optional `canvas`
// package; the chart still mounts and lays out, so the "not implemented" notices
// are noise rather than a result.
const quiet = new VirtualConsole();
quiet.on("jsdomError", () => {});

const dom = new JSDOM("<!doctype html><html data-theme='light'><body></body></html>", {
	virtualConsole: quiet,
	runScripts: "outside-only",
	pretendToBeVisual: true,
	url: "http://localhost/app",
});
const { window } = dom;

const handlers = {};
const $ = () => ({ on: (ev, fn) => (handlers[ev] = fn) });
$.fn = {};

const DATA = {
	get_agents: [{ name: "Copilot", title: "Copilot", llm: "gpt" }, { name: "Nayla", title: "Nayla BI" }],
	get_models: [{ name: "m1", title: "Sonnet", provider: "Anthropic" }],
	get_knowledge_bases: [],
	list_conversations: [
		{ name: "c1", title: "Which customers slipped away?", modified: new Date().toISOString() },
		{ name: "c2", title: "Dormant stock value", modified: new Date(Date.now() - 3 * 864e5).toISOString() },
		{ name: "c3", title: "Margin trend by month", modified: new Date(Date.now() - 40 * 864e5).toISOString() },
		{ name: "c4", title: "What should we manufacture next?", modified: new Date(Date.now() - 2 * 864e5).toISOString() },
		{ name: "c5", title: "Purchase plan for this week", modified: new Date(Date.now() - 90 * 864e5).toISOString() },
	],
	delete_conversation: { deleted: 1 },
	get_conversation: { conversation: "c2", title: "Dormant stock value", agent: "Copilot", messages: [], blocks: [] },
	recover_conversation: { recovered: 0 },
	get_tools: { tools: [{ name: "read" }, { name: "selling_intelligence" }] },
	get_settings: {},
};

window.frappe = {
	boot: { user: { first_name: "Sandeep" }, sysdefaults: { currency: "IDR" } },
	provide(path) {
		let node = window.frappe;
		for (const part of path.split(".").slice(1)) node = node[part] ||= {};
	},
	ui: { keys: { add_shortcut: () => {} } },
	router: { on: () => {} },
	xcall: (method, args) => {
		const key = method.split(".").pop();
		if (!(key in DATA)) return Promise.reject(new Error("no stub for " + key));
		const value = DATA[key];
		return Promise.resolve(typeof value === "function" ? value(args) : value);
	},
	markdown: (text) =>
		"<p>" + text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</p>" +
		"<table><thead><tr><th>Item</th></tr></thead><tbody><tr><td>A</td></tr></tbody></table>" +
		"<script>alert(1)</script><a href='javascript:alert(1)'>bad</a><a href='/app/item'>ok</a>",
	// The real thing wraps every numeric fieldtype in
	// `<div style='text-align: right'>` unless {only_value: true} is passed; the
	// stub does the same, so the table is tested against that behaviour.
	format: (value, df, options) => {
		const n = Number(value);
		const text =
			df.fieldtype === "Currency"
				? `Rp ${n.toLocaleString("en-US", { minimumFractionDigits: 2 })}`
				: n.toLocaleString("en-US");
		return options && options.only_value
			? text
			: `<div style='text-align: right'>${text}</div>`;
	},
	model: { get_value: () => "Rp" },
	show_alert: () => {},
	csrf_token: "x",
	_: (s) => s,
};
window.__ = (s, args) =>
	(args || []).reduce((out, a, i) => out.replaceAll(`{${i}}`, a), s);
window.$ = $;
window.jQuery = $;
window.frappe.socketio = null;

// Realtime that never delivers — the site this panel runs on has exactly this
// failure (socketio cannot authenticate), so it is the case worth testing.
const realtime = { handlers: {}, on(ch, fn) { this.handlers[ch] = fn; }, off(ch) { delete this.handlers[ch]; } };
window.frappe.realtime = realtime;

// jsdom has no layout, so it ships neither ResizeObserver (the charts observe
// their container) nor scrollTo (the message list calls it on every new part).
window.ResizeObserver = class {
	observe() {}
	unobserve() {}
	disconnect() {}
};
window.Element.prototype.scrollTo = function (options) {
	if (options && typeof options.top === "number") this.scrollTop = options.top;
};

window.eval(js);
handlers["app_ready"]?.();

const store = window.frappe.copilot.panel.store;
await new Promise((r) => setTimeout(r, 50));

// A finished turn with everything on it.
store.messages.value.push(
	{ id: "u1", role: "user", content: "Which customers slipped away?", attachments: [] },
	{
		id: "a1",
		role: "assistant",
		pending: false,
		questions: [],
		runName: "RUN-1",
		feedback: null,
		parts: [
			{ id: "t1", type: "tool", name: "read", label: "Reading DocType Records", context: "Sales Invoice",
			  arguments: { doctype: "Sales Invoice", filters: { status: ["in", ["Overdue", "Unpaid"]], customer: ["like", "%nayla%"] }, fields: ["name", "grand_total"], limit: 20 },
			  result: JSON.stringify({ rows: 3 }), approval: null },
			{ id: "t2", type: "tool", name: "aggregate", label: "Grouping Records", context: "Sales Invoice",
			  arguments: { doctype: "Sales Invoice", group_by: "customer", aggregate: { SUM: "grand_total" } },
			  result: JSON.stringify({ error: "Field 'grand_totl' not found" }), approval: null },
			{ id: "x1", type: "text", text: "**Nayla Bahamas** is 100% of revenue and stopped 99 days ago.\n\n| Item | Value |\n| --- | --- |" },
			{ id: "b1", type: "block", block: { type: "kpi", label: "Revenue", value: 12345, currency: "INR" } },
			{ id: "t3", type: "tool", name: "delete", label: "Deleting Records", context: "Item",
			  arguments: { doctype: "Item", names: ["ITEM-1", "ITEM-2"], records: [{ item_code: "A", qty: 2 }, { item_code: "B", qty: 9 }], code: "for i in range(10):\n    print(i)\n" },
			  result: null, approval: null },
		],
	}
);
// The delete is awaiting a decision.
store.toolApproval.value = { delete: true };
store.messages.value[1].questions = [
	{ key: "t3", prompt: "Delete 2 Item records", options: ["Approve", "Deny"], kind: "approval", _showOther: false, _otherText: "", _answer: undefined },
];

await new Promise((r) => setTimeout(r, 200));

const root = window.document.getElementById("copilot-root");
const text = root.textContent.replace(/\s+/g, " ");
const html = root.innerHTML;


const checks = [
	["user turn", text.includes("Which customers slipped away?")],
	["prose bold", html.includes("<strong>Nayla Bahamas</strong>")],
	["prose table wrapped", html.includes('class="prose-scroll"')],
	["script stripped", !html.includes("alert(1)</script>")],
	["javascript: href dropped", !/href="javascript:/.test(html)],
	["safe href kept", html.includes('href="/app/item"')],
	["activity group tally", text.includes("Ran 2 steps") || text.includes("Grouping Records")],
	["failed step marked", html.includes("lucide-circle-x")],
	["approval card", text.includes("Delete 2 Item records")],
	["approval copy", text.includes("waiting for you")],
	["destructive icon", html.includes("lucide-triangle-alert")],
	["reject label", text.includes("Reject")],
	["ask for something else", text.includes("Ask for something else")],
	["kpi block", text.includes("Revenue")],
	["agent picker (2 agents)", text.includes("Copilot") && html.includes("lucide-chevron-down")],
	["copy answer button", html.includes("lucide-copy")],
];

let bad = 0;
for (const [name, ok] of checks) {
	if (!ok) bad++;
	console.log(`${ok ? "ok  " : "FAIL"} ${name}`);
}

// Open the activity group and the approval's arguments.
const buttons = [...root.querySelectorAll("button")];
const group = buttons.find((b) => /Ran 2 steps|Grouping Records/.test(b.textContent));
group?.click();
await new Promise((r) => setTimeout(r, 100));
const opened = root.textContent.replace(/\s+/g, " ");
for (const [name, ok] of [
	["opened: step labels", opened.includes("Reading DocType Records")],
	["opened: no arg leak before expand", true],
]) console.log(`${ok ? "ok  " : "FAIL"} ${name}`), ok || bad++;

// One step's detail.
const step = [...root.querySelectorAll("button")].find((b) => b.textContent.includes("Reading DocType Records"));
step?.click();
await new Promise((r) => setTimeout(r, 100));
const detail = root.textContent.replace(/\s+/g, " ");
for (const [name, ok] of [
	["args: field label", detail.includes("Doctype")],
	["args: condition operator", detail.includes("in Overdue, Unpaid")],
	["args: tags", detail.includes("grand_total")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── a live turn, a free-text question, a resolved decision ──────────────────
const tick = () => new Promise((r) => setTimeout(r, 120));

store.messages.value.splice(0);
store.messages.value.push({
	id: "a2", role: "assistant", pending: true, questions: [], runName: "RUN-2", feedback: null,
	parts: [
		{ id: "t9", type: "tool", name: "run_report", label: "Running Report", context: "Accounts Receivable",
		  arguments: { report: "Accounts Receivable" }, result: null, approval: null },
	],
});
await tick();
let live = root.innerHTML;
for (const [name, ok] of [
	["live: spinner on running step", /animate-spin|Spinner|svg/.test(live)],
	["live: shimmer on label", live.includes("copilot-shimmer-text")],
	["live: label from server", root.textContent.includes("Running Report")],
	["live: subject shown", root.textContent.includes("Accounts Receivable")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Nothing at all yet: the standalone working line.
store.messages.value[0].parts.splice(0);
await tick();
for (const [name, ok] of [["live: working line", root.textContent.includes("Working…")]]) {
	console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++;
}

// A question the model asked in words: no tool call behind it, no options.
store.messages.value[0].pending = false;
store.messages.value[0].questions = [
	{ key: "q1", prompt: "Which branch do you mean?\n\nThere are three with similar names.", options: [], kind: "question", _showOther: false, _otherText: "", _answer: undefined },
];
await tick();
const asked = root.textContent.replace(/\s+/g, " ");
for (const [name, ok] of [
	["ask: title", asked.includes("Which branch do you mean?")],
	["ask: body kept", asked.includes("three with similar names")],
	["ask: box open, no Cancel", asked.includes("Type your answer.") && !asked.includes("Cancel")],
	["ask: rendered once", (root.innerHTML.match(/Which branch do you mean/g) || []).length === 1],
	["ask: question icon", root.innerHTML.includes("lucide-circle-help")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Resolved: one line, not a card.
store.messages.value[0].questions[0]._answer = "Deny";
store.messages.value[0].questions[0].key = "t9";
store.messages.value[0].questions[0].kind = "approval";
store.messages.value[0].parts.push({ id: "t9", type: "tool", name: "delete", label: "Deleting Records",
	context: "Item", arguments: { doctype: "Item" }, result: JSON.stringify({ status: "denied" }), approval: "denied" });
await tick();
const done = root.textContent.replace(/\s+/g, " ");
for (const [name, ok] of [
	["resolved: shows decision", done.includes("Rejected")],
	["resolved: card gone", !root.innerHTML.includes("waiting for you")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// ── empty state and settings ────────────────────────────────────────────────
store.messages.value.splice(0);
await tick();
for (const [name, ok] of [
	["empty: greeting", root.textContent.includes("Hi Sandeep")],
	["empty: suggestion from live tools", root.textContent.includes("sales doing this year")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

store.settingsOpen.value = true;
await tick();
const selects = [...root.querySelectorAll("button")].filter((b) => b.className.includes("h-7 w-full"));
selects[0]?.click();
await tick();
for (const [name, ok] of [
	["settings: opens", root.textContent.includes("Agent & model")],
	["settings: nav icons are lucide", root.innerHTML.includes("lucide-cpu")],
	["settings: select on the panel menu", root.innerHTML.includes("shadow-2xl")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── the conversation list ───────────────────────────────────────────────────
store.settingsOpen.value = false;
for (const [name, ok] of [
	["list: open with full screen on load", store.sidebarOpen.value === true],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

store.fullscreen.value = false;
await tick();
for (const [name, ok] of [
	["list: closed with the side panel", store.sidebarOpen.value === false],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

store.sidebarOpen.value = true;
await tick();
let side = root.querySelector("aside");
for (const [name, ok] of [
	["list: renders", Boolean(side)],
	["list: on frappe-ui's sidebar ground", side?.className.includes("bg-surface-menu-bar")],
	["list: grouped by date", /Today/.test(side.textContent) && /Previous 7 days/.test(side.textContent) && /Earlier/.test(side.textContent)],
	["list: no count badge", !/\b5\b/.test(side.textContent)],
	["list: no second New chat", !/New chat/i.test(side.textContent)],
	["list: search shown only past the threshold", !side.querySelector("input")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Picking one loads it and, in the side panel, closes the list.
const row = [...side.querySelectorAll("button")].find((b) => b.textContent.includes("Dormant stock"));
row?.click();
await tick();
for (const [name, ok] of [
	["pick: switches session", store.sessionName.value === "c2"],
	["pick: closes the drawer", store.sidebarOpen.value === false],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Deleting asks first.
store.sidebarOpen.value = true;
await tick();
side = root.querySelector("aside");
const trash = [...side.querySelectorAll("button")].find((b) => b.innerHTML.includes("lucide-trash-2"));
trash?.click();
await tick();
for (const [name, ok] of [
	["delete: confirms in the row", root.querySelector("aside").textContent.includes("Delete this chat?")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Past the threshold the search appears, and it narrows the list.
store.recentSessions.value = [
	...store.recentSessions.value,
	...Array.from({ length: 4 }, (_, i) => ({
		name: `x${i}`, title: `Older chat ${i}`, modified: new Date(Date.now() - 200 * 864e5).toISOString(),
	})),
];
await tick();
side = root.querySelector("aside");
const search = side.querySelector("input");
for (const [name, ok] of [
	["search: appears past the threshold", Boolean(search)],
	["search: is a frappe-ui field", Boolean(search) && search.className.includes("rounded")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

search.value = "dormant";
search.dispatchEvent(new window.Event("input", { bubbles: true }));
await new Promise((r) => setTimeout(r, 300));
side = root.querySelector("aside");
for (const [name, ok] of [
	["search: filters", side.textContent.includes("Dormant stock") && !side.textContent.includes("Older chat")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }
search.value = "";
search.dispatchEvent(new window.Event("input", { bubbles: true }));
await new Promise((r) => setTimeout(r, 300));

// Full screen gives it a pane of its own, and the header stays above both.
store.fullscreen.value = true;
await tick();
for (const [name, ok] of [
	["fullscreen: list becomes a pane", root.querySelector("aside")?.className.includes("w-60")],
	["fullscreen: header above both panes", root.firstElementChild.firstElementChild.tagName === "HEADER"],
	["header: toggle is lucide", root.innerHTML.includes("lucide-panel-left")],
	["header: every control is a lucide mask", (root.querySelector("header").innerHTML.match(/lucide-/g) || []).length >= 4],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── blocks: the tiles, the chart and the table ──────────────────────────────
store.sidebarOpen.value = false;
store.messages.value.splice(0);
const dormant = [
	{ item_code: "WFGELH25-004", item_name: "Wood Figurines Elephant Head Brown #2 25cm", item_group: "Wood Figurines", last_sold_date: null, days_inactive: null, stock_qty: 1928, valuation_rate: 50000, tied_up_capital: 96400000 },
	{ item_code: "WFGKMDBRN50-001", item_name: "Wood Figurines Komodo Brown 50cm", item_group: "Wood Figurines", last_sold_date: null, days_inactive: null, stock_qty: 48, valuation_rate: 250000, tied_up_capital: 12000000 },
	{ item_code: "BRCNFCSBLU725-001", item_name: "Bracelets Natural Flat Coco Sharktooth Blue", item_group: "Bracelets", last_sold_date: "2026-03-02", days_inactive: 190, stock_qty: 1820, valuation_rate: 3956, tied_up_capital: 7199920 },
];
const part = (block) => ({ id: `b${Math.random()}`, type: "block", block });
store.messages.value.push({
	id: "a3", role: "assistant", pending: false, questions: [], runName: "RUN-3", feedback: null,
	parts: [
		part({ type: "kpi", label: "Dormant Sku Count", value: 25 }),
		part({ type: "kpi", label: "Dormant Tied Up Capital", value: 177586570 }),
		part({ type: "kpi", label: "Stagnant 90 Plus Tied Up Capital", value: 80615700 }),
		part({ type: "kpi", label: "Reorder Action Count", value: 20 }),
		part({ type: "bar", x: "item_name", horizontal: true, series: [{ key: "tied_up_capital", label: "Tied up capital" }], rows: dormant }),
		part({ type: "table", columns: Object.keys(dormant[0]).map((key) => ({ key, label: key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) })), rows: dormant }),
	],
});
await tick();

const table = root.querySelector("table");
const headers = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
const firstRow = [...table.querySelectorAll("tbody tr:first-child td")].map((td) => td.textContent.trim());
for (const [name, ok] of [
	["cells carry no markup", !table.textContent.includes("<div")],
	["money is formatted", firstRow.includes("Rp 96,400,000.00")],
	["quantities are plain numbers", firstRow.includes("1,928")],
	["a column one row fills is kept", headers.includes("Days inactive") && headers.includes("Last sold date")],
	["every column is present otherwise", headers.includes("Item code") && headers.includes("Tied up capital")],
	["a total row, formatted the same way", table.querySelector("tfoot").textContent.includes("Rp 115,599,920.00")],
	["tiles share one row", Boolean(root.querySelector(".grid-cols-\\[repeat\\(auto-fit\\,minmax\\(11rem\\,1fr\\)\\)\\]"))],
	["four tiles in it", root.querySelectorAll(".grid > .rounded-lg").length >= 4],
	["currency sits beside the reading", root.textContent.includes("Rp")],
	["the chart is drawn", Boolean(root.querySelector("svg, canvas"))],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// The reported case: a column that is empty in every row is dropped, rather than
// showing as a stripe of em dashes that reads like a bug.
store.messages.value[0].parts.push(part({
	type: "table",
	columns: ["item_code", "last_sold_date", "days_inactive"].map((key) => ({ key, label: key })),
	rows: dormant.slice(0, 2),
}));
await tick();
const pruned = [...root.querySelectorAll("table")].pop();
const prunedHeaders = [...pruned.querySelectorAll("thead th")].map((th) => th.textContent.trim());
for (const [name, ok] of [
	["a column empty in every row is dropped", prunedHeaders.join() === "item_code"],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// A report cell that carries HTML shows its text, never its markup.
store.messages.value[0].parts.push(part({
	type: "table",
	columns: [{ key: "status", label: "Status" }],
	rows: [{ status: "<div class='indicator red'>Overdue</div>" }, { status: "Paid" }],
}));
await tick();
const last = [...root.querySelectorAll("table")].pop();
for (const [name, ok] of [
	["html in a report cell is reduced to its text", last.textContent.includes("Overdue") && !last.textContent.includes("indicator")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── a whole turn with realtime dead ─────────────────────────────────────────
// The panel must narrate and finish on the polling path alone: this is the
// site's actual condition, and before the replay it showed "Working…" and then
// an empty message until the user reloaded.
store.messages.value.splice(0);
store.sessionName.value = null;

const steps = [
	[],
	[{ type: "tool_started", id: "t1", name: "selling_intelligence", label: "Selling intelligence", context: null, arguments: { months: 12 } }],
	[
		{ type: "tool_started", id: "t1", name: "selling_intelligence", label: "Selling intelligence", context: null, arguments: { months: 12 } },
		{ type: "tool_ended", id: "t1", ok: true, result: JSON.stringify({ rows: 3 }) },
		{ type: "block", block: { type: "kpi", label: "Total Revenue", value: 19825712.8 } },
	],
	[
		{ type: "tool_started", id: "t1", name: "selling_intelligence", label: "Selling intelligence", context: null, arguments: { months: 12 } },
		{ type: "tool_ended", id: "t1", ok: true, result: JSON.stringify({ rows: 3 }) },
		{ type: "block", block: { type: "kpi", label: "Total Revenue", value: 19825712.8 } },
		{ type: "text", delta: "Nayla Bahamas is **100%** of revenue." },
	],
];
let tick_n = 0;
DATA.start_run = { run: "RUN-POLL", conversation: "c9" };
DATA.get_run = () => {
	const events = steps[Math.min(tick_n, steps.length - 1)];
	const done = tick_n >= steps.length - 1;
	tick_n++;
	return { run: "RUN-POLL", status: done ? "Completed" : "Running", error: null,
		output: "Nayla Bahamas is **100%** of revenue.", iterations: 1, pending_call: null, events };
};

store.send("Which customer bought the most this year?");
await new Promise((r) => setTimeout(r, 400));
const turn = () => store.messages.value[1];
for (const [name, ok] of [
	["poll: the question is on screen at once", root.textContent.includes("bought the most")],
	["poll: working while nothing has happened yet", root.textContent.includes("Working…")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Two polls in (the first finds nothing yet, the second the running step).
await new Promise((r) => setTimeout(r, 2600));
for (const [name, ok] of [
	["poll: the step appears while it runs", root.textContent.includes("Selling intelligence")],
	["poll: the step is expanded while live", root.textContent.includes("Months")],
	["poll: still streaming", turn().pending === true],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

await new Promise((r) => setTimeout(r, 3000));
for (const [name, ok] of [
	["poll: the tile arrives", root.textContent.includes("Total Revenue")],
	["poll: the answer arrives", root.innerHTML.includes("<strong>100%</strong>")],
	["poll: nothing is duplicated", (root.innerHTML.match(/Total Revenue/g) || []).length === 1],
	["poll: the step is still named", root.textContent.includes("Selling intelligence")],
	["poll: the turn ends", store.sending.value === false && turn().pending === false],
	["poll: and folds up once it is done", !root.textContent.includes("Months")],
	["poll: and a follow-up can be typed", root.querySelector("textarea").disabled === false],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── an approval, with realtime dead ─────────────────────────────────────────
// The card has to appear from the poll alone. It did not: `questions` arrives as
// a JSON string there, prepareQuestions got the string, threw inside the event
// handler, and the run was left with no card and the composer stuck on Stop.
store.messages.value.splice(0);
store.sessionName.value = null;
store.toolApproval.value = { execute: true };
DATA.start_run = { run: "RUN-ASK", conversation: "c10" };
let asks = 0;
DATA.get_run = () => {
	const call = { id: "x1", name: "execute", label: "Executing", kind: "approval",
		summary: "Find items matching beach wrap",
		arguments: { description: "Find items matching beach wrap", code: "items = frappe.get_list('Item')" } };
	const paused = asks++ > 0;
	return {
		run: "RUN-ASK", status: paused ? "Paused" : "Running", error: null, output: null,
		iterations: 1, duration: paused ? 6.2 : null,
		pending_call: paused ? call : null,
		events: [],
	};
};

store.send("create a sales order for Sharkeez");
await new Promise((r) => setTimeout(r, 3000));
for (const [name, ok] of [
	["approval: the card arrives without a reload", root.textContent.includes("Find items matching beach wrap")],
	["approval: it offers the decision", root.textContent.includes("Approve") && root.textContent.includes("Reject")],
	["approval: the composer is released", store.sending.value === false],
	["approval: the arguments are shown", root.textContent.includes("frappe.get_list")],
	["approval: no note on a first attempt", !root.textContent.includes("first attempt was rejected")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// A retried write says what the site rejected the first time, so being asked twice
// for what looks like the same thing has a reason attached.
store.messages.value.splice(0);
store.messages.value.push({
	id: "a5", role: "assistant", pending: false, runName: "RUN-5", duration: null,
	parts: [{ id: "r1", type: "tool", name: "create", label: "Creating Records", context: "Sales Order",
		arguments: { doctype: "Sales Order", records: [{ customer: "Sharkeez" }] }, result: null, approval: null }],
	questions: [{ key: "r1", kind: "approval", prompt: "Create 1 Sales Order record",
		note: "Delivery warehouse required for stock item BWR-001",
		options: ["Approve", "Deny"], _showOther: false, _otherText: "", _answer: undefined }],
});
store.toolApproval.value = { create: true };
await tick();
for (const [name, ok] of [
	["retry: the card says what was rejected", root.textContent.includes("Delivery warehouse required")],
	["retry: and does not claim it is waiting", !root.textContent.includes("waiting for you before")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// ── the footer, the declared formats ────────────────────────────────────────
store.messages.value.splice(0);
store.messages.value.push({
	id: "a4", role: "assistant", pending: false, questions: [], runName: "RUN-4", duration: 6.2,
	parts: [
		{ id: "k1", type: "block", block: { type: "kpi", label: "Sales Invoice (filtered)", value: 0, format: "number" } },
		{ id: "k2", type: "block", block: { type: "kpi", label: "Dormant Tied Up Capital", value: 177586570, format: "currency" } },
		{ id: "x9", type: "text", text: "Nothing last year." },
		{ id: "tb", type: "block", block: { type: "table",
			columns: [{ key: "customer", label: "Customer" }, { key: "value", label: "SUM of base_grand_total", format: "currency" }, { key: "orders", label: "Orders", format: "number" }],
			rows: [{ customer: "Sharkeez", value: 2477067.18, orders: 4 }] } },
	],
});
await tick();
const tiles = root.textContent;
for (const [name, ok] of [
	["kpi: a count is not money", /Sales Invoice \(filtered\)\s*0/.test(tiles.replace(/\s+/g, " "))],
	["kpi: a declared currency still is", tiles.includes("Rp")],
	["table: a declared currency column is formatted", tiles.includes("Rp 2,477,067.18")],
	["table: a declared number column is not", tiles.includes("4") && !tiles.includes("Rp 4")],
	["footer: the turn says how long it took", tiles.includes("6.2s")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── the composer ────────────────────────────────────────────────────────────
store.messages.value.splice(0);
store.sending.value = false;
DATA.start_run = { run: "RUN-C", conversation: "c11" };
DATA.get_run = () => ({ run: "RUN-C", status: "Completed", error: null, output: "ok", iterations: 1, duration: 1, pending_call: null, events: [{ type: "text", delta: "ok" }] });
await tick();

const field = root.querySelector("textarea");
// A five-line prompt grows the field: jsdom reports scrollHeight 0, so the grown
// state is asserted through the code path rather than the pixels — what matters is
// that sending puts the field back to having no inline height at all, which is what
// `rows="1"` needs to decide it again.
field.value = "one\ntwo\nthree\nfour\nfive";
field.dispatchEvent(new window.Event("input", { bubbles: true }));
await tick();
field.style.height = "132px"; // as the browser would have left it
field.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await new Promise((r) => setTimeout(r, 120));

const controls = root.querySelector("textarea").parentElement.lastElementChild;
for (const [name, ok] of [
	["composer: the prompt was sent", store.messages.value.some((m) => m.role === "user" && m.content.includes("five"))],
	["composer: the field is empty again", root.querySelector("textarea").value === ""],
	["composer: and back to one row", root.querySelector("textarea").style.height === ""],
	["composer: controls line up with the text", controls.className.includes("px-2") && !controls.className.includes("px-1.5")],
	["composer: the icon buttons are not touching", controls.className.includes("gap-1")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

store.sending.value = true;
await tick();
const stop = [...root.querySelector("textarea").parentElement.lastElementChild.querySelectorAll("button")].pop();
for (const [name, ok] of [
	["composer: stopping is subtle, not a solid block", stop.className.includes("bg-surface-red-2") || stop.className.includes("bg-surface-red-1")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }
store.sending.value = false;


// ── Stop ends the turn in the view ──────────────────────────────────────────
// The abort ends the stream, so no `done` arrives; the message has to stop being
// pending here or the panel shimmers "Working…" with nothing running.
store.messages.value.splice(0);
store.sessionName.value = null;
DATA.start_run = { run: "RUN-STOP", conversation: "c12" };
DATA.stop_run = { status: "Stopped" };
DATA.get_run = () => ({ run: "RUN-STOP", status: "Running", error: null, output: null, iterations: 1, duration: null, pending_call: null, events: [] });

store.send("count everything, slowly");
await new Promise((r) => setTimeout(r, 200));
const turn2 = () => store.messages.value[1];
for (const [name, ok] of [
	["stop: working while it runs", turn2().pending === true && root.textContent.includes("Working…")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

store.stopRun();
await new Promise((r) => setTimeout(r, 200));
for (const [name, ok] of [
	["stop: the turn is no longer pending", turn2().pending === false],
	["stop: 'Working…' is gone", !root.textContent.includes("Working…")],
	["stop: the composer is usable again", store.sending.value === false && root.querySelector("textarea").disabled === false],
	["stop: and it says it was stopped", root.textContent.includes("Stopped")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }


// ── table headings ──────────────────────────────────────────────────────────
// The exact bug reported: a domain tool's autorendered tables sat side by side
// as bare "25 records" / "20 records" with nothing to say which was which.
store.messages.value.splice(0);
const heading = (block) => {
	store.messages.value = [{
		id: "h1", role: "assistant", pending: false, questions: [], runName: "RUN-H", duration: null,
		parts: [{ id: "hb", type: "block", block }],
	}];
};

heading({ type: "table", title: "Reorder Recommendations",
	columns: [{ key: "item_code", label: "Item Code" }], rows: [{ item_code: "A" }] });
await tick();
for (const [name, ok] of [
	["heading: a declared title is shown", root.textContent.includes("Reorder Recommendations")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

heading({ type: "table", doctype: "Sales Invoice",
	columns: [{ key: "name", label: "Name" }], rows: [{ name: "ACC-SINV-001" }] });
await tick();
for (const [name, ok] of [
	["heading: falls back to doctype when no title", root.textContent.includes("Sales Invoice")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

heading({ type: "table", title: "Inactive Items 60 To 90 Days",
	columns: [{ key: "item_code", label: "Item Code" }], rows: [{ item_code: "A" }] });
const other = { type: "table", title: "Reorder Recommendations",
	columns: [{ key: "item_code", label: "Item Code" }], rows: [{ item_code: "B" }] };
store.messages.value[0].parts.push({ id: "hb2", type: "block", block: other });
await tick();
const titledTables = [...root.querySelectorAll(".rounded-lg")].map((el) => el.textContent).filter((t) => t.includes("Item Code"));
for (const [name, ok] of [
	["heading: two tables read differently", root.textContent.includes("Inactive Items 60 To 90 Days") && root.textContent.includes("Reorder Recommendations")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// A long title (execute's own description) truncates rather than pushing the
// record count and buttons off the header bar.
heading({ type: "table", title: "Compute items inactive between 60 and 90 days and their tied up capital across every warehouse",
	columns: [{ key: "a", label: "A" }], rows: [{ a: 1 }] });
await tick();
const headEl = [...root.querySelectorAll("span")].find((e) => e.title === "Compute items inactive between 60 and 90 days and their tied up capital across every warehouse");
for (const [name, ok] of [
	["heading: a long one truncates, not wraps", Boolean(headEl) && headEl.className.includes("truncate")],
	["heading: full text still reachable on hover", Boolean(headEl) && headEl.getAttribute("title")?.length > 40],
	["heading: the record count survives next to it", root.textContent.includes("1 records") || root.textContent.includes("record")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

// Neither title nor doctype: unchanged old behaviour, just the count chip.
heading({ type: "table", columns: [{ key: "a", label: "A" }], rows: [{ a: 1 }] });
await tick();
for (const [name, ok] of [
	["heading: no heading, no crash, count still shown", root.textContent.includes("1 records")],
]) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) bad++; }

console.log(bad ? `\n${bad} FAILURES` : "\nall checks passed");
process.exit(bad ? 1 : 0);
