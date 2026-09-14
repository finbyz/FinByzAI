// What a tool call means on screen: its name in words, its subject, whether it
// failed, and the shape each argument should take in a 770px column.
//
// The backend already answers the first three for every event it publishes — see
// `label`, `context` and `summary` in copilot/runner.py — so nothing here
// re-derives them from a table of tool names the way this layer used to. A tool
// added server-side now reads correctly with no change on this side; the only
// name the frontend still knows is `delete`, and only because "destructive" is a
// visual decision the server has no opinion about.
import { __ } from "@/lib/translate";

// ── names ───────────────────────────────────────────────────────────────────

/** snake_case → "Snake case". */
export function humanize(name) {
	return String(name || "")
		.replace(/_/g, " ")
		.replace(/^./, (c) => c.toUpperCase());
}

/** The activity line's label: the server's, or the tool's own name spelled out. */
export const toolLabel = (name, label) => label || humanize(name);

/** Models sometimes leak special tokens into a call ("describe<|channel|>"). */
export function normalizeToolName(name) {
	const clean = String(name || "").split("<|")[0].trim();
	return clean || String(name || "").trim();
}

/** The muted suffix on a line — which doctype / report / action. Mirrors
 *  runner._context for the reload path, where no live event carried one. */
export function contextOf(args) {
	const a = parseArgs(args);
	for (const key of ["doctype", "report", "search", "action"]) {
		const value = a[key];
		if (typeof value === "string" && value) return key === "action" ? humanize(value) : value;
	}
	return null;
}

/** Tools whose approval card asks in red rather than gray. */
export const isDestructive = (name) => name === "delete";

// ── arguments ───────────────────────────────────────────────────────────────

export function parseArgs(args) {
	if (!args) return {};
	if (typeof args === "object") return args;
	try {
		const parsed = JSON.parse(args);
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

/** The raw payload when it won't parse into fields, so a truncated or malformed
 *  call is shown verbatim instead of silently vanishing. "" otherwise. */
export function rawArgs(args) {
	if (typeof args !== "string" || !args.trim()) return "";
	return Object.keys(parseArgs(args)).length ? "" : args;
}

export const hasArgs = (args) =>
	Object.keys(parseArgs(args)).length > 0 || Boolean(rawArgs(args));

/** Arguments that are source code whatever their length. */
const SOURCE = { execute: new Set(["code"]), run_query: new Set(["sql"]) };
export const sourceKeys = (name) => SOURCE[name] || new Set();

// ── results ─────────────────────────────────────────────────────────────────

/** A wholly failed call's message, else null. The guard returns {error}; a bulk
 *  write returns {created|updated|deleted, failures} and only counts as failed
 *  when nothing at all got through. */
export function toolError(result) {
	if (typeof result !== "string") return null;
	let payload;
	try {
		payload = JSON.parse(result);
	} catch {
		return null; // plain text, not an error envelope
	}
	if (!payload || typeof payload !== "object") return null;
	if (typeof payload.error === "string") return payload.error;

	const failures = Array.isArray(payload.failures) ? payload.failures : [];
	const wrote = [payload.created, payload.updated, payload.deleted].some(
		(rows) => Array.isArray(rows) && rows.length
	);
	if (!failures.length || wrote) return null;
	return failures.map((f) => f?.error).filter((e) => typeof e === "string").join("\n") || null;
}

/** True while a call is still in flight — no result and no pending decision. */
export const isRunning = (part) => part.result === null && part.approval === null;

// ── values ──────────────────────────────────────────────────────────────────

export const isScalar = (v) => v === null || typeof v !== "object";
const isRecord = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** Multi-line, or one line too long to sit in a table cell. */
const LONG = 120;
const isLongText = (v) => typeof v === "string" && (v.includes("\n") || v.length > LONG);

// Frappe filter conditions are by far the most common argument in this app, so
// the one value shape worth naming: ["like", "%acme%"], ["in", [...]].
const OPERATORS = new Set([
	"=", "!=", ">", "<", ">=", "<=",
	"like", "not like", "in", "not in", "between", "is",
]);
const isCondition = (v) =>
	Array.isArray(v) &&
	v.length === 2 &&
	typeof v[0] === "string" &&
	OPERATORS.has(v[0].toLowerCase()) &&
	(isScalar(v[1]) || (Array.isArray(v[1]) && v[1].every(isScalar)));

/**
 * How a value renders: "empty" and "text" inline in the field grid; everything
 * else takes a full-width row of its own.
 *   code      — source, or prose too long to inline
 *   condition — a filter operator and its operand
 *   tags       — a list of scalars
 *   grid      — a list of records, as columns
 *   fields    — a nested object, recursed
 */
export function shapeOf(value, { source = false } = {}) {
	if (value === null || value === undefined || value === "") return "empty";
	if (source && typeof value === "string") return "code";
	if (Array.isArray(value)) {
		if (!value.length) return "empty";
		if (isCondition(value)) return "condition";
		return value.every(isScalar) ? "tags" : "grid";
	}
	if (typeof value === "object") return Object.keys(value).length ? "fields" : "empty";
	return isLongText(value) ? "code" : "text";
}

/** Inline text for one scalar. Never returns "" — an empty cell looks broken. */
export function formatValue(value) {
	if (value === null || value === undefined || value === "") return "—";
	if (typeof value === "boolean") return value ? __("Yes") : __("No");
	if (typeof value === "object") return "—"; // an empty {} / []; anything else renders elsewhere
	return String(value);
}

/** ["in", ["Open", "Overdue"]] → { operator: "in", operand: "Open, Overdue" }. */
export function formatCondition(value) {
	const operand = Array.isArray(value[1]) ? value[1] : [value[1]];
	return { operator: value[0], operand: operand.map(formatValue).join(", ") };
}

/** One column order for a list of records: every key, first seen first, so a
 *  field stays in the same place whatever order an individual record used. */
export function columnsOf(rows) {
	const columns = [];
	for (const row of rows) {
		if (!isRecord(row)) continue;
		for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key);
	}
	return columns;
}

export { isRecord };

/** "https://www.example.com/path" → "example.com" — the bit worth showing next to
 *  a source link, not the whole address. Falls back to the raw string for
 *  anything that isn't a real URL rather than throwing. */
export function hostnameOf(url) {
	try {
		return new URL(url).hostname.replace(/^www\./, "");
	} catch {
		return String(url || "");
	}
}
