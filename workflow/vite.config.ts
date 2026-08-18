import path from 'path';
import { copyFileSync, renameSync } from 'node:fs'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import proxyOptions from './proxyOptions.js';

function syncFrappeRouteEntry() {
	return {
		name: 'sync-frappe-workflow-route-entry',
		apply: 'build' as const,
		closeBundle() {
			const builtEntry = path.resolve(import.meta.dirname, '../finbyzai/public/workflow/index.html')
			const routeEntry = path.resolve(import.meta.dirname, '../finbyzai/www/workflow.html')
			const temporaryEntry = `${routeEntry}.tmp`

			copyFileSync(builtEntry, temporaryEntry)
			renameSync(temporaryEntry, routeEntry)
		},
	}
}

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [react(), tailwindcss(), syncFrappeRouteEntry()],
	server: {
		port: 8080,
		host: '0.0.0.0',
		proxy: proxyOptions
	},
	resolve: {
		alias: {
			'@': path.resolve(import.meta.dirname, 'src')
		},
	},
	build: {
		outDir: '../finbyzai/public/workflow',
		emptyOutDir: true,
		target: 'es2023',
		rolldownOptions: {
			output: {
				codeSplitting: {
					groups: [
						{ name: 'react-vendor', test: /node_modules\/(react|react-dom|scheduler|react-router)/ },
						{ name: 'workflow-canvas', test: /node_modules\/@xyflow/ },
						{ name: 'frappe-sdk', test: /node_modules\/(frappe-react-sdk|frappe-js-sdk|socket.io)/ },
						{ name: 'forms-validation', test: /node_modules\/(react-hook-form|ajv)/ },
					],
				},
			},
		},
	},
});
