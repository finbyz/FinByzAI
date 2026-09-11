<script setup>
import { ref, computed, watch, nextTick } from "vue";
import PanelDropdown from "./PanelDropdown.vue";
import AttachmentChip from "./AttachmentChip.vue";
import { Button, FeatherIcon } from "@/lib/ui";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

const {
	agents,
	models,
	selectedAgent,
	selectedModel,
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
	setAgent,
	setModel,
	knowledgeBases,
	selectedKnowledgeBase,
	knowledgeLabel,
	setKnowledgeBase,
	modelLogo,
	agentLogo,
	send,
	stopRun,
	attachFiles,
	removeAttachment,
} = useStore();

// What extract_file_content can actually read (see copilot/tools/files.py).
const ACCEPT = computed(() =>
	[
		"pdf", "docx", "doc", "pptx", "xlsx", "xlsm", "xls", "csv",
		"html", "htm", "txt", "md", "json", "xml",
		"png", "jpg", "jpeg", "gif", "webp",
	]
		.map((ext) => `.${ext}`)
		.join(",")
);

const text = ref("");
const el = ref(null);
const fileInput = ref(null);
const dragging = ref(false);

const inputDisabled = computed(
	() => !loaded.value || sending.value || paused.value || needsSetup.value
);

const agentItems = computed(() =>
	agents.value.map((a) => ({ value: a.name, label: a.title, logo: a.logo, hint: a.hint }))
);
const knowledgeItems = computed(() => [
	{ label: __("No knowledge base"), value: null },
	...knowledgeBases.value.map((k) => ({
		label: k.title,
		value: k.name,
		hint: k.status && k.status !== "Completed" ? k.status : undefined,
	})),
	{ label: __("Create a knowledge base…"), value: "__new__" },
]);

const modelItems = computed(() => [
	{ label: __("Agent's default"), value: null },
	...models.value.map((m) => ({
		label: m.title,
		value: m.name,
		logo: m.logo,
		group: m.provider,
		hint: [m.vision ? __("vision") : null, m.reasoning ? __("reasoning") : null]
			.filter(Boolean)
			.join(" · ") || undefined,
	})),
]);

const canSend = computed(() => text.value.trim() && !inputDisabled.value && !uploading.value);
const placeholder = computed(() => {
	if (!loaded.value) return __("Loading…");
	if (needsSetup.value) return __("Setup required…");
	return __("Ask {0}…", [agentLabel(selectedAgent.value)]);
});

// With no knowledge base on the site, the picker's only entry opens the doctype so
// the user can make one instead of staring at an empty menu.
function pickKnowledgeBase(value) {
	if (value === "__new__") {
		frappe.new_doc("Knowledge Base");
		return;
	}
	setKnowledgeBase(value);
}

function submit() {
	if (!canSend.value) return;
	send(text.value);
	text.value = "";
	nextTick(resize);
}

function pickFiles() {
	fileInput.value?.click();
}

function onFilesPicked(e) {
	attachFiles(e.target.files);
	e.target.value = ""; // allow re-picking the same file
}

function onDragOver(e) {
	if (inputDisabled.value) return;
	e.preventDefault();
	dragging.value = true;
}

function onDragLeave(e) {
	if (e.currentTarget.contains(e.relatedTarget)) return;
	dragging.value = false;
}

function onDrop(e) {
	e.preventDefault();
	dragging.value = false;
	if (inputDisabled.value) return;
	attachFiles(e.dataTransfer.files);
}

function onKeydown(e) {
	if (e.key === "Enter" && !e.shiftKey) {
		e.preventDefault();
		submit();
	}
}

function resize() {
	const t = el.value;
	if (!t) return;
	t.style.height = "auto";
	t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
}

watch(focusTick, () => nextTick(() => el.value?.focus()));
</script>

