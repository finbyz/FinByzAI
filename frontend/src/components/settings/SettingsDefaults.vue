<script setup>
import { computed } from "vue";
import SettingsRow from "./SettingsRow.vue";
import SettingsSelect from "./SettingsSelect.vue";
import { MultiSelect, Switch } from "@/lib/ui";
import { __ } from "@/lib/translate";

// Site-wide defaults. Copilot admins only — the dialog hides this pane otherwise.
const props = defineProps({
	data: { type: Object, required: true },
	draft: { type: Object, required: true },
});
const emit = defineEmits(["edit"]);

const system = computed(() => props.data.system || {});
const value = (key) => (key in props.draft ? props.draft[key] : system.value[key]);

const availableAgentItems = computed(() =>
	(props.data.agents || []).map((agent) => ({
		value: agent.name,
		label: agent.title || agent.name,
		logo: agent.logo,
	})),
);
const availableModelItems = computed(() =>
	(props.data.models || []).map((model) => ({
		value: model.name,
		label: model.title || model.name,
		logo: model.logo,
		description: model.provider,
	})),
);

const availableAgentNames = computed(() => {
	const selected = value("available_agents") || [];
	const names = selected.length ? selected : (props.data.agents || []).map((agent) => agent.name);
	return new Set(names);
});
const availableModelNames = computed(() => {
	const selected = value("available_models") || [];
	const names = selected.length ? selected : (props.data.models || []).map((model) => model.name);
	return new Set(names);
});

const agentItems = computed(() => [
	{ value: null, label: __("None") },
	...(props.data.agents || [])
		.filter((agent) => availableAgentNames.value.has(agent.name))
		.map((agent) => ({
			value: agent.name,
			label: agent.title || agent.name,
			logo: agent.logo,
		})),
]);
const modelItems = computed(() => [
	{ value: null, label: __("None") },
	...(props.data.models || [])
		.filter((model) => availableModelNames.value.has(model.name))
		.map((m) => ({
			value: m.name,
			label: m.title || m.name,
			logo: m.logo,
			group: m.provider,
		})),
]);

function setAvailableAgents(agents) {
	emit("edit", "available_agents", agents);
	if (agents.length && !agents.includes(value("default_agent"))) emit("edit", "default_agent", null);
	if (agents.length && !agents.includes(value("suggestion_agent"))) emit("edit", "suggestion_agent", null);
}

function setAvailableModels(models) {
	emit("edit", "available_models", models);
	if (models.length && !models.includes(value("default_model"))) emit("edit", "default_model", null);
}
</script>

<template>
	<div>
		<SettingsRow
			:label="__('Copilot enabled')"
			:description="__('Turns the Copilot off for everyone without uninstalling anything.')"
		>
			<Switch :model-value="Boolean(value('enabled'))" @update:model-value="emit('edit', 'enabled', $event ? 1 : 0)" />
		</SettingsRow>

		<SettingsRow
			:label="__('Available agents')"
			:description="__('Agents users can choose in Copilot. Leave empty to allow every agent.')"
		>
			<MultiSelect
				:model-value="value('available_agents') || []"
				:options="availableAgentItems"
				:placeholder="__('Select agents')"
				portal-to="#copilot-root"
				@update:model-value="setAvailableAgents"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Available models')"
			:description="
				__('Models users can choose in Copilot. Leave empty to allow every enabled model.')
			"
		>
			<MultiSelect
				:model-value="value('available_models') || []"
				:options="availableModelItems"
				:placeholder="__('Select models')"
				portal-to="#copilot-root"
				@update:model-value="setAvailableModels"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Default agent')"
			:description="__('Every new conversation starts with this agent.')"
		>
			<SettingsSelect
				:items="agentItems"
				:model-value="value('default_agent')"
				:placeholder="__('None')"
				@update:model-value="emit('edit', 'default_agent', $event)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Fallback model')"
			:description="__('Used only when the agent has no model set.')"
		>
			<SettingsSelect
				:items="modelItems"
				:model-value="value('default_model')"
				:placeholder="__('None')"
				@update:model-value="emit('edit', 'default_model', $event)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Suggestion agent')"
			:description="__('Writes each person\'s starter prompts every few days from their own past questions. Its model and its wording live on the agent, so edit it there.')"
		>
			<SettingsSelect
				:items="agentItems"
				:model-value="value('suggestion_agent')"
				:placeholder="__('Copilot Suggestions')"
				@update:model-value="emit('edit', 'suggestion_agent', $event)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Personalized suggestions')"
			:description="__('Allow recent Copilot questions to be sent to the suggestion agent every few days. Off by default.')"
		>
			<Switch
				:model-value="Boolean(value('enable_personalized_suggestions'))"
				@update:model-value="
					emit('edit', 'enable_personalized_suggestions', $event ? 1 : 0)
				"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Max steps per turn')"
			:description="__('How many tool calls one answer may take before it stops and asks you.')"
		>
			<input
				type="number"
				min="1"
				max="60"
				class="h-7 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2 text-xs text-ink-gray-8 outline-none focus:border-outline-gray-3"
				:value="value('max_iterations')"
				@change="emit('edit', 'max_iterations', Number($event.target.value) || 25)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('Skip approval for writes')"
			:description="__('Leave off on a live site. On, the agent creates, updates and deletes records without asking first.')"
		>
			<Switch
				:model-value="Boolean(value('auto_approve'))"
				@update:model-value="emit('edit', 'auto_approve', $event ? 1 : 0)"
			/>
		</SettingsRow>

		<SettingsRow
			:label="__('External web search')"
			:description="__('Lets the agent search the open web for anything outside this site. Off by default: each search sends the question to a third party and costs a small real amount.')"
		>
			<Switch
				:model-value="Boolean(value('enable_external_search'))"
				@update:model-value="emit('edit', 'enable_external_search', $event ? 1 : 0)"
			/>
		</SettingsRow>
	</div>
</template>
