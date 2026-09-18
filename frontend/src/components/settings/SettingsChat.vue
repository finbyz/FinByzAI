<script setup>
import { computed } from "vue";
import { ref } from "vue";
import SettingsRow from "./SettingsRow.vue";
import SettingsSelect from "./SettingsSelect.vue";
import * as api from "@/api/client";
import { __ } from "@/lib/translate";

// Per-conversation choices. Everything here is read from the AI Agent unless
// overridden, which is why each row says what it falls back to.
const props = defineProps({
	data: { type: Object, required: true },
	draft: { type: Object, required: true },
	store: { type: Object, required: true },
});
const emit = defineEmits(["edit"]);

const value = (key, fallback) => (key in props.draft ? props.draft[key] : fallback);

const agentItems = computed(() =>
	(props.data.agents || []).map((a) => ({
		value: a.name,
		label: a.title || a.name,
		logo: a.logo,
		hint: a.llm || undefined,
	}))
);

const modelItems = computed(() => [
	{ value: null, label: __("Use the agent's model") },
	...(props.data.models || []).map((m) => ({
		value: m.name,
		label: m.title || m.name,
		logo: m.logo,
		group: m.provider,
	})),
]);

const knowledgeItems = computed(() => [
	{ value: null, label: __("Use the agent's knowledge base") },
	...(props.data.knowledge_bases || []).map((k) => ({
		value: k.name,
		label: k.title || k.name,
		hint: k.status && k.status !== "Completed" ? k.status : undefined,
	})),
]);

const agent = computed(() => value("agent", props.store.selectedAgent.value));

// A dead key is the most common reason the Copilot appears to do nothing, and the
// provider's own message says exactly why. Cheaper to read it here than to send a real
// question and wait for the run to fail.
const testing = ref(false);
const result = ref(null);

async function test() {
	testing.value = true;
	result.value = null;
	try {
		result.value = await api.testModel({
			model: value("model", props.store.selectedModel.value),
			agent: agent.value,
		});
	} catch (e) {
		result.value = { ok: false, error: e.message || __("Could not reach the model") };
	} finally {
		testing.value = false;
	}
}
const activeAgent = computed(() =>
	(props.data.agents || []).find((a) => a.name === agent.value)
);
</script>

<template>
	<div>
		<SettingsRow
			:label="__('Agent')"
			:description="__('Its model, tools, knowledge base and memory settings are what this chat uses. Edit the AI Agent record to change those.')"
		>
			<SettingsSelect
				:items="agentItems"
				:model-value="agent"
				:placeholder="__('No agent configured')"
				@update:model-value="emit('edit', 'agent', $event)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Model')"
			:description="__('Overrides the agent’s model for this chat only.')"
		>
			<SettingsSelect
				:items="modelItems"
				:model-value="value('model', store.selectedModel.value)"
				:placeholder="__('Use the agent’s model')"
				@update:model-value="emit('edit', 'model', $event)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Knowledge base')"
			:description="__('The agent searches it before answering, and saves durable facts to it.')"
		>
			<SettingsSelect
				:items="knowledgeItems"
				:model-value="value('knowledge_base', store.selectedKnowledgeBase.value)"
				:placeholder="__('None')"
				@update:model-value="emit('edit', 'knowledge_base', $event)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Connection')"
			:description="__('Sends three tokens to the model above and shows what the provider says. Nothing is saved and no conversation is created.')"
		>
			<button
				class="h-7 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2 text-xs text-ink-gray-8 hover:border-outline-gray-3 disabled:opacity-50"
				:disabled="testing"
				@click="test"
			>
				{{ testing ? __("Testing…") : __("Test connection") }}
			</button>
		</SettingsRow>

		<div
			v-if="result"
			class="mt-3 rounded-lg border p-3 text-2xs"
			:class="
				result.ok
					? 'border-outline-green-1 bg-surface-green-1 text-ink-gray-7'
					: 'border-outline-red-1 bg-surface-red-1 text-ink-gray-7'
			"
		>
			<div class="font-medium" :class="result.ok ? 'text-ink-green-4' : 'text-ink-red-4'">
				{{ result.ok ? __("Model answered") : __("Model did not answer") }}
				<span v-if="result.latency_ms" class="font-normal text-ink-gray-5">
					· {{ result.latency_ms }}ms</span
				>
			</div>
			<div v-if="result.model" class="mt-1 font-mono">{{ result.model }}</div>
			<div class="mt-1 leading-relaxed">{{ result.reply || result.error }}</div>
			<div v-if="result.hint" class="mt-1 italic text-ink-gray-6">{{ result.hint }}</div>
		</div>

		<div
			v-if="activeAgent"
			class="mt-4 rounded-lg border border-outline-gray-1 bg-surface-gray-1 p-3 text-2xs text-ink-gray-6"
		>
			<div class="mb-1 font-medium text-ink-gray-7">{{ __("From the agent") }}</div>
			<div>{{ __("Model") }}: {{ activeAgent.llm || __("not set") }}</div>
			<div>
				{{ __("Knowledge base") }}: {{ activeAgent.knowledge_base || __("none attached") }}
			</div>
			<button
				class="mt-2 text-ink-blue-3 hover:underline"
				@click="frappe.set_route('Form', 'AI Agent', activeAgent.name)"
			>
				{{ __("Open this agent") }} →
			</button>
		</div>
		<div v-else class="mt-4 rounded-lg border border-outline-amber-1 bg-surface-amber-1 p-3 text-2xs text-ink-gray-7">
			{{ __("No AI Agent exists yet. Create one to give the Copilot its model, tools, knowledge base and memory.") }}
			<button class="ml-1 text-ink-blue-3 hover:underline" @click="frappe.new_doc('AI Agent')">
				{{ __("Create an agent") }} →
			</button>
		</div>
	</div>
</template>
