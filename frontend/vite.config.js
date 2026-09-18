import { fileURLToPath, URL } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Vite emits self-contained Frappe bundle entry points. After this build, run
// `bench build --app finbyzai` so Frappe creates the content-hashed assets.
// Real frappe-ui components come from source (`frappe-ui/src/...`) and are
// tree-shaken.
export default defineConfig({
	define: {
		"process.env.NODE_ENV": JSON.stringify("production"),
		__VUE_OPTIONS_API__: "true",
		__VUE_PROD_DEVTOOLS__: "false",
		__VUE_PROD_HYDRATION_MISMATCH_DETAILS__: "false",
	},
	plugins: [vue()],
	resolve: {
		alias: [
			{ find: "@", replacement: fileURLToPath(new URL("./src", import.meta.url)) },
			// frappe-ui's package `exports` map hides its source; alias it so we can import
			// individual components without dragging in the whole index (tiptap, echarts, …).
			{
				find: "frappe-ui/src",
				replacement: fileURLToPath(
					new URL("./node_modules/frappe-ui/src", import.meta.url)
				),
			},
		],
	},
	build: {
		outDir: fileURLToPath(new URL("../finbyzai/public/copilot", import.meta.url)),
		emptyOutDir: true,
		cssCodeSplit: false,
		sourcemap: false,
		target: "es2017",
		lib: {
			entry: fileURLToPath(new URL("./src/main.js", import.meta.url)),
			formats: ["iife"],
			name: "FinbyzCopilot",
			fileName: () => "finbyzai_copilot.bundle.js",
		},
		rollupOptions: {
			output: { assetFileNames: "finbyzai_copilot.bundle.[ext]" },
		},
	},
});
