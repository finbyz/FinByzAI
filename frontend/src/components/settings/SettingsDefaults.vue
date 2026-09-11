<script setup>
import { computed } from "vue";
import SettingsRow from "./SettingsRow.vue";
import SettingsSelect from "./SettingsSelect.vue";
import { Switch } from "@/lib/ui";
import { __ } from "@/lib/translate";

// Site-wide defaults. System Manager only — the dialog hides this pane otherwise.
const props = defineProps({
	data: { type: Object, required: true },
	draft: { type: Object, required: true },
});
const emit = defineEmits(["edit"]);

const system = computed(() => props.data.system || {});
const value = (key) => (key in props.draft ? props.draft[key] : system.value[key]);

const agentItems = computed(() => [
	{ value: null, label: __("None") },
	...(props.data.agents || []).map((a) => ({ value: a.name, label: a.title || a.name, logo: a.logo })),
]);
const modelItems = computed(() => [
	{ value: null, label: __("None") },
	...(props.data.models || []).map((m) => ({
		value: m.name,
		label: m.title || m.name,
		logo: m.logo,
		group: m.provider,
	})),
]);
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
	</div>
</template>
