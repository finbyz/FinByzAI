<script setup>
import { computed, ref } from "vue";
import { __ } from "@/lib/translate";

// A desk-style records table inside the chat: doctype + record count, sticky header,
// a filter box per column, record links, and CSV export. Filtering is client-side over
// the rows the backend already sent — it narrows what you are reading, it never
// re-queries, so the numbers can't drift from what the agent saw.
const props = defineProps({ block: { type: Object, required: true } });

const VISIBLE_ROWS = 12;
const filters = ref({});
const showFilters = ref(false);
const expanded = ref(false);

const columns = computed(() => {
	const raw = props.block.columns?.length
		? props.block.columns
		: Object.keys(props.block.rows?.[0] || {}).map((key) => ({ key }));
	return raw.map((col) => ({
		key: col.key,
		label: col.label || humanize(col.key),
		align: col.align || (isNumericColumn(col.key) ? "right" : "left"),
	}));
});

const rows = computed(() => {
	const active = Object.entries(filters.value).filter(([, v]) => String(v || "").trim());
	if (!active.length) return props.block.rows || [];
	return (props.block.rows || []).filter((row) =>
		active.every(([key, needle]) =>
			String(row[key] ?? "")
				.toLowerCase()
				.includes(String(needle).toLowerCase())
		)
	);
});

const visibleRows = computed(() => (expanded.value ? rows.value : rows.value.slice(0, VISIBLE_ROWS)));
const hiddenCount = computed(() => Math.max(0, rows.value.length - visibleRows.value.length));
const filtered = computed(() => rows.value.length !== (props.block.rows || []).length);

// Totals for numeric columns, so a long table still answers "how much in all".
const totals = computed(() => {
	const out = {};
	for (const col of columns.value) {
		if (col.align !== "right") continue;
		const numbers = rows.value.map((r) => r[col.key]).filter((v) => typeof v === "number");
		if (numbers.length > 1) out[col.key] = numbers.reduce((a, b) => a + b, 0);
	}
	return out;
});

function isNumericColumn(key) {
	const row = (props.block.rows || []).find((r) => r[key] !== null && r[key] !== undefined);
	return typeof row?.[key] === "number";
}

function humanize(key) {
	return String(key || "")
		.replace(/_/g, " ")
		.replace(/^./, (c) => c.toUpperCase());
}

// The desk's own formatter, so currency, dates and floats match the rest of the site.
function cell(value, key) {
	if (value === null || value === undefined || value === "") return "—";
	if (typeof value === "boolean") return value ? __("Yes") : __("No");
	if (typeof value === "number") {
		const currency = /amount|total|price|rate|value|balance|paid|outstanding/i.test(key);
		return frappe.format(value, { fieldtype: currency ? "Currency" : "Float" });
	}
	if (typeof value === "object") return "—";
	return String(value);
}

const isLink = (col, row) =>
	Boolean(props.block.doctype) && col.key === "name" && typeof row[col.key] === "string";

const statusTone = (value) => {
	const v = String(value || "").toLowerCase();
	if (/paid|completed|submitted|active|success|approved/.test(v)) return "text-ink-green-3";
	if (/overdue|failed|cancelled|rejected|error/.test(v)) return "text-ink-red-3";
	if (/draft|pending|unpaid|open|queued/.test(v)) return "text-ink-amber-3";
	return "";
};

function exportCsv() {
	const header = columns.value.map((c) => `"${c.label}"`).join(",");
	const body = rows.value
		.map((row) =>
			columns.value
				.map((c) => `"${String(row[c.key] ?? "").replace(/"/g, '""')}"`)
				.join(",")
		)
		.join("\n");
	const blob = new Blob([`${header}\n${body}`], { type: "text/csv;charset=utf-8" });
	const link = document.createElement("a");
	link.href = URL.createObjectURL(blob);
	link.download = `${props.block.doctype || "copilot"}-export.csv`;
	link.click();
	URL.revokeObjectURL(link.href);
}
</script>

