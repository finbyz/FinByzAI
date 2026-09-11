<script setup>
import { onMounted, ref } from "vue";
import PanelHeader from "./components/PanelHeader.vue";
import MessageList from "./components/MessageList.vue";
import Composer from "./components/Composer.vue";
import SettingsModal from "./components/settings/SettingsModal.vue";
import { useStore } from "./store";

const props = defineProps({
	onClose: { type: Function, default: null },
	onToggleFullscreen: { type: Function, default: null },
});
const store = useStore();
const { loadInitial, scrollTick, settingsOpen } = store;

const panel = ref(null);
const composer = ref(null);

// The composer is docked below the message list rather than floating over it, so
// there is no live height to measure and no chance of it covering the last message.
// That removes the ResizeObserver this used to need.
onMounted(loadInitial);
</script>

<template>
	<div
		ref="panel"
		class="copilot-panel relative flex h-full flex-col border-l border-outline-gray-2 bg-surface-white text-ink-gray-9"
	>
		<PanelHeader
			:on-toggle-fullscreen="props.onToggleFullscreen"
			@close="props.onClose && props.onClose()"
		/>
		<MessageList />
		<Composer ref="composer" />
		<SettingsModal v-model="settingsOpen" :store="store" />
	</div>
</template>
