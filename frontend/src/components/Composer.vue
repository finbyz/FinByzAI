<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { Button } from "@/lib/ui";
import Tip from "./Tip.vue";
import AttachmentChip from "./AttachmentChip.vue";
import Menu from "./Menu.vue";
import { useStore } from "@/store";
import { COLUMN, GUTTER } from "@/lib/layout";
import { __ } from "@/lib/translate";

// The composer, ours.
//
// Three judgements, each removing something:
//
// 1. It is docked and share the message list's column. Floating forced the list to pad
//    its bottom by a ResizeObserver-measured height, and in full screen the field ran
//    the whole 1500px while the conversation sat in a 770px column — the field looked
//    like a different app. Same column, same gutters, everything lines up.
// 2. Agent and model sit here; knowledge base stays in settings. Which agent answers
//    is a real choice when a site has more than one, so it belongs beside the field —
//    but with one agent configured there is nothing to choose, so the picker hides
//    itself rather than reading "Copilot" forever. Knowledge base is set once per
//    conversation, not per message, so it has no business on this row.
// 3. No keyboard hint. "⏎ send · ⇧⏎ new line" is decoration once you have sent one
//    message, and the shortcut is on the empty state where it is actually new.
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
	agentLogo,
	modelLabel,
	modelLogo,
	setAgent,
	setModel,
	send,
	stopRun,
	attachFiles,
	removeAttachment,
} = useStore();

// What extract_file_content can actually read (copilot/tools/files.py).
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
	agents.value.map((a) => ({
		value: a.name,
		label: a.title,
		logo: a.logo,
		hint: a.hint,
	}))
);

const modelItems = computed(() => [
	{ value: null, label: __("Default model"), hint: __("Whatever the agent uses") },
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

// Grow with the content, then scroll inside.
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

function onDrop(event) {
	event.preventDefault();
	dragging.value = false;
	if (event.dataTransfer?.files?.length) attachFiles(event.dataTransfer.files);
}
function onFilesPicked(event) {
	if (event.target.files?.length) attachFiles(event.target.files);
	event.target.value = "";
}

defineExpose({
	focus,
	setText: (value) => {
		text.value = value;
		resize();
		focus();
	},
});
</script>

<template>
	<div class="shrink-0 border-t border-outline-gray-1 bg-surface-white pb-3 pt-3" :class="GUTTER">
		<div :class="COLUMN">
			<!-- One surface: field, attachments and controls share a border. `rounded-lg`
			     and the outline tokens are frappe-ui's `outline` input variant. -->
			<div
				class="rounded-lg border bg-surface-white transition-colors"
				:class="
					dragging
						? 'border-outline-gray-3 bg-surface-gray-1'
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
					class="w-full resize-none border-0 bg-transparent px-2.5 pb-1 pt-2.5 text-p-base text-ink-gray-8 outline-none placeholder:text-ink-gray-4 disabled:cursor-default"
					@keydown="onKeydown"
					@input="resize"
				></textarea>

				<div class="flex items-center gap-0.5 px-1.5 pb-1.5">
					<Menu
						v-if="agents.length > 1"
						:items="agentItems"
						:model-value="selectedAgent"
						:disabled="locked"
						searchable
						@update:model-value="setAgent"
					>
						<template #trigger="{ toggle }">
							<button
								type="button"
								class="flex h-6 max-w-[11rem] items-center gap-1 rounded px-1.5 text-sm text-ink-gray-7 hover:bg-surface-gray-3 active:bg-surface-gray-4 disabled:opacity-50"
								:disabled="locked"
								@click="toggle"
							>
								<img
									v-if="agentLogo(selectedAgent)"
									:src="agentLogo(selectedAgent)"
									class="copilot-logo size-3.5 shrink-0"
									alt=""
								/>
								<span v-else class="lucide-bot size-3.5 shrink-0 text-ink-gray-5" aria-hidden="true"></span>
								<span class="truncate">{{ agentLabel(selectedAgent) || __("Agent") }}</span>
								<span class="lucide-chevron-down size-3 shrink-0 text-ink-gray-4" aria-hidden="true"></span>
							</button>
						</template>
					</Menu>

					<span v-if="agents.length > 1" class="text-ink-gray-3">/</span>

					<Menu
						:items="modelItems"
						:model-value="selectedModel"
						searchable
						@update:model-value="setModel"
					>
						<template #trigger="{ toggle }">
							<button
								type="button"
								class="flex h-6 max-w-[13rem] items-center gap-1 rounded px-1.5 text-sm text-ink-gray-7 hover:bg-surface-gray-3 active:bg-surface-gray-4"
								@click="toggle"
							>
								<img
									v-if="modelLogo(selectedModel)"
									:src="modelLogo(selectedModel)"
									class="copilot-logo size-3.5 shrink-0"
									alt=""
								/>
								<span v-else class="lucide-sparkles size-3.5 shrink-0 text-ink-gray-5" aria-hidden="true"></span>
								<span class="truncate">{{ modelLabel(selectedModel) || __("Default model") }}</span>
								<span class="lucide-chevron-down size-3 shrink-0 text-ink-gray-4" aria-hidden="true"></span>
							</button>
						</template>
					</Menu>

					<span class="flex-1"></span>

					<Tip :text="__('Attach a file')">
						<Button
							variant="ghost"
							icon="lucide-paperclip"
							:disabled="inputDisabled"
							@click="fileInput?.click()"
						/>
					</Tip>
					<input
						ref="fileInput"
						type="file"
						multiple
						:accept="ACCEPT"
						class="hidden"
						@change="onFilesPicked"
					/>

					<Tip v-if="sending" :text="__('Stop')">
						<Button theme="red" variant="solid" @click="stopRun">
							<template #icon><span class="size-2.5 rounded-sm bg-current"></span></template>
						</Button>
					</Tip>
					<Tip v-else :text="__('Send')">
						<Button
							variant="solid"
							theme="gray"
							icon="lucide-arrow-up"
							:disabled="!canSend"
							@click="submit"
						/>
					</Tip>
				</div>
			</div>
		</div>
	</div>
</template>
