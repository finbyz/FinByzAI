import frappeUIPreset from "frappe-ui/tailwind";

/** @type {import('tailwindcss').Config} */
export default {
	presets: [frappeUIPreset],
	// Desk's bootstrap defines the same utility names (.py-3, .mb-2, …) with
	// !important; the panel's must be !important too so the #copilot-root prefix can win.
	important: true,
	// No `theme.extend` for type: frappe-ui's preset already ships the whole scale
	// (2xs 11px … 4xl 24px, each with a `text-p-*` paragraph twin carrying looser
	// line-height and tuned letter-spacing). Overriding it, as this config used to,
	// means every size drifts from the rest of the desk.
	// Tailwind v3 ignores `content` declared inside presets, so the frappe-ui
	// source globs must be listed here for its component classes to be emitted.
	content: [
		"./src/**/*.{vue,js}",
		"./node_modules/frappe-ui/src/components/**/*.{vue,js,ts}",
		"./node_modules/frappe-ui/src/composables/**/*.{vue,js,ts}",
		"./node_modules/frappe-ui/src/utils/**/*.{vue,js,ts}",
	],
	plugins: [],
};
