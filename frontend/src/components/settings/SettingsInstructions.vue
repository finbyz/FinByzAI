<script setup>
import { computed } from "vue";
import { __ } from "@/lib/translate";

// The system prompt. Long, so it gets the whole pane rather than a row.
//
// Blank means "use FinByz's own default" — and that default is improved from
// time to time, in code, with no action needed here. The trap is the other
// state: fill this in, save it, and it freezes at exactly what it said that
// day. This site's own override sat untouched for weeks, silently missing
// every prompt fix shipped since — including the one that stopped the agent
// answering "hello" by going and reading customer records. "Reset to default"
// exists so getting back out of that state is as easy as getting into it.
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
const isOverridden = computed(() => Boolean(text.value.trim()));
</script>

<template>
	<div class="flex h-full flex-col">
		<div class="flex items-start justify-between gap-3 pb-3">
			<p class="text-2xs leading-relaxed text-ink-gray-5">
				{{ __("Prepended to every turn. The agent's own tools and knowledge base are described automatically, so this is for how it should behave — tone, priorities, what to avoid.") }}
				{{ __("Leave this blank to always use FinByz's own default, kept current as it improves — fill it in only to replace that permanently.") }}
			</p>
			<button
				v-if="isOverridden"
				type="button"
				class="shrink-0 whitespace-nowrap text-2xs text-ink-gray-5 underline decoration-dotted underline-offset-2 hover:text-ink-gray-7"
				@click="emit('edit', 'system_prompt', '')"
			>
				{{ __("Reset to default") }}
			</button>
		</div>
		<textarea
			class="min-h-[18rem] flex-1 resize-none rounded-md border border-outline-gray-2 bg-surface-white p-3 font-mono text-2xs leading-relaxed text-ink-gray-8 outline-none focus:border-outline-gray-3"
			:value="text"
			:placeholder="data.system_prompt_default || ''"
			@input="emit('edit', 'system_prompt', $event.target.value)"
		></textarea>
		<div class="flex items-center justify-between pt-2 text-2xs text-ink-gray-5">
			<span>{{ isOverridden ? __("{0} characters — overriding the default", [text.length]) : __("Using the built-in default") }}</span>
		</div>
	</div>
</template>
