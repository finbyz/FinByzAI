<script setup>
import { computed, ref } from "vue";
import Prose from "./Prose.vue";
import ActivityLine from "./ActivityLine.vue";
import ApprovalCard from "./ApprovalCard.vue";
import BlockRenderer from "./blocks/BlockRenderer.vue";
import Tip from "./Tip.vue";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

// One assistant turn: prose, the blocks it drew, what it did to get them, and
// any decision it is waiting on — in the order they happened.
//
// The grouping below is presentation only. Consecutive tool calls merge into one
// activity line, because a run that reads a doctype, counts it, then aggregates
// it has done one thing as far as the reader is concerned. A call that needs
// approval always stands alone: it is a question, and questions do not get
// folded into a tally.
const props = defineProps({ message: { type: Object, required: true } });
const { answerQuestion, toolApproval } = useStore();

const questions = computed(() => new Map(props.message.questions.map((q) => [q.key, q])));

// Needs, needed, or was configured to need a decision.
const needsDecision = (part) =>
	toolApproval.value[part.name] === true ||
	questions.value.has(part.id) ||
	part.approval !== null;

const items = computed(() => {
	const out = [];
	for (const part of props.message.parts) {
		if (part.type === "text") {
			out.push({ kind: "text", id: part.id, part });
		} else if (part.type === "block") {
			out.push({ kind: "block", id: part.id, part });
		} else if (needsDecision(part)) {
			const question = questions.value.get(part.id);
			if (question && question._answer === undefined)
				out.push({ kind: "decision", id: part.id, question, part });
			else out.push({ kind: "activity", id: part.id, parts: [part] });
		} else {
			const last = out[out.length - 1];
			if (last?.kind === "activity") last.parts.push(part);
			else out.push({ kind: "activity", id: part.id, parts: [part] });
		}
	}
	return out;
});

// A free-text ask from the model arrives as a question with no tool call behind
// it, so nothing in `parts` would render it. The recovery path does synthesise a
// call for the same question, hence the id check — a question is shown once.
const asks = computed(() => {
	const ids = new Set(props.message.parts.map((p) => p.id));
	return props.message.questions.filter(
		(q) => q.kind === "question" && q._answer === undefined && !ids.has(q.key)
	);
});

// Only until the first part lands; after that the activity line carries the
// state, and two live indicators at once just compete.
const working = computed(() => props.message.pending && !props.message.parts.length);

const answer = computed(() =>
	items.value
		.filter((i) => i.kind === "text")
		.map((i) => i.part.text)
		.join("\n\n")
		.trim()
);
const copied = ref(false);
let timer = 0;
async function copy() {
	try {
		await navigator.clipboard.writeText(answer.value);
		copied.value = true;
		clearTimeout(timer);
		timer = setTimeout(() => (copied.value = false), 1500);
	} catch {
		// Clipboard unavailable; the text is selectable either way.
	}
}
</script>

<template>
	<div class="copilot-parts group/msg flex flex-col">
		<template v-for="(item, i) in items" :key="item.id">
			<Prose v-if="item.kind === 'text'" :part="item.part" />
			<BlockRenderer v-else-if="item.kind === 'block'" :block="item.part.block" />
			<ApprovalCard
				v-else-if="item.kind === 'decision'"
				:question="item.question"
				:tool="item.part"
				@answer="(value) => answerQuestion(message, item.question, value)"
			/>
			<ActivityLine
				v-else
				:parts="item.parts"
				:sealed="i < items.length - 1"
				:live="message.pending"
			/>
		</template>

		<ApprovalCard
			v-for="ask in asks"
			:key="ask.key"
			:question="ask"
			@answer="(value) => answerQuestion(message, ask, value)"
		/>

		<div v-if="working" class="copilot-shimmer-text text-sm">{{ __("Working…") }}</div>

		<!-- The answer is the deliverable; copying it is the one action this turn
		     needs. It appears on hover so it isn't part of the reading surface. -->
		<div
			v-if="answer && !message.pending"
			class="flex opacity-0 transition-opacity focus-within:opacity-100 group-hover/msg:opacity-100"
		>
			<Tip :text="copied ? __('Copied') : __('Copy answer')">
				<button
					class="-ml-1 rounded p-1 hover:bg-surface-gray-2"
					:aria-label="__('Copy answer')"
					@click="copy"
				>
					<span
						class="size-3.5"
						:class="copied ? 'lucide-check text-ink-green-3' : 'lucide-copy text-ink-gray-5'"
						aria-hidden="true"
					></span>
				</button>
			</Tip>
		</div>
	</div>
</template>
