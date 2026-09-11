<script setup>
// A dropdown trigger that is indistinguishable from frappe-ui's ghost Button.
//
// frappe-ui's Button cannot be a dropdown trigger with a logo, a label and a chevron,
// so this is our own — but every measurement is lifted from Button.vue rather than
// invented, so the composer row reads as one set of controls:
//
//   xs   h-6  text-sm   px-1.5  rounded          (Button.vue sizeClasses)
//   sm   h-7  text-base px-2    rounded
//   ghost  text-ink-gray-8 bg-transparent hover:bg-surface-gray-3 active:bg-surface-gray-4
//
// If frappe-ui changes its scale, change it here too — do not guess new numbers.
import { computed } from "vue";
import { FeatherIcon } from "@/lib/ui";

const props = defineProps({
	label: { type: String, default: "" },
	logo: { type: String, default: null },
	icon: { type: String, default: null },
	size: { type: String, default: "xs" }, // xs | sm
	muted: { type: Boolean, default: false },
	disabled: { type: Boolean, default: false },
	chevron: { type: Boolean, default: true },
});

const metrics = computed(
	() =>
		({
			xs: "h-6 px-1.5 text-sm rounded gap-1",
			sm: "h-7 px-2 text-base rounded gap-1.5",
		})[props.size] || "h-6 px-1.5 text-sm rounded gap-1"
);

const glyph = computed(() => (props.size === "sm" ? "size-4" : "size-3.5"));
</script>

<template>
	<button
		type="button"
		class="flex max-w-[12rem] items-center bg-transparent transition-colors disabled:cursor-default disabled:opacity-50"
		:class="[
			metrics,
			disabled ? '' : 'hover:bg-surface-gray-3 active:bg-surface-gray-4',
			muted ? 'text-ink-gray-5' : 'text-ink-gray-8',
		]"
		:disabled="disabled"
	>
		<img v-if="logo" :src="logo" class="copilot-logo shrink-0" :class="glyph" alt="" />
		<FeatherIcon v-else-if="icon" :name="icon" class="shrink-0 text-ink-gray-6" :class="glyph" />
		<span class="truncate font-medium">{{ label }}</span>
		<FeatherIcon
			v-if="chevron && !disabled"
			name="chevron-down"
			class="size-3 shrink-0 text-ink-gray-5"
		/>
	</button>
</template>
