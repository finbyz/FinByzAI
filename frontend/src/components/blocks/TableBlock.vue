<script setup>
import { computed, ref } from "vue";
import { TextInput } from "@/lib/ui";
import { humanize } from "@/lib/tools";
import { formatCell, looksMonetary, plainText } from "@/lib/format";
import { __ } from "@/lib/translate";

// A records table inside the chat, on frappe-ui's ListView vocabulary: the header
// is `bg-surface-gray-2` with `text-sm text-ink-gray-5` labels, rows go
// `hover:bg-surface-menu-bar`, numbers sit right and on `tabular-nums` so a money
// column can be read down.
//
// Filtering is client-side over the rows the backend already sent: it narrows what
// you are reading, it never re-queries, so the numbers can never drift from the
// ones the agent saw.
//
// A column with nothing in any row is dropped. An all-empty column is how a
// "Last Sold Date" column full of em dashes ends up looking like a bug when in
// fact the answer is that none of these items ever sold.
const props = defineProps({ block: { type: Object, required: true } });

const VISIBLE_ROWS = 12;
const filters = ref({});
const showFilters = ref(false);
const expanded = ref(false);

const allRows = computed(() => props.block.rows || []);

const hasValue = (key) =>
	allRows.value.some((row) => row[key] !== null && row[key] !== undefined && row[key] !== "");

const columns = computed(() => {
	const raw = props.block.columns?.length
		? props.block.columns
		: Object.keys(allRows.value[0] || {}).map((key) => ({ key }));
	return raw
		.filter((col) => hasValue(col.key))
		.map((col) => ({
			key: col.key,
			label: col.label || humanize(col.key),
			align: col.align || (isNumericColumn(col.key) ? "right" : "left"),
			monetary: looksMonetary(col.key),
		}));
});

const rows = computed(() => {
	const active = Object.entries(filters.value).filter(([, v]) => String(v || "").trim());
	if (!active.length) return allRows.value;
	return allRows.value.filter((row) =>
		active.every(([key, needle]) =>
			String(row[key] ?? "")
				.toLowerCase()
				.includes(String(needle).toLowerCase())
		)
	);
});

const visibleRows = computed(() => (expanded.value ? rows.value : rows.value.slice(0, VISIBLE_ROWS)));
const hiddenCount = computed(() => Math.max(0, rows.value.length - visibleRows.value.length));
const filtered = computed(() => rows.value.length !== allRows.value.length);

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
	const row = allRows.value.find((r) => r[key] !== null && r[key] !== undefined);
	return typeof row?.[key] === "number";
}

// Formatting lives in lib/format.js — the desk's formatters, with the two traps
// (HTML-wrapped numbers, HTML inside report cells) handled in one place.
const cell = formatCell;

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
				.map((c) => `"${plainText(row[c.key] ?? "").replace(/"/g, '""')}"`)
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
			<span v-if="block.doctype" class="text-sm font-medium text-ink-gray-8">{{
				block.doctype
			}}</span>
			<span class="rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-6">
				{{ filtered ? __("{0} of {1} records", [rows.length, allRows.length]) : __("{0} records", [rows.length]) }}
			</span>
			<span class="flex-1"></span>
			<button
				class="flex size-6 items-center justify-center rounded hover:bg-surface-gray-3"
				:class="showFilters ? 'text-ink-gray-8' : 'text-ink-gray-5'"
				:title="__('Filter rows')"
				@click="showFilters = !showFilters"
			>
				<span class="lucide-filter size-3.5" aria-hidden="true"></span>
			</button>
			<button
				class="flex size-6 items-center justify-center rounded text-ink-gray-5 hover:bg-surface-gray-3"
				:title="__('Download CSV')"
				@click="exportCsv"
			>
				<span class="lucide-download size-3.5" aria-hidden="true"></span>
			</button>
		</div>

		<div class="copilot-scrollbar overflow-x-auto">
			<table class="w-full border-collapse text-left text-sm">
				<thead class="bg-surface-gray-2 text-ink-gray-5">
					<tr>
						<th
							v-for="col in columns"
							:key="col.key"
							class="whitespace-nowrap px-2.5 py-2 font-normal"
							:class="col.align === 'right' ? 'text-right' : 'text-left'"
						>
							{{ col.label }}
						</th>
					</tr>
					<tr v-if="showFilters" class="bg-surface-white">
						<th
							v-for="col in columns"
							:key="`f-${col.key}`"
							class="border-b border-outline-gray-1 px-1.5 py-1.5"
						>
							<TextInput
								v-model="filters[col.key]"
								class="min-w-[6rem]"
								size="sm"
								:placeholder="col.label"
								:debounce="150"
							/>
						</th>
					</tr>
				</thead>

				<tbody class="text-ink-gray-8">
					<tr
						v-for="(row, i) in visibleRows"
						:key="i"
						class="border-b border-outline-gray-1 last:border-0 hover:bg-surface-menu-bar"
					>
						<td
							v-for="col in columns"
							:key="col.key"
							class="max-w-[220px] truncate whitespace-nowrap px-2.5 py-2"
							:class="[
								col.align === 'right' ? 'text-right tabular-nums' : 'text-left',
								col.key === 'status' ? statusTone(row[col.key]) : '',
							]"
							:title="plainText(row[col.key] ?? '')"
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
						<td :colspan="columns.length" class="px-2.5 py-4 text-center text-ink-gray-5">
							{{ __("No rows match these filters.") }}
						</td>
					</tr>
				</tbody>

				<tfoot v-if="Object.keys(totals).length">
					<tr class="border-t border-outline-gray-2 bg-surface-gray-2 font-medium">
						<td
							v-for="(col, i) in columns"
							:key="`t-${col.key}`"
							class="whitespace-nowrap px-2.5 py-2"
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
			class="w-full border-t border-outline-gray-1 py-2 text-xs text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
			@click="expanded = true"
		>
			{{ __("Show {0} more rows", [hiddenCount]) }}
		</button>
		<button
			v-else-if="expanded && rows.length > VISIBLE_ROWS"
			class="w-full border-t border-outline-gray-1 py-2 text-xs text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
			@click="expanded = false"
		>
			{{ __("Collapse") }}
		</button>
	</div>
</template>
