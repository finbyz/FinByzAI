<script setup>
import { computed, onMounted, ref } from "vue";
import { Button, FeatherIcon } from "@/lib/ui";
import BrandMark from "./BrandMark.vue";
import * as api from "@/api/client";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

// The first thing anyone sees, and the only chance to answer "what can I ask it?".
//
// The suggestions are not a hardcoded list. They are derived from the tools the agent
// actually has, so a site with the selling and inventory tools installed offers those
// questions, and a site without them offers the generic ones that always work. A
// first-run screen that promises something this site cannot do is worse than a blank one.
const store = useStore();
const { needsSetup, selectedAgent, selectedKnowledgeBase, settingsOpen, send } = store;

const tools = ref([]);
const checking = ref(false);
const connection = ref(null);

// Each group is offered only when its tool is present.
const CATALOGUE = [
	{
		tool: "selling_intelligence",
		icon: "trending-up",
		prompts: [
			__("How are sales doing this year, and is any customer slipping away?"),
			__("Which items have lost the most revenue?"),
		],
	},
	{
		tool: "inventory_intelligence",
		icon: "package",
		prompts: [
			__("What hasn't sold in 60–90 days, and what capital is tied up in it?"),
			__("What needs reordering right now?"),
		],
	},
	{
		tool: "purchasing_intelligence",
		icon: "shopping-cart",
		prompts: [__("What should we order this week, and by when?")],
	},
	{
		tool: "manufacturing_intelligence",
		icon: "tool",
		prompts: [__("What should we manufacture next?")],
	},
	{
		tool: "financial_intelligence",
		icon: "pie-chart",
		prompts: [__("How are our margins trending, and who owes us the most?")],
	},
	// Always available — every site has reports and records.
	{
		tool: "run_report",
		icon: "file-text",
		prompts: [__("Run the Accounts Receivable report for this month")],
	},
	{
		tool: "read",
		icon: "database",
		prompts: [__("Show me this month's overdue invoices")],
	},
];

const available = computed(() => new Set(tools.value.map((t) => t.name)));

const suggestions = computed(() => {
	const out = [];
	for (const group of CATALOGUE) {
		if (!available.value.has(group.tool)) continue;
		for (const prompt of group.prompts) {
			out.push({ prompt, icon: group.icon });
			if (out.length >= 4) return out;
		}
	}
	return out;
});

const greeting = computed(() => {
	const name = (frappe.boot?.user?.first_name || "").trim();
	return name ? __("Hi {0}", [name]) : __("Copilot");
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

// A dead credential is the most common reason nothing works, and it is worth finding
// out here rather than after typing a question and waiting for the run to fail.
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
	<div class="flex flex-1 flex-col items-center justify-center px-6 py-10">
		<div class="w-full max-w-lg">
			<div class="flex flex-col items-center text-center">
				<BrandMark :size="30" />
				<h2 class="mt-3 text-lg font-semibold text-ink-gray-9">{{ greeting }}</h2>
				<p class="mt-1 text-base leading-relaxed text-ink-gray-6">
					{{ __("Ask about your data, draft records, or run a task.") }}
				</p>
			</div>

			<!-- Setup first, if there is any -->
			<div
				v-if="needsSetup"
				class="mt-5 rounded-lg border border-outline-amber-1 bg-surface-amber-1 p-3"
			>
				<div class="flex items-start gap-2">
					<FeatherIcon name="alert-triangle" class="mt-0.5 size-4 shrink-0 text-ink-amber-3" />
					<div class="min-w-0 flex-1">
						<p class="text-base font-medium text-ink-gray-8">{{ __("Setup needed") }}</p>
						<p class="mt-0.5 text-sm leading-relaxed text-ink-gray-6">
							{{ __("Add an LLM Provider with a working key, then pick a model and agent.") }}
						</p>
						<Button class="mt-2" variant="outline" @click="settingsOpen = true">
							{{ __("Open settings") }}
						</Button>
					</div>
				</div>
			</div>

			<!-- Suggestions, drawn from the tools this agent really has -->
			<div v-else-if="suggestions.length" class="mt-6 space-y-1.5">
				<p class="px-1 pb-1 text-2xs font-medium uppercase tracking-wide text-ink-gray-4">
					{{ __("Try asking") }}
				</p>
				<button
					v-for="item in suggestions"
					:key="item.prompt"
					class="flex w-full items-center gap-2.5 rounded-lg border border-outline-gray-2 bg-surface-white px-3 py-2.5 text-left transition-colors hover:border-outline-gray-3 hover:bg-surface-gray-1"
					@click="send(item.prompt)"
				>
					<FeatherIcon :name="item.icon" class="size-4 shrink-0 text-ink-gray-5" />
					<span class="min-w-0 flex-1 text-base leading-snug text-ink-gray-8">{{
						item.prompt
					}}</span>
					<FeatherIcon name="arrow-up-right" class="size-3.5 shrink-0 text-ink-gray-4" />
				</button>
			</div>

			<!-- Quiet footer: the shortcut, and a way to check the model before trusting it -->
			<div class="mt-6 flex items-center justify-center gap-3 text-2xs text-ink-gray-5">
				<span>{{ __("Ctrl+I to open or close") }}</span>
				<span class="text-ink-gray-3">·</span>
				<button class="hover:text-ink-gray-7" :disabled="checking" @click="testConnection">
					{{ checking ? __("Checking…") : __("Check the model") }}
				</button>
			</div>

			<div
				v-if="connection"
				class="mt-3 rounded-lg border p-2.5 text-2xs leading-relaxed"
				:class="
					connection.ok
						? 'border-outline-green-1 bg-surface-green-1 text-ink-gray-7'
						: 'border-outline-red-1 bg-surface-red-1 text-ink-gray-7'
				"
			>
				<span class="font-medium" :class="connection.ok ? 'text-ink-green-4' : 'text-ink-red-4'">
					{{ connection.ok ? __("Ready") : __("Not ready") }}
				</span>
				<span v-if="connection.model" class="text-ink-gray-5"> · {{ connection.model }}</span>
				<div class="mt-0.5">{{ connection.reply || connection.error }}</div>
				<div v-if="connection.hint" class="mt-0.5 italic text-ink-gray-6">
					{{ connection.hint }}
				</div>
			</div>
		</div>
	</div>
</template>