<template>
	<div
		class="flow-composer absolute inset-x-5 bottom-3.5 mx-auto flex max-w-3xl flex-col gap-1.5 rounded-xl border bg-surface-white px-2.5 py-2 shadow-sm transition-[border-color,background-color] focus-within:border-outline-gray-3"
		:class="dragging ? 'border-outline-gray-4 bg-surface-gray-1' : 'border-outline-gray-2'"
		@dragover="onDragOver"
		@dragleave="onDragLeave"
		@drop="onDrop"
	>
		<div v-if="attachments.length" class="flex flex-wrap gap-1.5">
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
			class="max-h-40 min-h-[50px] w-full resize-none border-0 bg-transparent text-base font-normal leading-relaxed text-ink-gray-9 outline-none placeholder:text-ink-gray-4"
			@keydown="onKeydown"
			@input="resize"
		></textarea>

		<div class="flex items-center gap-1.5">
			<!-- attach -->
			<button
				class="flex h-6 w-6 items-center justify-center rounded text-ink-gray-6 hover:bg-surface-gray-2 disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"
				:disabled="inputDisabled"
				:title="__('Attach file')"
				@click="pickFiles"
			>
				<FeatherIcon name="paperclip" class="h-3.5 w-3.5" />
			</button>
			<input
				ref="fileInput"
				type="file"
				multiple
				:accept="ACCEPT"
				class="hidden"
				@change="onFilesPicked"
			/>

			<!-- agent -->
			<PanelDropdown
				:items="agentItems"
				:model-value="selectedAgent"
				:disabled="locked"
				searchable
				@update:model-value="setAgent"
			>
				<template #trigger="{ toggle }">
					<button
						class="flex h-6 items-center gap-1 rounded px-1.5 text-[12.5px] text-ink-gray-6 hover:bg-surface-gray-2 disabled:cursor-default disabled:hover:bg-transparent"
						:disabled="locked"
						:title="__('Agent')"
						@click="toggle"
					>
						<img
							v-if="agentLogo(selectedAgent)"
							:src="agentLogo(selectedAgent)"
							class="copilot-logo h-3 w-3 shrink-0"
							alt=""
						/>
						<span class="font-medium text-ink-gray-8">{{
							agentLabel(selectedAgent)
						}}</span>
						<FeatherIcon v-if="!locked" name="chevron-down" class="h-3 w-3" />
					</button>
				</template>
			</PanelDropdown>

			<span class="text-ink-gray-3">/</span>

			<!-- model -->
			<PanelDropdown
				:items="modelItems"
				:model-value="selectedModel"
				searchable
				@update:model-value="setModel"
			>
				<template #trigger="{ toggle }">
					<button
						class="flex h-6 items-center gap-1 rounded px-1.5 text-[12.5px] text-ink-gray-6 hover:bg-surface-gray-2"
						:title="__('Model')"
						@click="toggle"
					>
						<img
							v-if="modelLogo(selectedModel)"
							:src="modelLogo(selectedModel)"
							class="copilot-logo h-3 w-3 shrink-0"
							alt=""
						/>
						<span class="font-medium text-ink-gray-8">
							{{ modelLabel(selectedModel) || __("Default") }}
						</span>
						<FeatherIcon name="chevron-down" class="h-3 w-3" />
					</button>
				</template>
			</PanelDropdown>

			<template>
				<span class="text-ink-gray-3">/</span>
				<PanelDropdown
					:items="knowledgeItems"
					:model-value="selectedKnowledgeBase"
					searchable
					@update:model-value="pickKnowledgeBase"
				>
					<template #trigger="{ toggle }">
						<button
							class="flex h-6 items-center gap-1 rounded px-1.5 text-[12.5px] text-ink-gray-6 hover:bg-surface-gray-2"
							:title="__('Knowledge base')"
							@click="toggle"
						>
							<FeatherIcon name="book-open" class="h-3 w-3" />
							<span class="font-medium text-ink-gray-8">{{
								knowledgeLabel(selectedKnowledgeBase) ||
								(knowledgeBases.length ? __("No KB") : __("Add KB"))
							}}</span>
							<FeatherIcon name="chevron-down" class="h-3 w-3" />
						</button>
					</template>
				</PanelDropdown>
			</template>

			<span class="flex-1"></span>

			<Button
				v-if="sending"
				theme="red"
				class="!bg-surface-red-3 hover:!bg-surface-red-4"
				:title="__('Stop')"
				@click="stopRun"
			>
				<template #icon
					><span class="h-2.5 w-2.5 rounded-[2px] bg-current"></span
				></template>
			</Button>
			<Button
				v-else
				variant="solid"
				:disabled="!canSend"
				:title="__('Send')"
				@click="submit"
			>
				<template #icon><FeatherIcon name="arrow-up" class="h-4 w-4" /></template>
			</Button>
		</div>
	</div>
</template>
