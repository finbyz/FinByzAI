<script setup>
import { computed, ref } from "vue";
import Code from "./Code.vue";
import {
	columnsOf,
	formatCondition,
	formatValue,
	humanize,
	isRecord,
	parseArgs,
	rawArgs,
	shapeOf,
	sourceKeys,
} from "@/lib/tools";
import { __ } from "@/lib/translate";

// What a tool was actually called with.
//
// One definition grid, no box: this already sits inside a card, inside a panel,
// inside the desk, and the version before this drew its own bordered table at
// the bottom of that stack — four nested frames to read one filter. Labels are
// `ink-gray-5`, values `ink-gray-8`; the contrast does the work a border was
// doing.
//
// Short values pair off in two columns. Anything that needs room — code, a list,
// a set of records, a nested object — takes a full-width row with its label
// above it, in place, so the argument order the model used is preserved.
const props = defineProps({
	arguments: { default: null },
	tool: { type: String, default: "" },
	depth: { type: Number, default: 0 },
});

// Past this the call is pathological; show it as the JSON it is rather than
// nesting grids until the column collapses.
const MAX_DEPTH = 2;
const TAG_LIMIT = 24;
const ROW_LIMIT = 8;

const fields = computed(() => {
	const source = sourceKeys(props.tool);
	return Object.entries(parseArgs(props.arguments)).map(([key, value]) => ({
		key,
		value,
		label: humanize(key),
		shape: shapeOf(value, { source: source.has(key) }),
	}));
});

const inline = computed(() => fields.value.filter((f) => f.shape === "empty" || f.shape === "text" || f.shape === "condition"));
const wide = computed(() => fields.value.filter((f) => !inline.value.includes(f)));
const raw = computed(() => (fields.value.length ? "" : rawArgs(props.arguments)));

const expanded = ref({});
const rowsFor = (field) =>
	expanded.value[field.key] ? field.value : field.value.slice(0, ROW_LIMIT);
const tagsFor = (field) =>
	expanded.value[field.key] ? field.value : field.value.slice(0, TAG_LIMIT);
const toggle = (field) => (expanded.value[field.key] = !expanded.value[field.key]);

const json = (value) => JSON.stringify(value, null, 2);
</script>

<template>
	<div v-if="fields.length || raw" class="flex flex-col gap-2 text-sm">
		<dl
			v-if="inline.length"
			class="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1"
		>
			<template v-for="field in inline" :key="field.key">
				<dt class="truncate text-ink-gray-5">{{ field.label }}</dt>
				<dd class="min-w-0 break-words text-ink-gray-8">
					<template v-if="field.shape === 'condition'">
						<span class="text-ink-gray-5">{{ formatCondition(field.value).operator }}</span>
						{{ formatCondition(field.value).operand }}
					</template>
					<template v-else>{{ formatValue(field.value) }}</template>
				</dd>
			</template>
		</dl>

		<div v-for="field in wide" :key="field.key" class="min-w-0">
			<div class="pb-1 text-ink-gray-5">{{ field.label }}</div>

			<Code v-if="field.shape === 'code'" :code="String(field.value)" />

			<!-- A list of scalars: wrapping tags, capped so one huge list can't
			     take over the panel. -->
			<div v-else-if="field.shape === 'tags'" class="flex flex-wrap items-center gap-1">
				<span
					v-for="(item, i) in tagsFor(field)"
					:key="i"
					class="rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-7"
					>{{ formatValue(item) }}</span
				>
				<button
					v-if="field.value.length > TAG_LIMIT"
					class="text-xs text-ink-gray-5 hover:text-ink-gray-7"
					@click="toggle(field)"
				>
					{{
						expanded[field.key]
							? __("Show less")
							: __("+{0} more", [field.value.length - TAG_LIMIT])
					}}
				</button>
			</div>

			<!-- A list of records: one row each, fields as columns, so twenty
			     records read as a shape instead of twenty stacked blobs. -->
			<template v-else-if="field.shape === 'grid' && depth < MAX_DEPTH">
				<div class="copilot-scrollbar -mx-1 overflow-x-auto px-1">
					<table class="w-full text-left text-xs">
						<thead>
							<tr class="text-ink-gray-5">
								<th
									v-for="column in columnsOf(field.value)"
									:key="column"
									class="whitespace-nowrap py-1 pr-3 font-normal"
								>
									{{ humanize(column) }}
								</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-outline-gray-1">
							<tr v-for="(row, i) in rowsFor(field)" :key="i" class="align-top">
								<td
									v-for="column in columnsOf(field.value)"
									:key="column"
									class="max-w-[16rem] truncate py-1 pr-3 text-ink-gray-8"
									:title="isRecord(row) ? formatValue(row[column]) : ''"
								>
									{{ isRecord(row) ? formatValue(row[column]) : formatValue(row) }}
								</td>
							</tr>
						</tbody>
					</table>
				</div>
				<button
					v-if="field.value.length > ROW_LIMIT"
					class="mt-1 text-xs text-ink-gray-5 hover:text-ink-gray-7"
					@click="toggle(field)"
				>
					{{
						expanded[field.key]
							? __("Show less")
							: __("Show {0} more", [field.value.length - ROW_LIMIT])
					}}
				</button>
			</template>

			<!-- A nested object recurses, marked by a rule rather than an indent
			     so the nesting is visible at a glance. -->
			<div
				v-else-if="field.shape === 'fields' && depth < MAX_DEPTH"
				class="border-l border-outline-gray-2 pl-2.5"
			>
				<ToolArgs :arguments="field.value" :tool="tool" :depth="depth + 1" />
			</div>

			<Code v-else :code="json(field.value)" />
		</div>

		<!-- Arguments the model didn't finish writing, or wrote as something other
		     than an object. Shown verbatim; hiding them hides why a call failed. -->
		<Code v-if="raw" :code="raw" />
	</div>
</template>
