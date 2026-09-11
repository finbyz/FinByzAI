<script setup>
import { computed } from "vue";
import { NumberChart } from "@/lib/ui";

// frappe-ui's own KPI tile rather than markup we maintain: it draws the reading, the
// delta arrow and its colouring, and it already knows that a fall is not always bad.
//
// Currency comes from the site, through NumberChart's `prefix`. A tile that renders a
// bare 19825712.8 next to the desk's ₹ 1,98,25,712.80 reads as a different product.
const props = defineProps({ block: { type: Object, required: true } });

const CURRENCY_WORDS = /amount|total|price|rate|value|revenue|spend|balance|outstanding|capital|margin|profit/i;

const config = computed(() => {
	const looksMonetary = CURRENCY_WORDS.test(props.block.label || "");
	return {
		title: props.block.label,
		value: Number(props.block.value) || 0,
		prefix: props.block.unit || (looksMonetary ? currencySymbol() : ""),
		delta: typeof props.block.delta === "number" ? props.block.delta : undefined,
		deltaSuffix: typeof props.block.delta === "number" ? "%" : undefined,
	};
});

function currencySymbol() {
	const code = frappe.boot?.sysdefaults?.currency;
	if (!code) return "";
	try {
		return frappe.model?.get_value?.("Currency", code, "symbol") || `${code} `;
	} catch {
		return `${code} `;
	}
}
</script>

<template>
	<div class="overflow-hidden rounded-lg border border-outline-gray-2">
		<NumberChart :config="config" />
	</div>
</template>
