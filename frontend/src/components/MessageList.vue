<script setup>
import { ref, watch } from "vue";
import UserMessage from "./UserMessage.vue";
import AssistantMessage from "./AssistantMessage.vue";
import EmptyState from "./EmptyState.vue";
import { useStore } from "@/store";
import { COLUMN, GUTTER, SCROLL_END } from "@/lib/layout";
import { __ } from "@/lib/translate";

// The conversation.
//
// It follows new content while the user is at the bottom and stops the moment
// they scroll up, because reading a table the agent just drew while the stream
// keeps yanking the view down is the fastest way to make a panel unusable. The
// button is the other half of that deal: having stopped following, there has to
// be one click back to the live edge — otherwise a multi-step run leaves the
// reader stranded mid-scrollback with no idea it has finished.
const { messages, needsSetup, agents, models, scrollTick, forceScroll } = useStore();

// Far enough from the bottom to mean "the user is reading", not "the last line
// is a pixel short".
const NEAR = 80;

const el = ref(null);
const atBottom = ref(true);
let frame = 0;
let follow = true;

function onScroll() {
	const node = el.value;
	if (!node) return;
	atBottom.value = node.scrollHeight - node.scrollTop - node.clientHeight < NEAR;
	follow = atBottom.value;
}

function toBottom(smooth = true) {
	const node = el.value;
	if (!node) return;
	node.scrollTo({ top: node.scrollHeight, behavior: smooth ? "smooth" : "auto" });
	follow = true;
	atBottom.value = true;
}

function onTick() {
	frame = 0;
	// Smooth for something the user did (sending, answering); instant while
	// streaming, where smooth scrolling can never keep up with the tokens.
	if (follow || forceScroll.value) toBottom(forceScroll.value);
	forceScroll.value = false;
}

// Bursts of new content coalesce into one scroll per frame.
watch(scrollTick, () => {
	if (!frame) frame = requestAnimationFrame(onTick);
});
</script>

<template>
	<div class="relative flex min-h-0 flex-1 flex-col">
		<div
			ref="el"
			class="copilot-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto pt-5"
			:class="[GUTTER, SCROLL_END]"
			@scroll.passive="onScroll"
		>
			<div class="flex flex-1 flex-col gap-6" :class="COLUMN">
				<EmptyState
					v-if="!messages.length"
					:setup="needsSetup"
					:has-models="models.length > 0"
					:has-agents="agents.length > 0"
				/>

				<template v-for="message in messages" :key="message.id">
					<template v-if="message.role === 'user'">
						<UserMessage :content="message.content" :attachments="message.attachments" />
						<div
							v-if="message.interrupted"
							class="flex items-center gap-1.5 text-sm text-ink-gray-5"
						>
							<span class="lucide-circle-x size-3.5 shrink-0" aria-hidden="true"></span>
							{{ __("This answer was interrupted") }}
						</div>
					</template>
					<AssistantMessage v-else :message="message" />
				</template>
			</div>
		</div>

		<Transition name="copilot-fade">
			<button
				v-if="!atBottom && messages.length"
				class="absolute bottom-3 left-1/2 flex size-7 -translate-x-1/2 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-6 shadow-md transition-colors hover:bg-surface-gray-2"
				:aria-label="__('Jump to the latest')"
				@click="toBottom()"
			>
				<span class="lucide-arrow-down size-3.5" aria-hidden="true"></span>
			</button>
		</Transition>
	</div>
</template>
