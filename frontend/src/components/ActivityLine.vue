<script setup>
import { computed, ref } from "vue";
import { Spinner } from "@/lib/ui";
import Code from "./Code.vue";
import ToolArgs from "./ToolArgs.vue";
import { contextOf, hasArgs, isRunning, toolError, toolLabel } from "@/lib/tools";
import { __ } from "@/lib/translate";

// What the agent did, as one line per turn-segment.
//
// A run can make a dozen calls to answer one question, and the answer is what
// the user came for — so consecutive calls collapse into a single line that says
// what is happening now, and opens into the steps only if asked.
//
// The leading slot is the same width whether it holds a spinner, a tick or a
// cross, so labels align down the whole turn and the line needs no timeline
// drawing: a spinner means running, a tick means done, a cross means the call
// came back an error. That status is the information the collapsed line used to
// be missing entirely.
const props = defineProps({
	parts: { type: Array, required: true },
	// A later part of the turn follows this one, so it is finished with.
	sealed: { type: Boolean, default: false },
	// The turn itself is still streaming.
	live: { type: Boolean, default: false },
});

const running = computed(() => props.parts.some(isRunning));
const single = computed(() => props.parts.length === 1);
const shimmer = computed(() => props.live && !props.sealed);

const step = (part) => ({
	id: part.id,
	label: toolLabel(part.name, part.label),
	context: part.context || contextOf(part.arguments),
	error: toolError(part.result),
	running: isRunning(part),
	part,
});
const steps = computed(() => props.parts.map(step));

// Collapsed, the line reads as the call in flight; once the turn moves on, as
// the tally, because by then no single call is the point.
const headline = computed(() => {
	const active = steps.value.find((s) => s.running);
	if (active) return active;
	if (props.sealed && !single.value)
		return { label: __("Ran {0} steps", [props.parts.length]), context: null, error: null };
	return steps.value[steps.value.length - 1];
});

const failed = computed(() => steps.value.some((s) => s.error));
const decision = computed(() => {
	const approval = single.value ? props.parts[0].approval : null;
	if (approval === "denied") return __("Rejected");
	if (approval === "redirected") return __("Changed");
	return "";
});

// Openable when there is anything underneath: more than one step, or one step
// with arguments or an error to show.
const openable = computed(
	() => !single.value || hasArgs(props.parts[0].arguments) || failed.value
);
const open = ref(false);
const stepOpen = ref({});

const detail = (s) => hasArgs(s.part.arguments) || Boolean(s.error);
</script>

<template>
	<div class="text-sm">
		<button
			class="flex w-full items-center gap-1.5 text-left"
			:class="openable ? '' : 'cursor-default'"
			:disabled="!openable"
			@click="open = !open"
		>
			<span class="flex size-3.5 shrink-0 items-center justify-center">
				<Spinner v-if="running" class="size-3 text-ink-gray-5" />
				<span
					v-else-if="failed"
					class="lucide-circle-x size-3.5 text-ink-red-3"
					aria-hidden="true"
				></span>
				<span v-else class="lucide-check size-3.5 text-ink-gray-4" aria-hidden="true"></span>
			</span>

			<span
				class="min-w-0 truncate"
				:class="[
					open ? 'text-ink-gray-8' : 'text-ink-gray-6',
					shimmer && running ? 'copilot-shimmer-text' : '',
				]"
				>{{ headline.label }}</span
			>
			<span v-if="headline.context" class="min-w-0 truncate text-xs text-ink-gray-4">
				{{ headline.context }}
			</span>
			<span v-if="decision" class="shrink-0 text-xs text-ink-gray-5">· {{ decision }}</span>

			<span
				v-if="openable"
				class="lucide-chevron-down size-3.5 shrink-0 text-ink-gray-4 transition-transform"
				:class="{ '-rotate-90': !open }"
				aria-hidden="true"
			></span>
		</button>

		<!-- Open: the steps, indented to the label's column so the two states are
		     the same shape. One step shows its arguments straight away — there is
		     nothing to choose between. -->
		<div v-if="open" class="mt-1.5 flex flex-col gap-1.5 pl-5">
			<template v-if="single">
				<ToolArgs :arguments="parts[0].arguments" :tool="parts[0].name" />
				<Code v-if="steps[0].error" :code="steps[0].error" tone="red" :preview="8" />
			</template>

			<template v-else>
			<div v-for="s in steps" :key="s.id">
				<button
					class="flex w-full items-center gap-1.5 text-left"
					:class="detail(s) ? '' : 'cursor-default'"
					:disabled="!detail(s)"
					@click="stepOpen[s.id] = !stepOpen[s.id]"
				>
					<span class="flex size-3.5 shrink-0 items-center justify-center">
						<Spinner v-if="s.running" class="size-3 text-ink-gray-5" />
						<span
							v-else-if="s.error"
							class="lucide-circle-x size-3.5 text-ink-red-3"
							aria-hidden="true"
						></span>
						<span
							v-else
							class="lucide-check size-3.5 text-ink-gray-4"
							aria-hidden="true"
						></span>
					</span>
					<span class="min-w-0 truncate text-ink-gray-7">{{ s.label }}</span>
					<span v-if="s.context" class="min-w-0 truncate text-xs text-ink-gray-4">
						{{ s.context }}
					</span>
					<span
						v-if="detail(s)"
						class="lucide-chevron-down size-3 shrink-0 text-ink-gray-4 transition-transform"
						:class="{ '-rotate-90': !stepOpen[s.id] }"
						aria-hidden="true"
					></span>
				</button>
				<div v-if="stepOpen[s.id]" class="mt-1.5 flex flex-col gap-1.5 pl-5">
					<ToolArgs :arguments="s.part.arguments" :tool="s.part.name" />
					<Code v-if="s.error" :code="s.error" tone="red" :preview="8" />
				</div>
			</div>
			</template>
		</div>
	</div>
</template>
