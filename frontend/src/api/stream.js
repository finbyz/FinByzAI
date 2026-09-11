// Run streaming. Flow read an SSE response body; this backend runs the turn in a
// background worker and publishes over socketio, so the same call signature —
// `await startRun(body, onEvent, signal)`, resolving when the turn ends — is
// reproduced on top of `frappe.realtime`. The store above this file is unchanged.
import * as api from "@/api/client";
import { __ } from "@/lib/translate";

// How often to ask the server what happened when no event has arrived.
const WATCHDOG_MS = 4000;

const call = (method, args) => frappe.xcall(`finbyzai.copilot.api.${method}`, args || {});

// Approval is a separate event here rather than a tool result, so the shim turns one
// `approval_required` into the three things the store already knows how to render:
// a tool part, that part's result (so its activity line stops shimmering), and the
// question that `done: Paused` carries.
function follow(run, onEvent, signal) {
	return new Promise((resolve) => {
		const channel = `copilot:${run}`;
		let pending = null;
		let settled = false;
		let watchdog = null;
		let lastEventAt = Date.now();

		const finish = () => {
			if (settled) return;
			settled = true;
			clearInterval(watchdog);
			frappe.realtime.off(channel, handler);
			resolve();
		};

		// Realtime is one delivery path, and it can fail: a run that errors in the
		// first 200ms can finish before this subscription exists, socketio may be down,
		// a backgrounded tab can miss events. Flow never needed this — its SSE stream
		// closing *was* the end-of-run signal. Here the only equivalent is to ask.
		// So while nothing is arriving, check the run itself, and if the server says it
		// is over, synthesize the events that never came.
		const check = async () => {
			if (settled || Date.now() - lastEventAt < WATCHDOG_MS) return;
			let state;
			try {
				state = await api.getRun(run);
			} catch {
				return; // transient; the next tick tries again
			}
			if (settled || !state || state.status === "Running") return;

			if (state.status === "Failed" && state.error) {
				onEvent({ type: "error", message: state.error });
			} else if (state.status === "Paused" && state.pending_call) {
				const call = state.pending_call;
				pending =
					call.kind === "question"
						? { key: call.id, prompt: call.summary || call.name, options: [__("Other")], kind: "question" }
						: {
								key: call.id,
								prompt: call.summary || call.name,
								options: ["Approve", "Deny"],
								kind: "approval",
							};
				onEvent({
					type: "tool_started",
					id: call.id,
					name: call.name,
					label: call.label,
					arguments: call.arguments,
				});
				onEvent({ type: "tool_ended", id: call.id, name: call.name, result: JSON.stringify({ awaiting_approval: true }) });
			}

			onEvent({
				type: "done",
				status: state.status,
				output: state.output,
				questions: state.status === "Paused" && pending ? JSON.stringify([pending]) : null,
			});
			finish();
		};

		const handler = (event) => {
			if (!event || !event.type) return;
			lastEventAt = Date.now();

			switch (event.type) {
				// The agent asked something. Flow's ConfirmCard already renders a
				// question with option buttons and a free-text box, so it arrives in
				// the same shape as an approval, only without a tool card.
				case "question":
					pending = {
						key: event.id,
						prompt: event.text,
						options: [...(event.options || []), __("Other")],
						kind: "question",
					};
					break;

				case "approval_required":
					pending = {
						key: event.id,
						prompt: event.summary || event.name,
						options: ["Approve", "Deny"],
						kind: "approval",
					};
					onEvent({
						type: "tool_started",
						id: event.id,
						name: event.name,
						label: event.label,
						context: event.context,
						arguments: event.arguments,
					});
					onEvent({
						type: "tool_ended",
						id: event.id,
						name: event.name,
						result: JSON.stringify({ awaiting_approval: true }),
					});
					break;

				case "done":
					// A failed run carries its message on `done` as well, so the error is
					// shown even if the separate `error` event went missing.
					if (event.status === "Failed" && event.error) {
						onEvent({ type: "error", message: event.error });
					}
					onEvent({
						...event,
						questions: event.status === "Paused" && pending ? JSON.stringify([pending]) : null,
					});
					finish();
					break;

				default:
					onEvent(event);
			}
		};

		frappe.realtime.on(channel, handler);
		watchdog = setInterval(check, WATCHDOG_MS);
		signal?.addEventListener("abort", finish, { once: true });
	});
}

export async function startRun(body, onEvent, signal) {
	const files = body.attachments;
	const res = await call("start_run", {
		input: body.input,
		conversation: body.session || null,
		model: body.model || null,
		attachments: files && files.length ? files : null,
	});
	if (!res || !res.run) throw new Error(__("Could not start the run."));

	// The worker begins the moment start_run's transaction commits, so subscribe
	// before announcing the run rather than after.
	const streamed = follow(res.run, onEvent, signal);
	onEvent({ type: "run_started", name: res.run, session: res.conversation });
	return streamed;
}

// Flow resumed a paused run by posting every answer at once; here each answer is an
// approve/reject on one call, and the free-text case becomes a reject with a reason.
export async function resumeRun(body, onEvent, signal) {
	const answers = body.answers || {};
	const [callId, answer] = Object.entries(answers)[0] || [];
	if (!callId) return;

	const streamed = follow(body.run_name, onEvent, signal);

	if (body.kind === "question") {
		await call("answer_question", { run: body.run_name, call_id: callId, answer });
		return streamed;
	}

	const approved = answer === "Approve";
	await call("approve_run", {
		run: body.run_name,
		call_id: callId,
		decision: approved ? "approve" : "reject",
		values: approved || answer === "Deny" ? null : { reason: answer },
	});
	return streamed;
}
