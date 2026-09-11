<script setup>
import { onMounted, ref } from "vue";
import { Badge, FeatherIcon } from "@/lib/ui";
import * as api from "@/api/client";
import { __ } from "@/lib/translate";

// What this agent can actually do, read from the live registry — so a tool added to
// the app, or an AI Tool linked on the agent, appears here with no UI change.
const props = defineProps({ store: { type: Object, required: true } });

const tools = ref([]);
const loading = ref(true);

onMounted(async () => {
	try {
		const data = await api.loadTools({
			agent: props.store.selectedAgent.value,
			knowledge_base: props.store.selectedKnowledgeBase.value,
		});
		tools.value = data.tools || [];
	} finally {
		loading.value = false;
	}
});
</script>

<template>
	<div>
		<p class="pb-3 text-2xs leading-relaxed text-ink-gray-5">
			{{ __("Everything the agent may call. Writes ask for your approval first; the rest run on their own, always with your own permissions.") }}
		</p>
		<div v-if="loading" class="text-xs text-ink-gray-5">{{ __("Loading…") }}</div>
		<div v-else class="space-y-1">
			<div
				v-for="tool in tools"
				:key="tool.name"
				class="rounded-md border border-outline-gray-1 px-3 py-2"
			>
				<div class="flex items-center gap-2">
					<span class="font-mono text-2xs text-ink-gray-8">{{ tool.name }}</span>
					<Badge v-if="tool.confirm" theme="orange" size="sm">{{ __("asks approval") }}</Badge>
					<Badge v-else-if="tool.asks" theme="blue" size="sm">{{ __("asks you") }}</Badge>
					<Badge v-if="tool.source !== 'builtin'" theme="gray" size="sm">{{ tool.source }}</Badge>
					<span class="flex-1"></span>
					<span class="text-2xs text-ink-gray-4">{{ tool.label }}</span>
				</div>
				<p class="mt-1 text-2xs leading-relaxed text-ink-gray-5">{{ tool.description }}</p>
			</div>
			<div v-if="!tools.length" class="text-xs text-ink-gray-5">{{ __("No tools available.") }}</div>
		</div>
	</div>
</template>
