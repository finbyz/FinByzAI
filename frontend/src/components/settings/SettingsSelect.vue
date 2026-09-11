<script setup>
import { computed } from "vue";
import { FeatherIcon } from "@/lib/ui";
import PanelDropdown from "../PanelDropdown.vue";

// The same dropdown the composer uses, so a model shows its provider logo here too.
const props = defineProps({
	modelValue: { default: null },
	items: { type: Array, default: () => [] },
	placeholder: { type: String, default: "" },
});
defineEmits(["update:modelValue"]);

const current = computed(() => props.items.find((i) => i.value === props.modelValue));
</script>

<template>
	<PanelDropdown
		:items="items"
		:model-value="modelValue"
		searchable
		align="right"
		@update:model-value="$emit('update:modelValue', $event)"
	>
		<template #trigger="{ toggle }">
			<button
				class="flex h-7 w-full items-center gap-2 rounded-md border border-outline-gray-2 bg-surface-white px-2 text-xs text-ink-gray-8 hover:border-outline-gray-3"
				@click="toggle"
			>
				<img v-if="current?.logo" :src="current.logo" class="copilot-logo h-3.5 w-3.5 shrink-0" alt="" />
				<span class="min-w-0 flex-1 truncate text-left" :class="{ 'text-ink-gray-4': !current }">
					{{ current?.label || placeholder }}
				</span>
				<FeatherIcon name="chevron-down" class="h-3 w-3 shrink-0 text-ink-gray-5" />
			</button>
		</template>
	</PanelDropdown>
</template>
