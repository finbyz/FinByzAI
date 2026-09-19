<script setup>
import { computed, onMounted, ref } from "vue";
import { Button, KeyboardShortcut } from "@/lib/ui";
import * as api from "@/api/client";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

// The first screen, ours, built on frappe-ui's empty-state anatomy: a glyph in a round
// gray well, a line of ink-gray-7, a line of ink-gray-5, one action.
//
// There are no example prompts. There were four, gated on tools like
// `selling_intelligence` that no site here has, so in practice the gate never matched
// and every user — a developer, someone in support, anyone without accounts access —
// was greeted by "Run the Accounts Receivable report" and "Show me this month's overdue
// invoices". Clicking either earned them a permission error as their first experience
// of the product. A suggestion that has to guess what someone may see is worse than no
// suggestion, so the screen now says what is true instead: ask for anything, and you
// will only ever be shown what you already have access to.
const store = useStore();
const { needsSetup, selectedAgent, settingsOpen, send } = store;

const checking = ref(false);
const connection = ref(null);

// Written from this user's own past questions by a scheduled job, so they can only
// ever point at things this person already asks about. Empty is the normal state for
// someone new, and the screen simply shows nothing rather than a generic example.
const suggestions = ref([]);
onMounted(async () => {
	suggestions.value = await api.loadSuggestions();
});

const greeting = computed(() => {
	const name = (frappe.boot?.user?.first_name || "").trim();
	return name && name !== "Administrator" ? __("Hi {0}", [name]) : __("Copilot");
});

async function testConnection() {
	checking.value = true;
	connection.value = null;
	try {
		connection.value = await api.testModel({ agent: selectedAgent.value });
	} catch (e) {
		connection.value = { ok: false, error: e.message };
	} finally {
		checking.value = false;
	}
}
</script>

<template>
	<div class="flex flex-1 flex-col items-center justify-center py-16">
		<div class="w-full max-w-md">
			<div class="flex flex-col items-center gap-3 text-center">
				<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
					<span class="lucide-sparkles size-6" aria-hidden="true"></span>
				</div>
				<p class="text-lg text-ink-gray-8">{{ greeting }}</p>
				<p class="text-p-base text-ink-gray-5">
					{{ __("Ask anything about your work — projects, tasks, time, reports, records. You only ever see what your permissions already allow.") }}
				</p>
			</div>

			<!-- Setup outranks everything else on this screen -->
			<div v-if="needsSetup" class="mt-8 flex flex-col items-center gap-3 text-center">
				<p class="text-p-sm text-ink-gray-6">
					{{ __("No model is configured yet. Add an LLM Provider with a working key, then pick a model.") }}
				</p>
				<Button variant="solid" theme="gray" label="Open settings" @click="settingsOpen = true" />
			</div>

			<template v-else-if="suggestions.length">
				<p class="mt-8 px-2 pb-1 text-sm text-ink-gray-5">{{ __("You often ask") }}</p>
				<div class="divide-y divide-outline-gray-1">
					<button
						v-for="prompt in suggestions"
						:key="prompt"
						class="w-full rounded px-2 py-2.5 text-left text-p-base text-ink-gray-7 hover:bg-surface-gray-2 hover:text-ink-gray-8"
						@click="send(prompt)"
					>
						{{ prompt }}
					</button>
				</div>
			</template>

			<div class="mt-8 flex items-center justify-center gap-2 text-2xs text-ink-gray-5">
				<KeyboardShortcut combo="Mod+I" />
				<span>{{ __("to open or close") }}</span>
				<span class="text-ink-gray-3">·</span>
				<button class="hover:text-ink-gray-7" :disabled="checking" @click="testConnection">
					{{ checking ? __("Checking…") : __("Check the model") }}
				</button>
			</div>

			<p
				v-if="connection"
				class="mt-3 text-center text-2xs leading-relaxed"
				:class="connection.ok ? 'text-ink-green-4' : 'text-ink-red-4'"
			>
				{{ connection.ok ? __("Model answered") : connection.error }}
				<span v-if="connection.hint" class="block text-ink-gray-5">{{ connection.hint }}</span>
			</p>
		</div>
	</div>
</template>
