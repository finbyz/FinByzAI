<script setup>
import { computed, nextTick, ref } from "vue";
import { Button } from "@/lib/ui";
import ToolArgs from "./ToolArgs.vue";
import { hasArgs, isDestructive, parseArgs } from "@/lib/tools";
import { __ } from "@/lib/translate";

// The one place in the panel where the user decides something.
//
// It reads top to bottom as a decision: what will happen, in the sentence the
// backend already writes for it (runner._summary — so a tool added server-side
// asks in its own words); then exactly what it will be done to, in a quiet
// inset; then the choice. The version before this looked like every other card
// in the scrollback, which is the wrong signal for the only element that can
// change data.
//
// frappe-ui's rule is one primary action per surface, so `Approve` is the solid
// button and everything else is subtle — and on a destructive call the solid
// button turns red rather than the whole card, because the card is not the
// dangerous part.
const props = defineProps({
	question: { type: Object, required: true },
	tool: { type: Object, default: null },
});
const emit = defineEmits(["answer"]);

const other = ref(null);

// An approval names a tool; a free-text ask from the model doesn't.
const isTool = computed(() => Boolean(props.tool));
const asking = computed(() => props.question.kind === "question");
const destructive = computed(() => isTool.value && isDestructive(props.tool.name));

// The server's summary is the title. A free-text ask puts its first paragraph in
// the title and keeps the rest as body.
const paragraphs = computed(() => (props.question.prompt || "").split("\n\n"));
const title = computed(() => paragraphs.value[0].trim() || __("Confirm this step"));
const body = computed(() => (isTool.value ? "" : paragraphs.value.slice(1).join("\n\n").trim()));

// `execute`'s description became the title, so it would only repeat below.
const args = computed(() => {
	if (!isTool.value) return null;
	if (props.tool.name !== "execute") return props.tool.arguments;
	const { description, ...rest } = parseArgs(props.tool.arguments);
	return rest;
});
const showArgs = computed(() => hasArgs(args.value));

const answered = computed(() => props.question._answer !== undefined);

// A question the model wrote with no options to pick from can only be answered
// in words, so the box is already open rather than behind a link.
const options = computed(() => props.question.options || []);
const typing = computed(() => props.question._showOther || !options.value.length);

// Options are stable tokens the backend matches on; only the display text is
// translated. Anything the model authored passes through as written.
const LABELS = { Approve: () => __("Approve"), Deny: () => __("Reject") };
const label = (option) => (LABELS[option] ? LABELS[option]() : option);
const theme = (option) => (option === "Approve" && destructive.value ? "red" : "gray");
const variant = (option) => (option === "Approve" ? "solid" : "subtle");

function showOther() {
	props.question._showOther = true;
	nextTick(() => other.value?.focus());
}
function cancelOther() {
	props.question._showOther = false;
	props.question._otherText = "";
}
function sendOther() {
	const text = props.question._otherText?.trim();
	if (text) emit("answer", text);
}
</script>

<template>
	<!-- Resolved: one line in the scrollback. A decision already taken has no
	     business occupying a card. -->
	<div v-if="answered" class="flex items-center gap-1.5 text-sm text-ink-gray-5">
		<span class="lucide-check size-3.5 shrink-0 text-ink-gray-4" aria-hidden="true"></span>
		<span class="min-w-0 truncate">{{ title }}</span>
		<span class="shrink-0">· {{ label(question._answer) }}</span>
	</div>

	<div v-else class="rounded-lg border border-outline-gray-2 bg-surface-white shadow-sm">
		<div class="flex gap-2.5 p-3">
			<span
				class="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md"
				:class="
					destructive
						? 'bg-surface-red-1 text-ink-red-3'
						: 'bg-surface-gray-2 text-ink-gray-6'
				"
			>
				<span
					class="size-3.5"
					:class="
						destructive
							? 'lucide-triangle-alert'
							: asking
								? 'lucide-circle-help'
								: 'lucide-shield-check'
					"
					aria-hidden="true"
				></span>
			</span>
			<div class="min-w-0 flex-1">
				<p class="break-words text-base font-medium text-ink-gray-8">{{ title }}</p>
				<!-- A retried write says what the site rejected the first time. Being
				     asked twice for what looks like the same thing, with no reason
				     given, is how a confirmation stops meaning anything. -->
				<p v-if="question.note" class="pt-0.5 text-p-sm text-ink-gray-6">
					{{ __("The first attempt was rejected: {0}", [question.note]) }}
				</p>
				<p v-else class="pt-0.5 text-p-sm text-ink-gray-5">
					{{
						isTool
							? __("Copilot is waiting for you before it changes anything.")
							: __("Copilot needs an answer to carry on.")
					}}
				</p>
			</div>
		</div>

		<div v-if="showArgs" class="mx-3 rounded-md bg-surface-gray-1 p-2.5">
			<ToolArgs :arguments="args" :tool="tool.name" />
		</div>
		<p
			v-else-if="body"
			class="mx-3 whitespace-pre-wrap break-words rounded-md bg-surface-gray-1 p-2.5 text-p-sm text-ink-gray-7"
		>
			{{ body }}
		</p>

		<div v-if="!typing" class="flex items-center gap-2 p-3">
			<button
				v-if="question.allow_other !== false"
				class="text-sm text-ink-gray-5 hover:text-ink-gray-7"
				@click="showOther"
			>
				{{ __("Ask for something else") }}
			</button>
			<span class="flex-1"></span>
			<Button
				v-for="option in options"
				:key="option"
				:variant="variant(option)"
				:theme="theme(option)"
				:label="label(option)"
				@click="emit('answer', option)"
			/>
		</div>

		<div v-else class="p-3">
			<p v-if="asking && !options.length" class="pb-2 text-p-sm text-ink-gray-5">
				{{ __("Type your answer.") }}
			</p>
			<textarea
				ref="other"
				v-model="question._otherText"
				rows="2"
				class="w-full resize-none rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-2 text-p-sm text-ink-gray-8 outline-none placeholder:text-ink-gray-4 focus:border-outline-gray-3"
				:placeholder="__('Describe what you want instead…')"
				@keydown.enter.exact.prevent="sendOther"
				@keydown.esc="cancelOther"
			></textarea>
			<div class="flex justify-end gap-2 pt-2">
				<Button
					v-if="options.length"
					variant="ghost"
					:label="__('Cancel')"
					@click="cancelOther"
				/>
				<Button
					variant="solid"
					theme="gray"
					:label="__('Send')"
					:disabled="!question._otherText?.trim()"
					@click="sendOther"
				/>
			</div>
		</div>
	</div>
</template>
