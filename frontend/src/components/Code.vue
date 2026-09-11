<script setup>
import { computed, ref } from "vue";
import { __ } from "@/lib/translate";

// A block of code or raw payload: clipped to a few lines, expandable, copyable.
//
// Clipping is the point. A tool's `code` argument or a stack trace can run
// hundreds of lines, and a scrollback that has to be paged past to reach the
// answer is worse than one that says how much it is hiding.
const props = defineProps({
	code: { type: String, default: "" },
	preview: { type: Number, default: 6 },
	tone: { type: String, default: "gray" }, // gray | red
});

const lines = computed(() => props.code.split("\n"));
const clipped = computed(() => lines.value.length > props.preview);
const open = ref(false);
const shown = computed(() =>
	clipped.value && !open.value ? lines.value.slice(0, props.preview).join("\n") : props.code
);

const copied = ref(false);
let timer = 0;
async function copy() {
	try {
		await navigator.clipboard.writeText(props.code);
		copied.value = true;
		clearTimeout(timer);
		timer = setTimeout(() => (copied.value = false), 1500);
	} catch {
		// Clipboard blocked (insecure context / denied permission) — the text is
		// selectable, so there is nothing to report.
	}
}
</script>

<template>
	<div class="group/code relative">
		<pre
			class="copilot-scrollbar max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md px-2.5 py-2 font-mono text-xs leading-relaxed"
			:class="tone === 'red' ? 'bg-surface-red-1 text-ink-red-4' : 'bg-surface-gray-2 text-ink-gray-8'"
			>{{ shown }}</pre
		>
		<button
			class="absolute right-1 top-1 rounded p-1 opacity-0 transition-opacity hover:bg-surface-gray-3 focus-visible:opacity-100 group-hover/code:opacity-100"
			:aria-label="__('Copy')"
			@click="copy"
		>
			<span
				class="size-3.5"
				:class="copied ? 'lucide-check text-ink-green-3' : 'lucide-copy text-ink-gray-5'"
				aria-hidden="true"
			></span>
		</button>
		<button
			v-if="clipped"
			class="mt-1 text-xs text-ink-gray-5 hover:text-ink-gray-7"
			@click="open = !open"
		>
			{{ open ? __("Show less") : __("Show all {0} lines", [lines.length]) }}
		</button>
	</div>
</template>
