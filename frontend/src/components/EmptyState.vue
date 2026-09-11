<script setup>
import { computed, onMounted, ref } from "vue";
import { Button } from "@/lib/ui";
import * as api from "@/api/client";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

// The first screen, ours, built on frappe-ui's empty-state anatomy: a glyph in a round
// gray well, a line of ink-gray-7, a line of ink-gray-5, one action.
//
// What came off it, following "gray first, and a border needs a reason":
// - the four bordered suggestion cards became plain rows with a hover fill. Four
//   borders inside a bordered panel below a bordered header is five boxes deep.
// - the ↗ on every row: decoration, and it implied navigation rather than sending.
// - "TRY ASKING" in caps: a quiet label is `text-sm text-ink-gray-5`, not shouting.
//
// The suggestions themselves still come from the live tool list, so the screen never
// offers something this site cannot answer.
const store = useStore();
const { needsSetup, selectedAgent, selectedKnowledgeBase, settingsOpen, send } = store;

const tools = ref([]);
const checking = ref(false);
const connection = ref(null);

const CATALOGUE = [
	{
		tool: "selling_intelligence",
		prompts: [
			__("How are sales doing this year, and is any customer slipping away?"),
			__("Which items have lost the most revenue?"),
		],
	},
	{
		tool: "inventory_intelligence",
		prompts: [__("What hasn't sold in 60–90 days, and what is it worth?")],
	},
	{ tool: "purchasing_intelligence", prompts: [__("What should we order this week?")] },
	{ tool: "manufacturing_intelligence", prompts: [__("What should we manufacture next?")] },
	{ tool: "financial_intelligence", prompts: [__("How are our margins trending?")] },
	{ tool: "run_report", prompts: [__("Run the Accounts Receivable report")] },
	{ tool: "read", prompts: [__("Show me this month's overdue invoices")] },
];

const available = computed(() => new Set(tools.value.map((t) => t.name)));

const suggestions = computed(() => {
	const out = [];
	for (const group of CATALOGUE) {
		if (!available.value.has(group.tool)) continue;
		for (const prompt of group.prompts) {
			out.push(prompt);
			if (out.length >= 4) return out;
		}
	}
	return out;
});

const greeting = computed(() => {
	const name = (frappe.boot?.user?.first_name || "").trim();
	return name && name !== "Administrator" ? __("Hi {0}", [name]) : __("Copilot");
});

onMounted(async () => {
	try {
		const data = await api.loadTools({
			agent: selectedAgent.value,
			knowledge_base: selectedKnowledgeBase.value,
		});
		tools.value = data.tools || [];
	} catch {
		tools.value = [];
	}
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
					{{ __("Ask about your data, draft records, or run a task.") }}
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
				<p class="mt-8 px-2 pb-1 text-sm text-ink-gray-5">{{ __("Try asking") }}</p>
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
				<span>{{ __("Ctrl+I to open or close") }}</span>
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
