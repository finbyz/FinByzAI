<script setup>
import { computed, ref } from "vue";
import { TextInput } from "@/lib/ui";
import Tip from "./Tip.vue";
import { useStore } from "@/store";
import { __ } from "@/lib/translate";

// Past conversations.
//
// The surface is frappe-ui's own Sidebar: `surface-menu-bar` ground, a hairline
// on the right, `p-2`, and rows that are ghost buttons going to
// `bg-surface-selected shadow-sm` when active. (Its `SidebarItem` component
// itself can't be used here — it calls `useRoute()`, and this panel has no
// router, and its tooltips portal out of the panel's CSS scope.)
//
// What it deliberately doesn't have: a header of its own with a count badge (the
// panel has a header one row above, and how many conversations exist is not a
// thing anyone needs to know), a second New chat button (the header's is always
// visible), and a per-row icon (every row would carry the same one, so it says
// nothing). Dates do the sorting work instead, the way CRM and Gameplan group
// their lists.
const emit = defineEmits(["pick"]);

const { recentSessions, sessionName, switchSession, deleteSession } = useStore();

// Long enough to be worth searching — the same threshold the panel's menus use.
const SEARCH_FROM = 7;
const DAY = 24 * 60 * 60 * 1000;

const query = ref("");
const confirming = ref(null);
const deleting = ref(null);

const sessions = computed(() => recentSessions.value || []);
const searchable = computed(() => sessions.value.length >= SEARCH_FROM);

const matching = computed(() => {
	const needle = query.value.trim().toLowerCase();
	if (!needle) return sessions.value;
	return sessions.value.filter((s) => (s.title || "").toLowerCase().includes(needle));
});

// Today / the last week / everything else. The list arrives newest first, so the
// groups come out in order without sorting anything again.
const groups = computed(() => {
	const start = new Date().setHours(0, 0, 0, 0);
	const buckets = [
		{ label: __("Today"), after: start, items: [] },
		{ label: __("Previous 7 days"), after: start - 7 * DAY, items: [] },
		{ label: __("Earlier"), after: -Infinity, items: [] },
	];
	for (const session of matching.value) {
		const at = session.modified ? new Date(session.modified).getTime() : 0;
		(buckets.find((b) => at >= b.after) || buckets[2]).items.push(session);
	}
	return buckets.filter((b) => b.items.length);
});

function pick(name) {
	switchSession(name);
	emit("pick");
}

async function remove(name) {
	deleting.value = name;
	try {
		await deleteSession(name);
	} finally {
		deleting.value = null;
		confirming.value = null;
	}
}
</script>

<template>
	<aside
		class="copilot-scrollbar flex h-full flex-col overflow-y-auto border-r border-outline-gray-1 bg-surface-menu-bar p-2"
	>
		<div class="flex shrink-0 items-center pb-2">
			<TextInput
				v-if="searchable"
				v-model="query"
				class="w-full"
				:placeholder="__('Search conversations…')"
				:debounce="150"
			>
				<template #prefix>
					<span class="lucide-search size-3.5 text-ink-gray-4" aria-hidden="true"></span>
				</template>
			</TextInput>
			<span v-else class="px-2 text-sm text-ink-gray-5">{{ __("Conversations") }}</span>
		</div>

		<p v-if="!groups.length" class="px-2 py-6 text-center text-sm text-ink-gray-5">
			{{ query ? __("No conversations match") : __("No conversations yet") }}
		</p>

		<div v-for="group in groups" :key="group.label" class="pb-2">
			<div class="px-2 pb-1 text-xs text-ink-gray-4">{{ group.label }}</div>

			<div
				v-for="session in group.items"
				:key="session.name"
				class="group/row flex h-7 items-center gap-1 rounded px-2"
				:class="
					session.name === sessionName
						? 'bg-surface-selected shadow-sm'
						: 'hover:bg-surface-gray-2'
				"
			>
				<!-- Deleting is permanent, so the row asks first rather than acting on
				     one stray click of a hover-only icon. -->
				<template v-if="confirming === session.name">
					<span class="min-w-0 flex-1 truncate text-sm text-ink-gray-7">
						{{ __("Delete this chat?") }}
					</span>
					<button
						class="shrink-0 text-xs text-ink-red-3 hover:underline"
						:disabled="deleting === session.name"
						@click="remove(session.name)"
					>
						{{ deleting === session.name ? __("Deleting…") : __("Delete") }}
					</button>
					<button
						class="shrink-0 text-xs text-ink-gray-5 hover:text-ink-gray-7"
						@click="confirming = null"
					>
						{{ __("Cancel") }}
					</button>
				</template>

				<template v-else>
					<button
						class="min-w-0 flex-1 truncate text-left text-sm"
						:class="
							session.name === sessionName ? 'text-ink-gray-8' : 'text-ink-gray-7'
						"
						@click="pick(session.name)"
					>
						{{ session.title || __("Untitled") }}
					</button>
					<Tip :text="__('Delete')">
						<button
							class="shrink-0 rounded p-0.5 opacity-0 hover:bg-surface-gray-3 focus-visible:opacity-100 group-hover/row:opacity-100"
							:aria-label="__('Delete')"
							@click="confirming = session.name"
						>
							<span
								class="lucide-trash-2 size-3.5 text-ink-gray-5"
								aria-hidden="true"
							></span>
						</button>
					</Tip>
				</template>
			</div>
		</div>
	</aside>
</template>
