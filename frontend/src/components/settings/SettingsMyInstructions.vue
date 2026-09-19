<script setup>
import { computed } from "vue";
import { __ } from "@/lib/translate";

// The user's own standing instructions.
//
// The sibling pane, "Instructions" under Site, is the system prompt: one text that
// every user on the site gets, and only a Copilot Admin can change it. This one is
// personal — it is added to this user's turns and nobody else's, so it needs no
// special role. It cannot widen what the user may see: every tool still checks
// permissions, whatever is written here.
const props = defineProps({
	data: { type: Object, required: true },
	draft: { type: Object, required: true },
});
const emit = defineEmits(["edit"]);

const MAX = 4000;

const text = computed(() =>
	"instructions" in props.draft
		? props.draft.instructions
		: props.data.user?.instructions || ""
);
const tooLong = computed(() => text.value.length > MAX);
</script>

<template>
	<div class="flex h-full flex-col">
		<div class="flex items-start justify-between gap-3 pb-3">
			<p class="text-2xs leading-relaxed text-ink-gray-5">
				{{ __("How the Copilot should work with you — the units you think in, the detail you want, the things you always care about. This is added to your own chats only; nobody else sees it.") }}
			</p>
			<button
				v-if="text.trim()"
				type="button"
				class="shrink-0 whitespace-nowrap text-2xs text-ink-gray-5 underline decoration-dotted underline-offset-2 hover:text-ink-gray-7"
				@click="emit('edit', 'instructions', '')"
			>
				{{ __("Clear") }}
			</button>
		</div>
		<textarea
			class="min-h-[18rem] flex-1 resize-none rounded-md border border-outline-gray-2 bg-surface-white p-3 font-mono text-2xs leading-relaxed text-ink-gray-8 outline-none focus:border-outline-gray-3"
			:value="text"
			:placeholder="__('e.g. Always show amounts in INR. Prefer a table over a long list. I look after the Ahmedabad territory.')"
			@input="emit('edit', 'instructions', $event.target.value)"
		></textarea>
		<div class="flex items-center justify-between pt-2 text-2xs">
			<span :class="tooLong ? 'text-ink-red-4' : 'text-ink-gray-5'">
				{{ tooLong ? __("{0} characters — the limit is {1}", [text.length, MAX]) : __("{0} characters — applies to your chats only", [text.length]) }}
			</span>
		</div>
	</div>
</template>
