<script setup>
import { computed } from "vue";
import { NumberChart } from "@/lib/ui";
import { currencySymbol, looksMonetary } from "@/lib/format";

// frappe-ui's own KPI tile rather than markup we maintain: it draws the reading,
// the delta arrow and its colouring, and it already knows that a fall is not
// always bad.
//
// Its `prefix` is not used for the currency, though. That slot is an icon well —
// `size-4`, rendered with v-html — so "Rp" landed cramped and clipped against
// the figure. The symbol belongs in the reading itself, which is what the
// `subtitle` slot is for; the slot hands back the component's own `formatValue`,
// so "168.3M" is still frappe-ui's compaction, not ours.
const props = defineProps({ block: { type: Object, required: true } });

// The block says what it is when the tool knew — a count is a count, a SUM of a
// Currency field is money. Only when nothing was declared does the label get
// read, which is how a tile titled "Sales Invoice (filtered)" came out as "Rp 0".
const symbol = computed(() => {
	if (props.block.unit) return props.block.unit;
	if (props.block.format === "currency") return currencySymbol();
	if (props.block.format) return "";
	return looksMonetary(props.block.label) ? currencySymbol() : "";
});

const config = computed(() => ({
	title: props.block.label,
	value: Number(props.block.value) || 0,
	delta: typeof props.block.delta === "number" ? props.block.delta : undefined,
	deltaSuffix: typeof props.block.delta === "number" ? "%" : undefined,
}));
</script>

<template>
	<div class="overflow-hidden rounded-lg border border-outline-gray-2">
		<NumberChart :config="config">
			<template #subtitle="{ formatValue }">
				<div
					class="flex flex-1 items-center gap-1 truncate text-[24px] font-semibold leading-10 text-ink-gray-6 tabular-nums"
				>
					<span v-if="symbol" class="text-base font-medium text-ink-gray-5">{{
						symbol
					}}</span>
					{{ formatValue(config.value, 1, true) }}
				</div>
			</template>
		</NumberChart>
	</div>
</template>
