<script setup>
import { computed } from "vue";
import { __ } from "@/lib/translate";

// The system prompt. Long, so it gets the whole pane rather than a row.
const props = defineProps({
	data: { type: Object, required: true },
	draft: { type: Object, required: true },
});
const emit = defineEmits(["edit"]);

const text = computed(() =>
	"system_prompt" in props.draft
		? props.draft.system_prompt
		: props.data.system?.system_prompt || ""
);
</script>

<template>
	<div class="flex h-full flex-col">
		<p class="pb-3 text-2xs leading-relaxed text-ink-gray-5">
			{{ __("Prepended to every turn. The agent's own tools and knowledge base are described automatically, so this is for how it should behave — tone, priorities, what to avoid.") }}
		</p>
		<textarea
			class="min-h-[18rem] flex-1 resize-none rounded-md border border-outline-gray-2 bg-surface-white p-3 font-mono text-2xs leading-relaxed text-ink-gray-8 outline-none focus:border-outline-gray-3"
			:value="text"
			@input="emit('edit', 'system_prompt', $event.target.value)"
		></textarea>
		<div class="pt-2 text-2xs text-ink-gray-5">{{ text.length }} {{ __("characters") }}</div>
	</div>
</template>
