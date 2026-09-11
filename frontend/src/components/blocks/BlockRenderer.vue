<template>
  <div class="my-3 space-y-2">
    <KpiBlock v-if="block.type === 'kpi'" :block="block" />
    <TableBlock v-else-if="block.type === 'table'" :block="block" />
    <LineBlock v-else-if="block.type === 'line'" :block="block" />
    <BarBlock v-else-if="block.type === 'bar'" :block="block" />
    <RecordsBlock v-else-if="block.type === 'records'" :block="block" />
    <div v-else class="text-xs text-ink-gray-5 font-mono p-2 bg-surface-gray-2 rounded border border-outline-gray-2">
      Unsupported block type: {{ block.type }}
    </div>
  </div>
</template>

<script setup>
import KpiBlock from "./KpiBlock.vue";
import TableBlock from "./TableBlock.vue";
import LineBlock from "./LineBlock.vue";
import BarBlock from "./BarBlock.vue";
import RecordsBlock from "./RecordsBlock.vue";

defineProps({
  block: {
    type: Object,
    required: true,
  },
});
</script>
