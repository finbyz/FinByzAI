<script setup>
import { computed } from "vue";
import { Button } from "@/lib/ui";
import Tip from "./Tip.vue";
import BrandMark from "./BrandMark.vue";
import Menu from "./Menu.vue";
import { useStore } from "@/store";
import { GUTTER } from "@/lib/layout";
import { __ } from "@/lib/translate";

// The header.
//
// It used to carry five icon buttons side by side — history, settings, new chat,
// fullscreen, close. In a 420px panel that is a row of anonymous glyphs
// competing for the same attention, and frappe-ui's guidance is explicit: one
// primary action per surface, the rest ghost, and action clusters collapse into
// a single menu.
//
// So: the conversation list gets a toggle (it is a place you go, not an action),
// New chat stays because it is what people reach for mid-thought, Close stays
// because a panel must always be closable, and Settings and full screen live in
// the overflow menu.
const props = defineProps({ onToggleFullscreen: { type: Function, default: null } });
const emit = defineEmits(["close"]);

const store = useStore();
const { newChat, fullscreen, settingsOpen, sidebarOpen, toggleSidebar } = store;

const menuItems = computed(() => [
	{ value: "__settings__", label: __("Settings"), icon: "lucide-settings" },
	{
		value: "__fullscreen__",
		label: fullscreen.value ? __("Exit full screen") : __("Full screen"),
		icon: fullscreen.value ? "lucide-minimize-2" : "lucide-maximize-2",
	},
]);

function onSelect(item) {
	if (item.value === "__settings__") settingsOpen.value = true;
	else if (item.value === "__fullscreen__") props.onToggleFullscreen?.();
}
</script>

<template>
	<header
		class="flex min-h-12 shrink-0 items-center gap-2 border-b border-outline-gray-1 py-2"
		:class="GUTTER"
	>
		<Tip :text="sidebarOpen ? __('Hide conversations') : __('Show conversations')">
			<Button variant="ghost" icon="lucide-panel-left" @click="toggleSidebar" />
		</Tip>

		<BrandMark :size="18" />
		<span class="text-base font-medium text-ink-gray-8">{{ __("Copilot") }}</span>
		<span class="flex-1"></span>

		<Tip :text="__('New chat')">
			<Button variant="ghost" icon="lucide-plus" @click="newChat" />
		</Tip>

		<Menu :items="menuItems" align="right" side="bottom" @select="onSelect">
			<template #trigger="{ toggle }">
				<Tip :text="__('More')">
					<Button variant="ghost" icon="lucide-more-horizontal" @click="toggle" />
				</Tip>
			</template>
		</Menu>

		<Tip :text="__('Close (Ctrl+I)')">
			<Button variant="ghost" icon="lucide-x" @click="emit('close')" />
		</Tip>
	</header>
</template>
