<script setup>
// One labelled setting: label + help text on the left, control on the right. Keeps
// every pane visually identical without repeating the markup.
defineProps({
	label: { type: String, required: true },
	description: { type: String, default: "" },
});
</script>

<template>
	<div class="flex items-start gap-4 border-b border-outline-gray-1 py-3 last:border-0">
		<div class="min-w-0 flex-1">
			<div class="text-xs font-medium text-ink-gray-8">{{ label }}</div>
			<p v-if="description" class="mt-0.5 text-2xs leading-relaxed text-ink-gray-5">
				{{ description }}
			</p>
		</div>
		<div class="w-56 shrink-0"><slot /></div>
	</div>
</template>
