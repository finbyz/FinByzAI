<template>
  <div
    class="flex flex-col h-full bg-surface-gray-2 border-r border-outline-gray-2 transition-all duration-200 overflow-hidden"
    :class="[
      isOverlay ? 'fixed inset-y-0 left-0 z-30 w-64 shadow-xl' : 'w-60 flex-shrink-0',
      !store.sidebarOpen && !isOverlay ? 'hidden' : ''
    ]"
  >
    <!-- Top New Chat -->
    <div class="p-3 border-b border-outline-gray-2 flex items-center justify-between gap-2">
      <button
        type="button"
        @click="store.newConversation()"
        class="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-white border border-outline-gray-2 hover:bg-surface-gray-3 text-xs font-semibold text-ink-gray-8 transition-colors shadow-2xs"
      >
        <span>+</span>
        <span>New Chat</span>
      </button>
      <button
        v-if="isOverlay"
        type="button"
        @click="store.sidebarOpen = false"
        class="p-1 rounded text-ink-gray-5 hover:text-ink-gray-8"
      >
        ✕
      </button>
    </div>

    <!-- Search if > 10 items -->
    <div v-if="store.conversations.length > 10" class="px-3 pt-2 pb-1">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="Search chats..."
        class="w-full px-2.5 py-1 text-2xs rounded border border-outline-gray-2 bg-surface-white text-ink-gray-9 focus:outline-none focus:border-outline-blue-3"
      />
    </div>

    <!-- Conversation list grouped by Date -->
    <div
      ref="listRef"
      class="flex-1 overflow-y-auto p-2 space-y-3 focus:outline-none"
      tabindex="0"
      @keydown="handleListKeyDown"
    >
      <div v-for="(group, groupName) in filteredGroups" :key="groupName" class="space-y-1">
        <div v-if="group.length > 0" class="px-2 py-1 text-2xs font-semibold text-ink-gray-4 uppercase tracking-wider">
          {{ groupName }}
        </div>
        <div
          v-for="conv in group"
          :key="conv.name"
          @click="store.selectConversation(conv.name)"
          class="group flex items-center justify-between px-2.5 py-2 rounded-lg cursor-pointer text-xs transition-colors"
          :class="store.activeConversationId === conv.name ? 'bg-surface-white border border-outline-gray-2 text-ink-gray-9 font-medium shadow-2xs' : 'text-ink-gray-7 hover:bg-surface-gray-3'"
        >
          <div class="flex flex-col min-w-0 flex-1">
            <template v-if="editingId === conv.name">
              <input
                v-model="editingTitle"
                type="text"
                class="px-1 py-0.5 text-xs rounded border border-outline-blue-3 bg-surface-white text-ink-gray-9 focus:outline-none"
                @blur="saveRename(conv.name)"
                @keyup.enter="saveRename(conv.name)"
                @click.stop
              />
            </template>
            <template v-else>
              <span class="truncate">{{ conv.title || 'Untitled' }}</span>
              <span class="text-2xs text-ink-gray-4">{{ formatRelativeTime(conv.modified) }}</span>
            </template>
          </div>

          <!-- Actions on hover -->
          <div class="opacity-0 group-hover:opacity-100 flex items-center gap-1 transition-opacity ml-1.5" @click.stop>
            <button
              type="button"
              @click="startRename(conv)"
              class="p-0.5 text-ink-gray-4 hover:text-ink-gray-8"
              title="Rename"
            >
              ✏️
            </button>
            <button
              type="button"
              @click="store.deleteConversation(conv.name)"
              class="p-0.5 text-ink-gray-4 hover:text-ink-red-3"
              title="Delete"
            >
              🗑️
            </button>
          </div>
        </div>
      </div>

      <div v-if="store.conversations.length === 0" class="p-4 text-center text-2xs text-ink-gray-4">
        No past chats yet.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from "vue";
import { store } from "../store";
import { formatRelativeTime, groupConversationsByDate } from "../lib/formatters";

const props = defineProps({
  isOverlay: {
    type: Boolean,
    default: false,
  },
});

const searchQuery = ref("");
const editingId = ref(null);
const editingTitle = ref("");

const filteredGroups = computed(() => {
  let list = store.conversations;
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase();
    list = list.filter((c) => (c.title || "").toLowerCase().includes(q));
  }
  return groupConversationsByDate(list);
});

function startRename(conv) {
  editingId.value = conv.name;
  editingTitle.value = conv.title || "";
}

function saveRename(id) {
  if (editingId.value && editingTitle.value.trim()) {
    store.renameConversation(id, editingTitle.value.trim());
  }
  editingId.value = null;
}

function handleListKeyDown(e) {
  if (store.conversations.length === 0) return;
  const currentIdx = store.conversations.findIndex((c) => c.name === store.activeConversationId);
  if (e.key === "ArrowDown") {
    e.preventDefault();
    const nextIdx = Math.min(store.conversations.length - 1, (currentIdx < 0 ? 0 : currentIdx + 1));
    store.selectConversation(store.conversations[nextIdx].name);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    const prevIdx = Math.max(0, (currentIdx < 0 ? 0 : currentIdx - 1));
    store.selectConversation(store.conversations[prevIdx].name);
  }
}
</script>
