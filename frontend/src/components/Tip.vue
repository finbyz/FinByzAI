<script setup>
import { ref } from "vue";

// A tooltip that renders in place.
//
// frappe-ui's `Tooltip` portals its bubble to `document.body`, and this bundle's
// CSS is prefixed with `#copilot-root`, so a portalled bubble arrives with no
// styling at all — the same trap that made the settings dialog invisible and
// that `Menu` exists to avoid. Everything the panel overlays lives in the panel.
//
// The bubble itself is frappe-ui's: `surface-gray-7` ground, white ink, `text-xs`.
const props = defineProps({
	text: { type: String, default: "" },
	placement: { type: String, default: "top" }, // top | bottom
	delay: { type: Number, default: 400 },
});

const shown = ref(false);
let timer = 0;

function show() {
	if (!props.text) return;
	clearTimeout(timer);
	timer = setTimeout(() => (shown.value = true), props.delay);
}
function hide() {
	clearTimeout(timer);
	shown.value = false;
}
</script>

<template>
	<span
		class="relative inline-flex"
		@mouseenter="show"
		@mouseleave="hide"
		@focusin="show"
		@focusout="hide"
		@click="hide"
	>
		<slot />
		<span
			v-if="shown"
			role="tooltip"
			class="pointer-events-none absolute left-1/2 z-50 -translate-x-1/2 whitespace-nowrap rounded bg-surface-gray-7 px-2 py-1 text-xs text-ink-white shadow-xl"
			:class="placement === 'bottom' ? 'top-[calc(100%+6px)]' : 'bottom-[calc(100%+6px)]'"
			>{{ text }}</span
		>
	</span>
</template>
