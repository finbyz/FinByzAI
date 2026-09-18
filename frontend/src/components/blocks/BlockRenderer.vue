<script setup>
import ChartBlock from "./ChartBlock.vue";
import KpiBlock from "./KpiBlock.vue";
import RecordsBlock from "./RecordsBlock.vue";
import SourcesBlock from "./SourcesBlock.vue";
import TableBlock from "./TableBlock.vue";
import { __ } from "@/lib/translate";

// One block, one renderer. `bar`, `line` and `area` share ChartBlock — the only
// difference is the series type, and keeping them in one file keeps the series
// mapping in one place.
//
// `sources` is handled here for a lone tool call with no message around it
// (a reload, a future use of the block outside chat), but `AssistantMessage.vue`
// normally intercepts it before it ever reaches this component — a bibliography
// belongs after the paragraph it supports, not wherever mid-turn the tool call
// that produced it happened to run.
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
		<SourcesBlock v-else-if="block.type === 'sources'" :items="block.items" />
		<p v-else class="text-2xs text-ink-gray-5">
			{{ __("Cannot display a {0} block.", [block.type]) }}
		</p>
	</div>
</template>
