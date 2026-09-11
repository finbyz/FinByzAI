<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { Button, FeatherIcon, Tooltip } from "@/lib/ui";
import AttachmentChip from "./AttachmentChip.vue";
import ControlPill from "./ControlPill.vue";
import PanelDropdown from "./PanelDropdown.vue";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

// The composer, ours.
//
// Two deliberate departures from what we had:
//
// 1. It is docked, not floating. A floating card means the message list has to pad its
//    bottom by the composer's live height, measured with a ResizeObserver, and a grown
//    composer still covers the last message for a frame. Docking removes the entire
//    problem — the list ends where the composer begins.
// 2. The controls sit inside the field, on its bottom edge, so the input is one
//    surface rather than a textarea with a toolbar underneath it.
//
// Everything visible is either a frappe-ui component or built to frappe-ui's own
// metrics (see ControlPill.vue).
const {
	agents,
	models,
	knowledgeBases,
	selectedAgent,
	selectedModel,
	selectedKnowledgeBase,
	attachments,
	sending,
	paused,
	locked,
	loaded,
	needsSetup,
	uploading,
	focusTick,
	agentLabel,
	modelLabel,
	knowledgeLabel,
	agentLogo,
	modelLogo,
	setAgent,
	setModel,
	setKnowledgeBase,
	send,
	stopRun,
	attachFiles,
	removeAttachment,
} = useStore();

// What extract_file_content can actually read (see copilot/tools/files.py).
const ACCEPT = [
	"pdf", "docx", "doc", "pptx", "xlsx", "xlsm", "xls", "csv",
	"html", "htm", "txt", "md", "json", "xml",
	"png", "jpg", "jpeg", "gif", "webp",
]
	.map((ext) => `.${ext}`)
	.join(",");

const text = ref("");
const el = ref(null);
const fileInput = ref(null);
const dragging = ref(false);

const inputDisabled = computed(() => !loaded.value || needsSetup.value || locked.value);
const canSend = computed(() => text.value.trim() && !inputDisabled.value && !uploading.value);

const placeholder = computed(() => {
	if (!loaded.value) return __("Loading…");
	if (needsSetup.value) return __("Finish setup to start…");
	if (paused.value) return __("Answer above to continue…");
	return __("Ask anything about your data…");
});

const agentItems = computed(() =>
	agents.value.map((a) => ({ value: a.name, label: a.title, logo: a.logo, hint: a.hint }))
);
const modelItems = computed(() => [
	{ value: null, label: __("Agent's default") },
	...models.value.map((m) => ({
		value: m.name,
		label: m.title,
		logo: m.logo,
		group: m.provider,
		hint: [m.vision ? __("vision") : null, m.reasoning ? __("reasoning") : null]
			.filter(Boolean)
			.join(" · ") || undefined,
	})),
]);
const knowledgeItems = computed(() => [
	{ value: null, label: __("No knowledge base") },
	...knowledgeBases.value.map((k) => ({
		value: k.name,
		label: k.title,
		hint: k.status && k.status !== "Completed" ? k.status : undefined,
	})),
	{ value: "__new__", label: __("Create a knowledge base…") },
]);

function pickKnowledgeBase(value) {
	if (value === "__new__") return frappe.new_doc("Knowledge Base");
	setKnowledgeBase(value);
}

function submit() {
	if (!canSend.value) return;
	send(text.value);
	text.value = "";
	resize();
}

function onKeydown(event) {
	if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
		event.preventDefault();
		submit();
	}
}

// Grow with the content up to a ceiling, then scroll inside.
function resize() {
	const node = el.value;
	if (!node) return;
	node.style.height = "auto";
	node.style.height = `${Math.min(node.scrollHeight, 168)}px`;
}

function focus() {
	nextTick(() => el.value?.focus());
}
watch(focusTick, focus);

// Drag a file anywhere onto the field.
function onDrop(event) {
	event.preventDefault();
	dragging.value = false;
	if (event.dataTransfer?.files?.length) attachFiles(event.dataTransfer.files);
}
function pickFiles() {
	fileInput.value?.click();
}
function onFilesPicked(event) {
	if (event.target.files?.length) attachFiles(event.target.files);
	event.target.value = "";
}

