<template>
  <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-3 shadow-2xs">
    <div v-if="chartConfig" class="w-full h-64">
      <AxisChart :config="chartConfig" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";
import AxisChart from "frappe-ui/src/components/Charts/AxisChart.vue";

const props = defineProps({
  block: {
    type: Object,
    required: true,
  },
});

const chartConfig = computed(() => {
  if (!props.block || !props.block.rows) return null;
  const series = (props.block.series || []).map((s) => ({
    name: s.label || s.key,
    type: "bar",
    key: s.key,
  }));

  return {
    data: props.block.rows,
    title: "",
    swapXY: Boolean(props.block.horizontal),
    xAxis: {
      key: props.block.x,
      type: "category",
    },
    yAxis: {
      type: "value",
    },
    series,
  };
});
</script>
