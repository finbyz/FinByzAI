<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import SettingsChat from "./SettingsChat.vue";
import SettingsDefaults from "./SettingsDefaults.vue";
import SettingsInstructions from "./SettingsInstructions.vue";
import SettingsTools from "./SettingsTools.vue";
import * as api from "@/api/client";
import { __ } from "@/lib/translate";

// Two-pane settings: a navigation rail on the left, one section at a time on the
// right, and a single Save that writes both the conversation's own choices and (for a
// System Manager) the site defaults.
//
// Rendered inside the panel root rather than with frappe-ui's Dialog, and that is not
// a style preference. This bundle's CSS is scoped to `#copilot-root` so it cannot leak
// onto the desk, while frappe-ui's Dialog teleports its markup to `document.body` —
// outside that scope, where not one of these classes applies. The dialog opened
// correctly and was invisible. Everything the panel overlays has to live in the panel.
const props = defineProps({
	modelValue: { type: Boolean, default: false },
	store: { type: Object, required: true },
});
const emit = defineEmits(["update:modelValue"]);

const open = computed({
	get: () => props.modelValue,
	set: (v) => emit("update:modelValue", v),
});

const data = ref(null);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const active = ref("chat");
// Edited values live here until Save, so closing the dialog discards nothing silently.
const draft = ref({ conversation: {}, system: {} });

const canEditSystem = computed(() => Boolean(data.value?.can_edit_system));
// Fullscreen gets a dimmed backdrop and a centred card; the side panel gets neither.
const fullscreen = props.store.fullscreen;
// Below this the nav cannot sit beside the content; it becomes a row of tabs on top.
const narrow = computed(() => !fullscreen.value);

const groups = computed(() => [
	{
		label: __("Chat"),
		items: [
			{ key: "chat", label: __("Agent & model"), icon: "lucide-cpu" },
			{ key: "tools", label: __("Tools"), icon: "lucide-wrench" },
		],
	},
	...(canEditSystem.value
		? [
				{
					label: __("Site"),
					items: [
						{ key: "defaults", label: __("Defaults"), icon: "lucide-sliders-horizontal" },
						{ key: "instructions", label: __("Instructions"), icon: "lucide-file-text" },
					],
				},
			]
		: []),
]);

const activeItem = computed(() =>
	groups.value.flatMap((g) => g.items).find((i) => i.key === active.value)
);

watch(open, async (isOpen) => {
	if (!isOpen) return;
	loading.value = true;
	error.value = "";
	try {
		data.value = await api.loadSettings();
		draft.value = { conversation: {}, system: {} };
		if (!canEditSystem.value && ["defaults", "instructions"].includes(active.value))
			active.value = "chat";
	} catch (e) {
		error.value = e.message || __("Could not load settings");
	} finally {
		loading.value = false;
	}
});

function close() {
	open.value = false;
}

// Esc closes, but only while the modal is the thing on top.
function onKeydown(event) {
	if (event.key === "Escape" && open.value) {
		event.stopPropagation();
		close();
	}
}
onMounted(() => document.addEventListener("keydown", onKeydown, true));
onUnmounted(() => document.removeEventListener("keydown", onKeydown, true));

function edit(scope, key, value) {
	draft.value[scope] = { ...draft.value[scope], [key]: value };
}

const dirty = computed(
	() =>
		Object.keys(draft.value.conversation).length > 0 ||
		Object.keys(draft.value.system).length > 0
);

async function save(store) {
	if (!dirty.value) {
		open.value = false;
		return;
	}
	saving.value = true;
	error.value = "";
	const payload = {};
	if (Object.keys(draft.value.conversation).length && store.sessionName.value)
		payload.conversation = { name: store.sessionName.value, ...draft.value.conversation };
	if (Object.keys(draft.value.system).length && canEditSystem.value)
		payload.system = draft.value.system;

	try {
		await api.saveSettings(payload);
	} catch (e) {
		error.value = e.message || __("Could not save");
		saving.value = false;
		return;
	}

	// Reflect the picks in the composer at once; a new chat with no conversation yet
	// still applies them locally so the next turn uses them.
	const c = draft.value.conversation;
	if ("agent" in c) store.selectedAgent.value = c.agent;
	if ("model" in c) store.selectedModel.value = c.model;
	if ("knowledge_base" in c) store.selectedKnowledgeBase.value = c.knowledge_base;

	saving.value = false;
	open.value = false;
	frappe.show_alert({ message: __("Copilot settings saved"), indicator: "green" });
}
</script>

