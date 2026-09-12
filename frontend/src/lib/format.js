// Turning a value into what a person reads in a cell or on a tile.
//
// It all goes through the desk's own formatters, because a currency column in
// the chat that reads `96400000` next to the same column on the desk reading
// `Rp 96,400,000.00` looks like a different product. Two things are easy to get
// wrong there, and both were:
//
//  - `frappe.format` returns *HTML* for every numeric fieldtype —
//    `<div style='text-align: right'>…</div>` — unless you pass
//    `{only_value: true}`. Rendered as text (which is the only safe way to
//    render model- or report-supplied data), that markup shows up in the cell.
//  - a Script Report cell may itself carry HTML: an indicator pill, a link.
//    So a string value is reduced to its text before it is shown.
import { __ } from "@/lib/translate";

// Column names that mean money. One list, used by the tables and the KPI tiles,
// so a figure doesn't gain a currency symbol on a tile and lose it in a table.
const MONETARY =
	/amount|total|price|rate|value|valuation|revenue|sales|spend|cost|capital|balance|outstanding|paid|margin|profit|fee|discount|tax/i;

export const looksMonetary = (key) => MONETARY.test(String(key || ""));

/** The site's currency symbol ("Rp", "₹"), or "" when it can't be resolved. */
export function currencySymbol() {
	const code = frappe.boot?.sysdefaults?.currency;
	if (!code) return "";
	try {
		return frappe.model?.get_value?.("Currency", code, "symbol") || code;
	} catch {
		return code;
	}
}

/** A string that may carry markup, reduced to the text a person should see. */
export function plainText(value) {
	const text = String(value);
	if (!text.includes("<")) return text;
	try {
		return new DOMParser().parseFromString(text, "text/html").body.textContent.trim();
	} catch {
		return text;
	}
}

/**
 * One cell. `format` is what the block declared for the column ("currency" |
 * "number", stamped from the doctype's own meta server-side); the column name is
 * only read when nothing was declared. Empty is an em dash, never "" — a blank
 * cell reads as a broken table.
 */
export function formatCell(value, key, format) {
	if (value === null || value === undefined || value === "") return "—";
	if (typeof value === "boolean") return value ? __("Yes") : __("No");
	if (typeof value === "number") {
		const money = format ? format === "currency" : looksMonetary(key);
		return frappe.format(value, { fieldtype: money ? "Currency" : "Float" }, { only_value: true });
	}
	if (typeof value === "object") return "—"; // an empty {} / []; anything else renders elsewhere
	return plainText(value);
}