defineExpose({ focus, setText: (value) => { text.value = value; resize(); focus(); } });
</script>

<template>
	<div class="shrink-0 border-t border-outline-gray-2 bg-surface-white px-3 py-3">
		<!-- One surface: the field, its attachments and its controls share a border,
		     styled as frappe-ui's `outline` variant. -->
		<div
			class="rounded-lg border bg-surface-white transition-colors"
			:class="
				dragging
					? 'border-outline-gray-4 bg-surface-gray-1'
					: 'border-outline-gray-2 focus-within:border-outline-gray-3'
			"
			@dragover.prevent="dragging = true"
			@dragleave.prevent="dragging = false"
			@drop="onDrop"
		>
			<div v-if="attachments.length" class="flex flex-wrap gap-1.5 px-2.5 pt-2.5">
				<AttachmentChip
					v-for="a in attachments"
					:key="a.uid"
					:file-name="a.file_name"
					:file-size="a.file_size"
					:status="a.status"
					:error="a.error"
					removable
					@remove="removeAttachment(a.uid)"
				/>
			</div>

			<textarea
				ref="el"
				v-model="text"
				rows="1"
				:placeholder="placeholder"
				:disabled="inputDisabled"
				class="max-h-42 w-full resize-none border-0 bg-transparent px-2.5 pb-1 pt-2.5 text-base leading-relaxed text-ink-gray-9 outline-none placeholder:text-ink-gray-4 disabled:cursor-default"
				@keydown="onKeydown"
				@input="resize"
			></textarea>

			<!-- controls, on the field's bottom edge -->
			<div class="flex items-center gap-1 px-2 pb-2">
				<PanelDropdown
					v-if="agents.length > 1"
					:items="agentItems"
					:model-value="selectedAgent"
					:disabled="locked"
					searchable
					@update:model-value="setAgent"
				>
					<template #trigger="{ toggle }">
						<ControlPill
							:label="agentLabel(selectedAgent) || __('Agent')"
							:logo="agentLogo(selectedAgent)"
							icon="cpu"
							:disabled="locked"
							@click="toggle"
						/>
					</template>
				</PanelDropdown>

				<PanelDropdown
					:items="modelItems"
					:model-value="selectedModel"
					searchable
					@update:model-value="setModel"
				>
					<template #trigger="{ toggle }">
						<ControlPill
							:label="modelLabel(selectedModel) || __('Default model')"
							:logo="modelLogo(selectedModel)"
							icon="zap"
							:muted="!selectedModel"
							@click="toggle"
						/>
					</template>
				</PanelDropdown>

				<PanelDropdown
					:items="knowledgeItems"
					:model-value="selectedKnowledgeBase"
					searchable
					@update:model-value="pickKnowledgeBase"
				>
					<template #trigger="{ toggle }">
						<ControlPill
							:label="knowledgeLabel(selectedKnowledgeBase) || __('No KB')"
							icon="book-open"
							:muted="!selectedKnowledgeBase"
							@click="toggle"
						/>
					</template>
				</PanelDropdown>

				<span class="flex-1"></span>

				<!-- Only while typing: a hint nobody needs to read twice. -->
				<span
					v-if="text.length > 2 && !sending"
					class="hidden pr-1 text-2xs text-ink-gray-4 sm:inline"
				>
					{{ __("⏎ send · ⇧⏎ new line") }}
				</span>

				<Tooltip :text="__('Attach a file')">
					<Button variant="ghost" :disabled="inputDisabled" @click="pickFiles">
						<template #icon><FeatherIcon name="paperclip" class="size-4" /></template>
					</Button>
				</Tooltip>
				<input
					ref="fileInput"
					type="file"
					multiple
					:accept="ACCEPT"
					class="hidden"
					@change="onFilesPicked"
				/>

				<Tooltip v-if="sending" :text="__('Stop')">
					<Button theme="red" variant="solid" @click="stopRun">
						<template #icon><span class="size-2.5 rounded-sm bg-current"></span></template>
					</Button>
				</Tooltip>
				<Button v-else variant="solid" :disabled="!canSend" :tooltip="__('Send')" @click="submit">
					<template #icon><FeatherIcon name="arrow-up" class="size-4" /></template>
				</Button>
			</div>
		</div>
	</div>
</template>
