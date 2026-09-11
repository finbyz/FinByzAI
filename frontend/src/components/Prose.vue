<script setup>
import { ref, watch, onUnmounted } from "vue";

// The assistant's own words. Markdown, streamed.
//
// Two constraints shape this: the text arrives a token at a time, and it is
// model output, which means untrusted. So the full string is re-parsed on a
// throttle — per-token parsing of an accumulating string is quadratic — and the
// resulting HTML is walked against an allow-list before it is injected.
//
// It takes the whole part rather than `part.text` so only this component
// re-renders per token.
const props = defineProps({ part: { type: Object, required: true } });

const THROTTLE_MS = 100;
const html = ref("");
let timer = 0;
let parsedAt = 0;

// Everything markdown itself can emit. Anything else is unwrapped to its text.
const KEEP = new Set(
	("p br hr h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code em strong del " +
		"table thead tbody tr th td a img").split(" ")
);
// Unwrapping these would surface their contents as text; drop them whole.
const DROP = new Set("script style iframe object embed form link meta base".split(" "));
// A leading "/" allows a desk path but not "//host", which is off-origin.
const SAFE_HREF = /^(https?:|mailto:|#|\/(?!\/))/i;
const KEEP_ATTR = { a: "href", img: "src" };

function sanitize(root) {
	for (const el of [...root.querySelectorAll("*")]) {
		const tag = el.tagName.toLowerCase();
		if (DROP.has(tag)) {
			el.remove();
			continue;
		}
		if (!KEEP.has(tag)) {
			el.replaceWith(...el.childNodes);
			continue;
		}
		const url = KEEP_ATTR[tag];
		for (const { name, value } of [...el.attributes]) {
			const attr = name.toLowerCase();
			const allowed =
				attr === "title" || attr === "alt" || (attr === url && SAFE_HREF.test(value));
			if (!allowed) el.removeAttribute(name);
		}
		// The panel floats over the desk; a link must not navigate it away.
		if (tag === "a") {
			el.setAttribute("target", "_blank");
			el.setAttribute("rel", "noopener noreferrer");
		}
	}
	// A wide table scrolls in its own box rather than widening the panel.
	for (const table of [...root.querySelectorAll("table")]) {
		const box = root.ownerDocument.createElement("div");
		box.className = "prose-scroll";
		table.replaceWith(box);
		box.append(table);
	}
}

const escape = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

function parse() {
	timer = 0;
	parsedAt = performance.now();
	let text = props.part.text || "";
	// Close a fence the stream hasn't closed yet, so code renders as code while
	// it is still arriving instead of flashing as raw text with backticks.
	if ((text.match(/```/g) || []).length % 2) text += "\n```";

	if (!window.frappe?.markdown) {
		html.value = `<p>${escape(text)}</p>`;
		return;
	}
	const doc = new DOMParser().parseFromString(frappe.markdown(text), "text/html");
	sanitize(doc.body);
	html.value = doc.body.innerHTML;
}

function schedule() {
	if (timer) return; // a pending parse will read the freshest text when it fires
	const since = performance.now() - parsedAt;
	if (since >= THROTTLE_MS) parse();
	else timer = setTimeout(parse, THROTTLE_MS - since);
}

watch(() => props.part.text, schedule, { immediate: true });
onUnmounted(() => timer && clearTimeout(timer));
</script>

<template>
	<!-- eslint-disable-next-line vue/no-v-html -- sanitized above -->
	<div class="copilot-prose" v-html="html"></div>
</template>
