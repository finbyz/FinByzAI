<template>
  <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-3.5 shadow-2xs">
    <div class="text-xs font-medium text-ink-gray-5 truncate mb-1">
      {{ block.label }}
    </div>
    <div class="flex items-baseline gap-2">
      <span class="text-2xl font-semibold text-ink-gray-9 tracking-tight">
        <span v-if="block.unit" class="text-base font-normal text-ink-gray-5 mr-0.5">{{ block.unit }}</span>{{ formattedValue }}
      </span>
      <span
        v-if="block.delta !== undefined && block.delta !== null"
        class="inline-flex items-center text-xs font-medium px-1.5 py-0.5 rounded"
        :class="block.delta >= 0 ? 'bg-surface-green-2 text-ink-green-4' : 'bg-surface-red-2 text-ink-red-4'"
      >
        <span>{{ block.delta >= 0 ? '↑' : '↓' }}</span>
        <span>{{ formattedDelta }}</span>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { formatNumber, formatFloat } from "../../lib/formatters";

const props = defineProps({
  block: {
    type: Object,
    required: true,
  },
});

const formattedValue = computed(() => {
  const val = props.block.value;
  return typeof val === "number" ? formatFloat(val) : String(val ?? "");
});

const formattedDelta = computed(() => {
  const delta = props.block.delta;
  if (delta === null || delta === undefined) return "";
  return typeof delta === "number" ? Math.abs(delta).toLocaleString() : String(delta);
});
</script>