<template>
	<Transition name="copilot-modal">
		<div
			v-if="open"
			class="absolute inset-0 z-50 flex items-center justify-center"
			:class="fullscreen ? 'bg-black-overlay-200 p-4' : 'p-2'"
			@click.self="close"
		>
			<!-- Side panel: no dimming — it is only 420px wide, and darkening it makes the
			     whole panel look disabled. Fullscreen: dim, because there is a page behind. -->
			<div
				class="copilot-settings flex h-full w-full overflow-hidden rounded-xl border border-outline-gray-2 bg-surface-white"
				:class="[
					fullscreen ? 'max-h-[34rem] max-w-3xl shadow-2xl' : 'shadow-sm',
					narrow ? 'flex-col' : '',
				]"
			>
				<!-- navigation -->
				<nav
					class="shrink-0 border-outline-gray-1 bg-surface-gray-1"
					:class="
						narrow
							? 'flex gap-1 overflow-x-auto border-b px-2 py-2'
							: 'w-44 overflow-y-auto border-r p-3 sm:w-52'
					"
				>
					<div v-if="!narrow" class="px-2 pb-3 text-base font-semibold text-ink-gray-9">
						{{ __("Settings") }}
					</div>
					<template v-for="group in groups" :key="group.label">
						<div
							v-if="!narrow"
							class="px-2 pb-1 pt-3 text-2xs font-semibold uppercase tracking-wide text-ink-gray-4"
						>
							{{ group.label }}
						</div>
						<button
							v-for="item in group.items"
							:key="item.key"
							class="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs"
							:class="[
								narrow ? 'shrink-0' : 'mb-0.5 w-full',
								active === item.key
									? 'bg-surface-white font-medium text-ink-gray-9 shadow-2xs'
									: 'text-ink-gray-6 hover:bg-surface-gray-2',
							]"
							@click="active = item.key"
						>
							<span class="size-3.5 shrink-0" :class="item.icon" aria-hidden="true"></span>
							<span :class="narrow ? 'hidden sm:inline' : ''" class="truncate">{{
								item.label
							}}</span>
						</button>
					</template>
				</nav>

				<!-- content -->
				<!-- min-h-0 is load-bearing: without it this section's height is its content
				     height, the card clips the overflow, and the pane below cannot scroll. -->
				<section class="flex min-h-0 min-w-0 flex-1 flex-col">
					<header
						class="flex items-center gap-2 border-b border-outline-gray-1 px-5 py-3"
					>
						<span class="text-base font-semibold text-ink-gray-9">{{
							activeItem?.label
						}}</span>
						<span class="flex-1"></span>
						<button
							class="flex h-6 w-6 items-center justify-center rounded text-ink-gray-6 hover:bg-surface-gray-2"
							:title="__('Close')"
							@click="close"
						>
							<span class="lucide-x size-4" aria-hidden="true"></span>
						</button>
					</header>

					<div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
						<div v-if="loading" class="text-xs text-ink-gray-5">{{ __("Loading…") }}</div>
						<template v-else-if="data">
							<SettingsChat
								v-if="active === 'chat'"
								:data="data"
								:draft="draft.conversation"
								:store="store"
								@edit="(k, v) => edit('conversation', k, v)"
							/>
							<SettingsTools v-else-if="active === 'tools'" :store="store" />
							<SettingsDefaults
								v-else-if="active === 'defaults'"
								:data="data"
								:draft="draft.system"
								@edit="(k, v) => edit('system', k, v)"
							/>
							<SettingsInstructions
								v-else-if="active === 'instructions'"
								:data="data"
								:draft="draft.system"
								@edit="(k, v) => edit('system', k, v)"
							/>
						</template>
					</div>

					<footer
						class="flex items-center gap-2 border-t border-outline-gray-1 px-5 py-3"
					>
						<span v-if="error" class="text-xs text-ink-red-4">{{ error }}</span>
						<span v-else-if="dirty" class="text-xs text-ink-gray-5">{{
							__("Unsaved changes")
						}}</span>
						<span class="flex-1"></span>
						<button
							class="rounded-md px-3 py-1.5 text-xs text-ink-gray-7 hover:bg-surface-gray-2"
							@click="close"
						>
							{{ __("Cancel") }}
						</button>
						<button
							class="rounded-md bg-surface-gray-7 px-3 py-1.5 text-xs font-medium text-ink-white hover:bg-surface-gray-6 disabled:opacity-50"
							:disabled="saving"
							@click="save(store)"
						>
							{{ saving ? __("Saving…") : __("Save") }}
						</button>
					</footer>
				</section>
			</div>
		</div>
	</Transition>
</template>

<style scoped>
.copilot-modal-enter-active,
.copilot-modal-leave-active {
	transition: opacity 0.15s ease;
}
.copilot-modal-enter-from,
.copilot-modal-leave-to {
	opacity: 0;
}
</style>
