// Data access for the panel. Keeps the call signatures the store already uses, but
// every one of them now talks to finbyzai.copilot.api.* instead of flow.api.*.
import { __ } from "@/lib/translate";

const call = (method, args) => frappe.xcall(`finbyzai.copilot.api.${method}`, args || {});

// There is one agent — the Copilot itself. The picker stays so the composer's
// "Copilot / model" row reads the same, and so agents can be added later.
// Real AI Agent records; the picker falls back to a single "Copilot" entry when the
// site has none configured yet, so the composer never renders empty.
export const loadAgents = () =>
	call("get_agents")
		.then((rows) =>
			(rows || []).map((r) => ({
				name: r.name,
				title: r.title || r.name,
				logo: r.logo,
				hint: r.llm || undefined,
			}))
		)
		.then((rows) => (rows.length ? rows : [{ name: "Copilot", title: "Copilot" }]))
		.catch(() => [{ name: "Copilot", title: "Copilot" }]);

export const loadModels = () =>
	call("get_models").then((rows) =>
		(rows || []).map((r) => ({
			name: r.name,
			title: r.title || r.name,
			provider: r.provider,
			logo: r.logo,
			vision: Boolean(r.supports_vision),
			reasoning: Boolean(r.is_reasoning),
		}))
	);

export const loadHistory = (limit = 30) => call("list_conversations", { limit });

export const deleteConversation = (conversation) => call("delete_conversation", { conversation });

export const loadKnowledgeBases = () =>
	call("get_knowledge_bases").then((rows) =>
		(rows || []).map((r) => ({ name: r.name, title: r.title || r.name, status: r.status }))
	);

// Everything the settings dialog needs in one round trip.
export const loadSettings = () => call("get_settings");

// What the agent can call — read from the live registry, shown in Settings → Tools.
export const loadTools = (args) => call("get_tools", args);

// Ask the chosen model to say one word, and report what the provider replied.
export const testModel = (args) => call("test_model", args);

export const saveSettings = (payload) => call("save_settings", payload);

// A conversation in the shape the store's reload path expects: tool_calls as an
// OpenAI-style JSON string, plus our blocks carried through per message.
export const getSession = async (name) => {
	const data = await call("get_conversation", { conversation: name });
	return {
		name: data.conversation,
		title: data.title,
		agent: data.agent,
		knowledge_base: data.knowledge_base,
		model: data.model,
		attachments: [],
		active_run: data.active_run || null,
		// { run: { duration, status } } — so a reloaded turn still says how long it took
		runs: data.runs || {},
		messages: (data.messages || []).map((m) => ({
			role: m.role,
			content: m.content,
			tool_call_id: m.tool_call_id,
			run: m.run,
			blocks: m.blocks || [],
			tool_calls: m.tool_calls
				? JSON.stringify(
						m.tool_calls.map((c) => ({
							id: c.id,
							type: "function",
							function: { name: c.name, arguments: JSON.stringify(c.args || {}) },
							// The server stamps these (api._labelled) so a reloaded run
							// shows the same labels the live one did.
							label: c.label || null,
							context: c.context || null,
						}))
					)
				: null,
		})),
	};
};

// A run left waiting for approval, rebuilt as one question so the reload path can
// re-render its confirm card.
export const getPausedRun = async (session) => {
	const data = await call("get_conversation", { conversation: session });
	const active = data.active_run;
	if (!active || active.status !== "Paused" || !active.pending_call) return [];
	const pending = active.pending_call;
	return [
		{
			name: active.run,
			questions: JSON.stringify([
				{
					key: pending.id,
					prompt: pending.summary || pending.name,
					options: ["Approve", "Deny"],
					tool: { id: pending.id, name: pending.name, arguments: pending.arguments || {} },
				},
			]),
		},
	];
};

export const recoverSession = (session) => call("recover_conversation", { conversation: session });

// The run's state, for the stream's watchdog. Cheap enough to poll.
export const getRun = (run) => call("get_run", { run });

export const stopRun = (run_name) => call("stop_run", { run: run_name });

// Approvals arrive as their own event, so the panel never needs the tool→confirm map.
export const getAgentTools = () => Promise.resolve({});

// Upload a file as private and return the created File doc.
export async function uploadFile(file) {
	const form = new FormData();
	form.append("file", file, file.name);
	form.append("is_private", "1");

	const resp = await fetch("/api/method/upload_file", {
		method: "POST",
		headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		body: form,
	});
	const data = await resp.json().catch(() => ({}));
	if (!resp.ok) throw new Error(serverMessage(data) || __("Upload failed ({0})", [resp.status]));
	return data.message;
}

// Flow validated attachments server-side before a turn; here the extract_file_content
// tool validates at read time, so staging is just the chip metadata.
export const attachFile = (file) =>
	frappe
		.xcall("frappe.client.get_value", {
			doctype: "File",
			filters: { name: file },
			fieldname: ["name", "file_name", "file_size"],
		})
		.then((doc) => ({ file: doc.name, file_name: doc.file_name, file_size: doc.file_size }));

// Extract the human-readable message from a frappe error body.
export function serverMessage(data) {
	try {
		const msgs = JSON.parse(data._server_messages || "[]");
		if (msgs.length) return JSON.parse(msgs[0]).message;
	} catch {
		// fall through to other error fields
	}
	return data.exception || data._error_message || null;
}
