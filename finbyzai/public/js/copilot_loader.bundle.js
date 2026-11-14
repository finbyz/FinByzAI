// Copilot loader: the only finbyzai script on every desk page.
//
// The Copilot panel bundle (Vue + frappe-ui, ~1.5 MB JS + 370 KB CSS) used to be in
// app_include_js, so every desk page for every user downloaded, parsed and ran it,
// though only Copilot users ever see the panel. Now it loads only for users with
// Copilot, after the desk is ready and the browser is idle.
//
// The panel bundle starts itself from an `app_ready` handler (frontend/src/main.js).
// By the time it loads, app_ready has fired, so after loading we call just the
// app_ready handlers the bundle registered - never the other apps' handlers again.
frappe.provide("frappe.copilot");

$(document).on("app_ready", () => {
	if (!frappe.boot?.copilot_enabled || frappe.copilot.loading || frappe.copilot.panel) return;
	frappe.copilot.loading = true;

	const handlers = () => ($._data(document, "events")?.app_ready || []).map((h) => h.handler);
	const load = () => {
		const before = new Set(handlers());
		frappe.require(["finbyzai_copilot.bundle.css", "finbyzai_copilot.bundle.js"]).then(() => {
			for (const handler of handlers()) {
				if (!before.has(handler)) handler.call(document, $.Event("app_ready"));
			}
		});
	};
	const idle = window.requestIdleCallback || ((cb) => setTimeout(cb, 1500));
	idle(load, { timeout: 4000 });
});
