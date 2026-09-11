<script setup>
import { computed } from "vue";
import { AxisChart } from "@/lib/ui";
import { __ } from "@/lib/translate";

// Bar, line and area in one component, on frappe-ui's AxisChart — which brings the
// tooltip, the legend, the axis formatting and the empty state with it.
//
// The important detail is the series mapping. AxisChart reads each series' values as
// `row[series.name]` (axisChartOptions.ts:56), so `name` must be the *data key*, not a
// caption. Our blocks carry both — `{key: "value", label: "SUM of base_grand_total by
// Customer"}` — and the previous version passed the label as `name`, so every chart
// looked up a column that did not exist and drew nothing at all. Rows are remapped to
// the label here instead, which makes the legend read properly and keeps the lookup true.
const props = defineProps({
	block: { type: Object, required: true },
	kind: { type: String, default: "bar" }, // bar | line | area
});

const series = computed(() => props.block.series || []);

const data = computed(() =>
	(props.block.rows || []).map((row) => {
		const out = { [props.block.x]: row[props.block.x] };
		for (const s of series.value) out[s.label || s.key] = row[s.key];
		return out;
	})
);

const config = computed(() => ({
	data: data.value,
	title: series.value.length === 1 ? series.value[0].label || "" : "",
	xAxis: { key: props.block.x, type: "category" },
	yAxis: {},
	// Horizontal bars for long category labels — AxisChart refuses swapXY on anything
	// but bars, so only the bar kind may ask for it.
	swapXY: props.kind === "bar" ? Boolean(props.block.horizontal) : false,
	series: series.value.map((s) => ({
		name: s.label || s.key,
		type: props.kind === "area" ? "area" : props.kind,
		...(props.kind !== "bar" ? { showDataPoints: data.value.length <= 12 } : {}),
	})),
}));

const empty = computed(() => !data.value.length || !series.value.length);
</script>

<template>
	<div class="rounded-lg border border-outline-gray-2 bg-surface-white p-2">
		<div v-if="empty" class="flex h-32 items-center justify-center text-base text-ink-gray-5">
			{{ __("Nothing to plot") }}
		</div>
		<div v-else class="h-60 w-full">
			<AxisChart :config="config" />
		</div>
	</div>
</template>