<template>
	<div class="overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-white">
		<!-- doctype · N records, filter toggle, export -->
		<div
			class="flex items-center gap-2 border-b border-outline-gray-1 px-2.5 py-1.5 text-ink-gray-6"
		>
			<span v-if="block.doctype" class="text-xs font-medium text-ink-gray-8">{{
				block.doctype
			}}</span>
			<span class="rounded bg-surface-gray-2 px-1.5 py-0.5 text-2xs text-ink-gray-6">
				{{ filtered ? __("{0} of {1} records", [rows.length, block.rows.length]) : __("{0} records", [rows.length]) }}
			</span>
			<span class="flex-1"></span>
			<button
				class="flex h-5 items-center gap-1 rounded px-1 text-2xs hover:bg-surface-gray-2"
				:class="showFilters ? 'text-ink-gray-8' : 'text-ink-gray-5'"
				:title="__('Filter rows')"
				@click="showFilters = !showFilters"
			>
				<span class="lucide-filter size-3" aria-hidden="true"></span>
			</button>
			<button
				class="flex h-5 items-center gap-1 rounded px-1 text-2xs text-ink-gray-5 hover:bg-surface-gray-2"
				:title="__('Download CSV')"
				@click="exportCsv"
			>
				<span class="lucide-download size-3" aria-hidden="true"></span>
			</button>
		</div>

		<div class="overflow-x-auto">
			<table class="w-full border-collapse text-left text-xs">
				<thead class="bg-surface-gray-1 text-ink-gray-6">
					<tr>
						<th
							v-for="col in columns"
							:key="col.key"
							class="whitespace-nowrap border-b border-outline-gray-1 px-2.5 py-1.5 font-medium"
							:class="col.align === 'right' ? 'text-right' : 'text-left'"
						>
							{{ col.label }}
						</th>
					</tr>
					<tr v-if="showFilters">
						<th
							v-for="col in columns"
							:key="`f-${col.key}`"
							class="border-b border-outline-gray-1 px-1.5 py-1"
						>
							<input
								v-model="filters[col.key]"
								type="text"
								class="h-6 w-full min-w-[70px] rounded border border-outline-gray-2 bg-surface-white px-1.5 text-2xs text-ink-gray-8 outline-none placeholder:text-ink-gray-4 focus:border-outline-gray-3"
								:placeholder="col.label"
							/>
						</th>
					</tr>
				</thead>

				<tbody class="text-ink-gray-8">
					<tr
						v-for="(row, i) in visibleRows"
						:key="i"
						class="border-b border-outline-gray-1 last:border-0 hover:bg-surface-gray-1"
					>
						<td
							v-for="col in columns"
							:key="col.key"
							class="max-w-[220px] truncate whitespace-nowrap px-2.5 py-1.5"
							:class="[
								col.align === 'right' ? 'text-right tabular-nums' : 'text-left',
								col.key === 'status' ? statusTone(row[col.key]) : '',
							]"
							:title="String(row[col.key] ?? '')"
						>
							<a
								v-if="isLink(col, row)"
								:href="`/app/${encodeURIComponent(block.doctype)}/${encodeURIComponent(row[col.key])}`"
								target="_blank"
								class="text-ink-blue-3 hover:underline"
								>{{ row[col.key] }}</a
							>
							<template v-else>{{ cell(row[col.key], col.key) }}</template>
						</td>
					</tr>
					<tr v-if="!rows.length">
						<td :colspan="columns.length" class="px-2.5 py-3 text-center text-ink-gray-5">
							{{ __("No rows match these filters.") }}
						</td>
					</tr>
				</tbody>

				<tfoot v-if="Object.keys(totals).length">
					<tr class="border-t border-outline-gray-2 bg-surface-gray-1 font-medium">
						<td
							v-for="(col, i) in columns"
							:key="`t-${col.key}`"
							class="whitespace-nowrap px-2.5 py-1.5"
							:class="col.align === 'right' ? 'text-right tabular-nums' : 'text-left'"
						>
							<template v-if="totals[col.key] !== undefined">{{
								cell(totals[col.key], col.key)
							}}</template>
							<template v-else-if="i === 0">{{ __("Total") }}</template>
						</td>
					</tr>
				</tfoot>
			</table>
		</div>

		<button
			v-if="hiddenCount"
			class="w-full border-t border-outline-gray-1 py-1.5 text-2xs text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
			@click="expanded = true"
		>
			{{ __("Show {0} more rows", [hiddenCount]) }}
		</button>
		<button
			v-else-if="expanded && rows.length > VISIBLE_ROWS"
			class="w-full border-t border-outline-gray-1 py-1.5 text-2xs text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
			@click="expanded = false"
		>
			{{ __("Collapse") }}
		</button>
	</div>
</template>
