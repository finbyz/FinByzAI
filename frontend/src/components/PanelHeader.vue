<script setup>
import { computed } from "vue";
import { Button, Tooltip } from "@/lib/ui";
import BrandMark from "./BrandMark.vue";
import Menu from "./Menu.vue";
import { useStore } from "@/store";
import { GUTTER } from "@/lib/layout";
import { __ } from "@/lib/translate";

// The header, ours.
//
// It used to carry five icon buttons side by side — history, settings, new chat,
// fullscreen, close. In a 420px panel that is a row of anonymous glyphs competing for
// the same attention, and frappe-ui's guidance is explicit: one primary action per
// surface, the rest subtle or ghost, and action clusters collapse into a single menu.
//
// So: New chat stays visible because it is the one thing people reach for mid-thought,
// Close stays because a panel must always be closable, and everything else moves into
// one overflow menu. Three controls instead of five, and the recent conversations sit
// in that menu rather than behind a clock icon nobody recognises.
const props = defineProps({ onToggleFullscreen: { type: Function, default: null } });
const emit = defineEmits(["close"]);

const store = useStore();
const { recentSessions, switchSession, newChat, fullscreen, settingsOpen } = store;

const menuItems = computed(() => {
	const items = [
		{ value: "__settings__", label: __("Settings"), icon: "lucide-settings" },
		{
			value: "__fullscreen__",
			label: fullscreen.value ? __("Exit full screen") : __("Full screen"),
			icon: fullscreen.value ? "lucide-minimize-2" : "lucide-maximize-2",
		},
	];
	const recent = recentSessions.value || [];
	if (recent.length) {
		items.push(
			...recent.slice(0, 8).map((session) => ({
				value: session.name,
				label: session.title || __("Untitled"),
				group: __("Recent"),
			}))
		);
	}
	return items;
});

function onSelect(item) {
	if (item.value === "__settings__") settingsOpen.value = true;
	else if (item.value === "__fullscreen__") props.onToggleFullscreen?.();
	else switchSession(item.value);
}
</script>

<template>
	<header
		class="flex min-h-12 shrink-0 items-center gap-2 border-b border-outline-gray-1 py-2"
		:class="GUTTER"
	>
		<BrandMark :size="18" />
		<span class="text-base font-medium text-ink-gray-8">{{ __("Copilot") }}</span>
		<span class="flex-1"></span>

		<Tooltip :text="__('New chat')">
			<Button variant="ghost" icon="lucide-plus" @click="newChat" />
		</Tooltip>

		<Menu :items="menuItems" align="right" side="bottom" searchable @select="onSelect">
			<template #trigger="{ toggle }">
				<Button variant="ghost" icon="lucide-more-horizontal" :tooltip="__('More')" @click="toggle" />
			</template>
		</Menu>

		<Tooltip :text="__('Close (Ctrl+I)')">
			<Button variant="ghost" icon="lucide-x" @click="emit('close')" />
		</Tooltip>
	</header>
</template>
