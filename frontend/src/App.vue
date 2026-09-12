<script setup>
import { onMounted, ref, watch } from "vue";
import PanelHeader from "./components/PanelHeader.vue";
import MessageList from "./components/MessageList.vue";
import Composer from "./components/Composer.vue";
import ChatHistorySidebar from "./components/ChatHistorySidebar.vue";
import SettingsModal from "./components/settings/SettingsModal.vue";
import { useStore } from "./store";

const props = defineProps({
	onClose: { type: Function, default: null },
	onToggleFullscreen: { type: Function, default: null },
});
const store = useStore();
const { loadInitial, settingsOpen, sidebarOpen, fullscreen } = store;

const composer = ref(null);

// The composer is docked below the message list rather than floating over it, so
// there is no live height to measure and no chance of it covering the last
// message. That removes the ResizeObserver this used to need.
onMounted(loadInitial);

// Full screen has room for two panes, so the conversation list comes with it;
// the side panel does not, so it goes away again. One rule, applied on load too
// (`immediate`), and no memory of a state the user can't see.
watch(fullscreen, (wide) => (sidebarOpen.value = wide), { immediate: true });
</script>

<template>
	<div
		class="copilot-panel relative flex h-full flex-col overflow-hidden border-l border-outline-gray-2 bg-surface-white text-ink-gray-9"
	>
		<!-- The header stays above both panes: it carries the toggle, so the list
		     can never cover the only way back out of it. -->
		<PanelHeader
			:on-toggle-fullscreen="props.onToggleFullscreen"
			@close="props.onClose && props.onClose()"
		/>

		<div class="relative flex min-h-0 flex-1">
			<!-- Two panes in full screen. The side panel has no room for two, so
			     the same list covers the conversation until something is picked. -->
			<ChatHistorySidebar
				v-if="sidebarOpen"
				:class="fullscreen ? 'w-60 shrink-0' : 'absolute inset-0 z-30'"
				@pick="fullscreen || (sidebarOpen = false)"
			/>

			<div class="flex min-w-0 flex-1 flex-col">
				<MessageList />
				<Composer ref="composer" />
			</div>
		</div>

		<SettingsModal v-model="settingsOpen" :store="store" />
	</div>
</template>
