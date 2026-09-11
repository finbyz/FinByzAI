<script setup>
import { computed } from "vue";
import Menu from "../Menu.vue";
import { __ } from "@/lib/translate";

// A select, on the panel's own anchored menu — the same one the composer uses,
// so a model shows its provider logo and its search box behaves identically in
// both places. The trigger is frappe-ui's `outline` input: `h-7`, `rounded-md`,
// `outline-gray-2` going to `outline-gray-3` on hover.
const props = defineProps({
	modelValue: { default: null },
	items: { type: Array, default: () => [] },
	placeholder: { type: String, default: "" },
});
defineEmits(["update:modelValue"]);

const current = computed(() => props.items.find((i) => i.value === props.modelValue));
</script>

<template>
	<Menu
		:items="items"
		:model-value="modelValue"
		searchable
		align="right"
		side="bottom"
		width="min-w-full max-w-[20rem]"
		@update:model-value="$emit('update:modelValue', $event)"
	>
		<template #trigger="{ toggle }">
			<button
				type="button"
				class="flex h-7 w-full items-center gap-2 rounded-md border border-outline-gray-2 bg-surface-white px-2 text-xs text-ink-gray-8 hover:border-outline-gray-3"
				:aria-label="placeholder || __('Select')"
				@click="toggle"
			>
				<img
					v-if="current?.logo"
					:src="current.logo"
					class="copilot-logo size-3.5 shrink-0"
					alt=""
				/>
				<span
					class="min-w-0 flex-1 truncate text-left"
					:class="{ 'text-ink-gray-4': !current }"
				>
					{{ current?.label || placeholder }}
				</span>
				<span
					class="lucide-chevron-down size-3 shrink-0 text-ink-gray-5"
					aria-hidden="true"
				></span>
			</button>
		</template>
	</Menu>
</template>
