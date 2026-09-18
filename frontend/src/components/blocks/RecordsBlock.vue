<template>
  <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-2 shadow-2xs space-y-1.5">
    <div class="px-2 py-1 text-2xs font-semibold text-ink-gray-5 uppercase tracking-wider flex justify-between">
      <span>{{ block.doctype }} ({{ block.rows?.length || 0 }})</span>
    </div>
    <div class="space-y-1">
      <a
        v-for="(row, idx) in block.rows"
        :key="idx"
        :href="`/app/${encodeURIComponent(block.doctype)}/${encodeURIComponent(row.name || row.id || '')}`"
        target="_blank"
        class="flex items-center justify-between p-2 rounded hover:bg-surface-gray-2 border border-transparent hover:border-outline-gray-2 transition-all group"
      >
        <div class="flex flex-col min-w-0">
          <span class="text-xs font-medium text-ink-blue-3 group-hover:underline truncate">
            {{ row.title || row.name || row.id || `Record #${idx + 1}` }}
          </span>
          <span v-if="row.subtitle || row.description" class="text-2xs text-ink-gray-5 truncate">
            {{ row.subtitle || row.description }}
          </span>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0 text-xs text-ink-gray-6">
          <span v-if="row.status" class="px-1.5 py-0.5 rounded bg-surface-gray-3 text-2xs font-medium">
            {{ row.status }}
          </span>
          <span class="text-ink-gray-4 group-hover:text-ink-gray-7">→</span>
        </div>
      </a>
    </div>
  </div>
</template>

<script setup>
defineProps({
  block: {
    type: Object,
    required: true,
  },
});
</script>
