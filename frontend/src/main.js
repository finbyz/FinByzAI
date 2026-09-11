import { createApp, watch } from "vue";
import App from "@/App.vue";
import { useStore } from "@/store";
import { readPanelState, writePanelState } from "@/lib/panelState";
import "@/index.css";

const PANEL_WIDTH = 420;
const MIN_WIDTH = 360;

// Slide-in overlay panel injected into the Frappe desk. The Vue app (with real
// frappe-ui components) mounts inside #copilot-root; all bundle CSS is scoped to
// that id so nothing leaks onto the desk.
class CopilotPanel {
	constructor() {
		const saved = readPanelState();
		this.visible = Boolean(saved.open);
		this._halfWidth = saved.width || PANEL_WIDTH;
		// Fullscreen is the default mode; a saved preference wins on reload.
		this._initialFullscreen = saved.fullscreen ?? true;

		this._mount();
		this._syncTheme();
		this._registerShortcut();
		this._addLauncher();
		this._watchRoute();
		this._watchDeskFocus();

		watch(this.store.sessionName, () => this._persist());
	}

	get fullscreen() {
		return this.store.fullscreen.value;
	}

	_mount() {
		this.store = useStore();
		this.store.fullscreen.value = this._initialFullscreen;

		this.root = document.createElement("div");
		this.root.id = "copilot-root";
		Object.assign(this.root.style, {
			position: "fixed",
			top: "0",
			right: "0",
			width: this.fullscreen ? "100vw" : `${this._halfWidth}px`,
			height: "100vh",
			zIndex: "1040",
			// A restored-open panel renders in place (no slide) so a refresh is seamless.
			transform: this.visible ? "translateX(0)" : "translateX(100%)",
			transition: "transform 0.22s ease, opacity 0.25s ease",
			boxShadow: "-2px 0 16px rgba(0, 0, 0, 0.08)",
		});
		document.body.appendChild(this.root);

		this.app = createApp(App, {
			onClose: () => this.hide(),
			onToggleFullscreen: () => this.toggleFullscreen(),
		});
		this.app.mount(this.root);

		this._addResizeHandle();
	}

	// Thin grab strip on the panel's left edge. Dragging it changes the panel
	// width (anchored to the right). Appended after mount so Vue's render
	// doesn't clobber it.
	_addResizeHandle() {
		const handle = document.createElement("div");
		Object.assign(handle.style, {
			position: "absolute",
			top: "0",
			left: "0",
			width: "6px",
			height: "100%",
			cursor: "ew-resize",
			zIndex: "10",
		});
		this.root.appendChild(handle);

		const onMove = (e) => {
			const max = window.innerWidth - 80;
			const width = Math.min(max, Math.max(MIN_WIDTH, window.innerWidth - e.clientX));
			this.root.style.width = `${width}px`;
			this._halfWidth = width;
			// A manual resize takes the panel out of fullscreen; keep the header icon honest.
			this.store.fullscreen.value = false;
		};
		const onUp = () => {
			document.removeEventListener("mousemove", onMove);
			document.removeEventListener("mouseup", onUp);
			document.body.style.userSelect = "";
			this.root.style.transition = this._savedTransition;
			this._persist();
		};
		handle.addEventListener("mousedown", (e) => {
			e.preventDefault();
			// Drop the width transition while dragging so it tracks the cursor.
			this._savedTransition = this.root.style.transition;
			this.root.style.transition = "none";
			document.body.style.userSelect = "none";
			document.addEventListener("mousemove", onMove);
			document.addEventListener("mouseup", onUp);
		});
	}

	// Navigating the desk means the user wants the desk. A fullscreen panel would hide
	// the page they just opened, so it steps back to the side panel — still open, still
	// mid-conversation, just no longer in the way.
	_watchRoute() {
		const collapse = () => {
			if (this.visible && this.fullscreen) this.toggleFullscreen();
		};
		if (frappe.router?.on) frappe.router.on("change", collapse);
		else $(document).on("page-change", collapse);
	}

