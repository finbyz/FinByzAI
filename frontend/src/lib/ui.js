// Single import point for the frappe-ui components the panel uses. They are pulled
// from source (aliased in vite.config.js) and tree-shaken into the bundle.
export { default as Button } from "frappe-ui/src/components/Button/Button.vue";
export { default as Badge } from "frappe-ui/src/components/Badge/Badge.vue";
export { default as Spinner } from "frappe-ui/src/components/Spinner.vue";
export { default as Switch } from "frappe-ui/src/components/Switch/Switch.vue";
export { default as Tooltip } from "frappe-ui/src/components/Tooltip/Tooltip.vue";
export { default as KeyboardShortcut } from "frappe-ui/src/components/KeyboardShortcut.vue";

// Charts: real components with their own tooltips, legends and empty states, rather
// than markup we maintain. AxisChart covers bar / line / area; NumberChart is the KPI
// tile; DonutChart is share-of-total.
export { default as AxisChart } from "frappe-ui/src/components/Charts/AxisChart.vue";
export { default as NumberChart } from "frappe-ui/src/components/Charts/NumberChart.vue";
export { default as DonutChart } from "frappe-ui/src/components/Charts/DonutChart.vue";

// FeatherIcon stays only while Flow's remaining components still import it; new code
// uses lucide classes (`<span class="lucide-x size-4" />`), which is where frappe-ui
// itself has moved.
export { default as FeatherIcon } from "frappe-ui/src/components/FeatherIcon.vue";
