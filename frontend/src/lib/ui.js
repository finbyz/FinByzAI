// Single import point for the frappe-ui components the panel uses. They are pulled
// from source (aliased in vite.config.js) and tree-shaken into the bundle.
export { default as Button } from "frappe-ui/src/components/Button/Button.vue";
export { default as Badge } from "frappe-ui/src/components/Badge/Badge.vue";
export { default as Spinner } from "frappe-ui/src/components/Spinner.vue";
export { default as Switch } from "frappe-ui/src/components/Switch/Switch.vue";
export { default as TextInput } from "frappe-ui/src/components/TextInput/TextInput.vue";
// No Tooltip: frappe-ui's portals its bubble to document.body, outside the
// #copilot-root prefix every rule in this bundle carries, so it renders unstyled.
// components/Tip.vue is the in-panel stand-in.
export { default as KeyboardShortcut } from "frappe-ui/src/components/KeyboardShortcut.vue";

// Charts: real components with their own tooltips, legends and empty states, rather
// than markup we maintain. AxisChart covers bar / line / area; NumberChart is the KPI
// tile; DonutChart is share-of-total.
export { default as AxisChart } from "frappe-ui/src/components/Charts/AxisChart.vue";
export { default as NumberChart } from "frappe-ui/src/components/Charts/NumberChart.vue";
export { default as DonutChart } from "frappe-ui/src/components/Charts/DonutChart.vue";

// No icon component: icons are lucide classes on a sized span
// (`<span class="lucide-x size-4" />`), which is the icon pack frappe-ui's own
// components moved to. One system, and no SVG in the bundle per icon used.
