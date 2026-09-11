<script setup>
import { nextTick, onMounted, ref, watch } from "vue";

// Live label: cross-fades on text change, shimmers while active, animates its width
// so a trailing chevron glides. Adapted from frappe/flow#9.
const props = defineProps({
	text: { type: String, default: "" },
	active: { type: Boolean, default: false },
});

const sizer = ref(null);
const width = ref("auto");

async function measure() {
	await nextTick();
	if (sizer.value) width.value = `${Math.ceil(sizer.value.getBoundingClientRect().width)}px`;
}

watch(() => props.text, measure);
onMounted(measure);
</script>

<template>
	<span
		class="copilot-label relative inline-grid items-center overflow-hidden align-bottom"
		:style="{ width }"
	>
		<span
			ref="sizer"
			class="invisible col-start-1 row-start-1 w-fit justify-self-start whitespace-nowrap"
			aria-hidden="true"
			>{{ text }}</span
		>
		<Transition name="copilot-label">
			<span
				:key="text"
				class="col-start-1 row-start-1 whitespace-nowrap"
				:class="active ? 'copilot-shimmer-text' : ''"
				>{{ text }}</span
			>
		</Transition>
	</span>
</template>

<style scoped>
.copilot-label {
	transition: width 0.25s ease;
}
.copilot-label-enter-active,
.copilot-label-leave-active {
	transition: opacity 0.25s ease;
}
/* Leaving label goes absolute so it never affects the measured/flow width. */
.copilot-label-leave-active {
	position: absolute;
	left: 0;
	top: 0;
}
.copilot-label-enter-from,
.copilot-label-leave-to {
	opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
	.copilot-label,
	.copilot-label-enter-active,
	.copilot-label-leave-active {
		transition: none;
	}
}
</style>
