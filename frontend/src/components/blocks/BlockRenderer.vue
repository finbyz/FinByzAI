<script setup>
import ChartBlock from "./ChartBlock.vue";
import KpiBlock from "./KpiBlock.vue";
import RecordsBlock from "./RecordsBlock.vue";
import TableBlock from "./TableBlock.vue";
import { __ } from "@/lib/translate";

// One block, one renderer. `bar`, `line` and `area` share ChartBlock — the only
// difference is the series type, and keeping them in one file keeps the series
// mapping in one place.
defineProps({ block: { type: Object, required: true } });
</script>

<template>
	<div>
		<KpiBlock v-if="block.type === 'kpi'" :block="block" />
		<TableBlock v-else-if="block.type === 'table'" :block="block" />
		<ChartBlock
			v-else-if="['bar', 'line', 'area'].includes(block.type)"
			:block="block"
			:kind="block.type"
		/>
		<RecordsBlock v-else-if="block.type === 'records'" :block="block" />
		<p v-else class="text-2xs text-ink-gray-5">
			{{ __("Cannot display a {0} block.", [block.type]) }}
		</p>
	</div>
</template>