	// While a turn is running and the user goes back to working in the desk, fade the
	// panel: the agent keeps going, the events keep arriving, but it stops competing
	// for attention. Any interaction with the panel restores it at once.
	_watchDeskFocus() {
		const dim = (on) => {
			if (this.store.dimmed.value === on) return;
			this.store.dimmed.value = on;
			this.root.style.opacity = on ? "0.45" : "1";
		};

		document.addEventListener(
			"pointerdown",
			(event) => {
				if (!this.visible) return;
				const inside = this.root.contains(event.target);
				// Only fade for a running turn — a finished answer is worth reading.
				if (!inside && this.store.sending.value) dim(true);
				else if (inside) dim(false);
			},
			true
		);

		// Coming back to a finished run should never leave the panel faded.
		watch(this.store.sending, (running) => {
			if (!running) dim(false);
		});
		this.root.addEventListener("mouseenter", () => dim(false));
	}

	// Mirror the desk's light/dark theme onto the panel root so scoped tokens
	// resolve to the right palette.
	_syncTheme() {
		const apply = () => {
			const theme = document.documentElement.getAttribute("data-theme") || "light";
			this.root.setAttribute("data-theme", theme);
		};
		apply();
		new MutationObserver(apply).observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["data-theme"],
		});
	}

	// Two shortcuts on purpose: ctrl+i is the one muscle memory expects, and some
	// browsers (Firefox) swallow ctrl+shift+k for their own console before the page
	// ever sees it.
	_registerShortcut() {
		for (const shortcut of ["ctrl+i", "ctrl+shift+k"]) {
			frappe.ui.keys.add_shortcut({
				shortcut,
				action: () => this.toggle(),
				description: __("Toggle Copilot panel"),
				ignore_inputs: true,
			});
		}
	}

	// A launcher, because a shortcut nobody can see is a feature nobody can find.
	// Bottom-right, hides itself while the panel is open.
	_addLauncher() {
		const button = document.createElement("button");
		button.id = "copilot-launcher";
		button.title = __("Copilot (Ctrl+I)");
		button.innerHTML =
			'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
			'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
			'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
		Object.assign(button.style, {
			position: "fixed",
			right: "18px",
			bottom: "18px",
			zIndex: "1030",
			width: "38px",
			height: "38px",
			display: "flex",
			alignItems: "center",
			justifyContent: "center",
			borderRadius: "999px",
			border: "1px solid var(--border-color, #e2e2e2)",
			background: "var(--card-bg, #fff)",
			color: "var(--text-color, #1f272e)",
			boxShadow: "0 2px 8px rgba(0,0,0,0.10)",
			cursor: "pointer",
		});
		button.addEventListener("click", () => this.toggle());
		document.body.appendChild(button);
		this.launcher = button;
		this._syncLauncher();
	}

	_syncLauncher() {
		if (this.launcher) this.launcher.style.display = this.visible ? "none" : "flex";
	}

	show() {
		this.visible = true;
		this.root.style.transform = "translateX(0)";
		this.store.restoreSession();
		this._syncLauncher();
		this._persist();
	}

	hide() {
		this.visible = false;
		this.root.style.transform = "translateX(100%)";
		this._syncLauncher();
		this._persist();
	}

	toggle() {
		this.visible ? this.hide() : this.show();
	}

	// Expand to the full viewport width, or restore the half-screen width. State
	// lives in the store so the header icon tracks it reactively.
	toggleFullscreen() {
		const next = !this.fullscreen;
		this.store.fullscreen.value = next;
		this.root.style.width = next ? "100vw" : `${this._halfWidth}px`;
		this._persist();
	}

	_persist() {
		writePanelState({
			open: this.visible,
			fullscreen: this.fullscreen,
			width: this._halfWidth,
			session: this.store.sessionName.value,
		});
	}
}

frappe.provide("frappe.copilot");
$(document).on("app_ready", () => {
	frappe.copilot.panel = new CopilotPanel();
	// A console-reachable handle, for when a shortcut is intercepted by the browser.
	window.copilot = {
		open: () => frappe.copilot.panel.show(),
		close: () => frappe.copilot.panel.hide(),
		toggle: () => frappe.copilot.panel.toggle(),
	};
});
