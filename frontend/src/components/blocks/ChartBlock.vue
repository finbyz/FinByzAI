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

// The measure's name goes on the y axis, which is where frappe-ui draws it — as
// "↑ Revenue". Leaving it out is what produced the "↑ undefined" on every chart
// (eChartOptions.ts sets `name: `↑ ${config.yAxis.title}`` with no guard), and
// when there is no single measure the arrow is suppressed rather than left bare.
const measure = computed(() =>
	series.value.length === 1 ? series.value[0].label || series.value[0].key || "" : ""
);

const config = computed(() => ({
	data: data.value,
	xAxis: { key: props.block.x, type: "category" },
	yAxis: measure.value ? { title: measure.value } : { echartOptions: { name: "" } },
	// Horizontal bars for long category labels — AxisChart refuses swapXY on anything
	// but bars, so only the bar kind may ask for it.
	swapXY: props.kind === "bar" ? Boolean(props.block.horizontal) : false,
	series: series.value.map((s) => ({
		name: s.label || s.key,
		type: props.kind === "area" ? "area" : props.kind,
		...(props.kind !== "bar" ? { showDataPoints: data.value.length <= 12 } : {}),
	})),
}));

// A series whose key is in none of the rows plots nothing, and AxisChart answers a
// config it cannot use with a bare red "Error". Say so ourselves instead.
const plottable = computed(() =>
	data.value.some((row) => series.value.some((s) => typeof row[s.label || s.key] === "number"))
);
const empty = computed(() => !data.value.length || !series.value.length || !plottable.value);

// frappe-ui's ECharts wrapper carries `min-h-[300px]` on its own div, so a
// shorter box does not shrink the chart — it overflows, and the chart paints
// over the block below it. That was the overlap in the panel. So 300px is the
// floor, the card clips anything beyond it, and a horizontal bar chart grows
// with its bars instead of squeezing ten item names into one frame.
const MIN_HEIGHT = 19; // rem, just over the 300px the chart insists on
const height = computed(() => {
	if (!config.value.swapXY) return `${MIN_HEIGHT}rem`;
	return `${Math.max(MIN_HEIGHT, data.value.length * 1.75 + 4)}rem`;
});
</script>

<template>
	<div class="overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-white p-2">
		<div v-if="empty" class="flex h-32 items-center justify-center text-base text-ink-gray-5">
			{{ __("Nothing to plot") }}
		</div>
		<div v-else class="w-full" :style="{ height }">
			<AxisChart :config="config" />
		</div>
	</div>
</template>
