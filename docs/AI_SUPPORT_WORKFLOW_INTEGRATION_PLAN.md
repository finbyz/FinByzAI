# FinbyzAI AI Support and Workflow Integration Plan

| Document field | Value |
|---|---|
| Status | Proposed implementation plan |
| Assessment date | 2026-08-24 |
| Application | FinbyzAI |
| Primary business record | ERPNext Issue |
| Supporting records | Communication, Contact, Lead, Customer, ToDo, Comment |
| Reference product | HubSpot workflows and Customer Agent |
| Scope | AI-assisted support workflows, guarded automation, and human handoff |

## 1. Purpose

This document defines how FinbyzAI can add HubSpot-style AI support capabilities to the existing workflow builder without copying HubSpot branding, proprietary assets, or source code. The goal is behavioral parity where it is useful for ERP support teams:

- understand an incoming support request;
- summarize the record and conversation;
- classify intent, priority, language, sentiment, and risk;
- retrieve an answer from approved knowledge;
- draft or send a response under explicit policy;
- route work to the correct person or team;
- hand off safely when AI should not continue;
- expose every decision, output, source, and side effect in workflow history.

The recommended design does **not** introduce a second workflow engine or a separate AI product. It extends the current FinbyzAI workflow runtime and reuses the existing AI Agent, Knowledge Base, LLM, structured-output, permissions, execution ledger, and workflow-history foundations.

## 2. Executive recommendation

Build two related capabilities in phases:

1. **AI workflow actions** — deterministic workflow steps that summarize, classify, extract, draft, or answer from knowledge. They return typed outputs that branches and later actions can use.
2. **AI support agent** — a stateful support participant assigned to an Issue or Communication thread. It can answer from approved knowledge and request human handoff, but it operates under tighter channel, tool, confidence, and approval policies.

Start with assistive actions that never send or mutate anything on their own. A generated draft should flow into an existing, permission-checked action such as Send Email, Update Record, Create ToDo, Add Comment, or Assign User. This boundary is important:

```text
Incoming support event
        │
        ▼
AI action: understand or draft
        │ typed, validated output
        ▼
Branch / approval / policy check
        │
        ▼
Existing workflow action performs the side effect
```

This makes retries, permissions, simulations, audits, and failures understandable. It also prevents a prompt or retrieved document from silently becoming an instruction to alter ERP data.

## 3. What HubSpot behavior should be adopted

HubSpot provides two useful patterns rather than one monolithic “AI node”:

- Workflow AI actions can summarize or transform record data, and their output can be reused by later workflow actions. HubSpot documents this for its [Summarize record action](https://knowledge.hubspot.com/workflows/use-hubspots-ai-to-summarize-data-in-workflows) and [Data Agent actions](https://knowledge.hubspot.com/workflows/use-ai-to-manage-data-in-workflows).
- Its Customer Agent uses configured content sources, can be assigned through routing and workflows, can take configured actions, and can hand a conversation to a human. HubSpot documents the [Customer Agent setup](https://knowledge.hubspot.com/customer-agent/set-up-the-customer-agent), [handoff process](https://knowledge.hubspot.com/customer-agent/set-up-and-customize-the-customer-agents-handoff-process), and [channel deployment](https://knowledge.hubspot.com/customer-agent/deploy-the-customer-agent-to-channels).

The transferable product principles are:

- AI steps have a narrow purpose and plain-language setup.
- Generated results become reusable workflow data.
- Knowledge sources are explicitly selected and governed.
- Human handoff is a first-class result, not an exception hidden in logs.
- Deployment can be limited by channel, audience, issue type, operating hours, or percentage.
- Teams can preview and test behavior before publication.
- Performance includes resolution, escalation, knowledge-gap, and failure signals.

FinbyzAI should implement these principles using ERPNext Issue, Frappe Communication, current workflow semantics, and installed integrations.

## 4. Current FinbyzAI foundation

FinbyzAI already contains most low-level building blocks, but they are not yet exposed as production workflow actions.

| Capability | Current state | Reuse decision |
|---|---|---|
| AI agents | `AI Agent` selects an LLM/provider, knowledge base, tools, memory, limits, and structured output | Reuse after permission and versioning hardening |
| Structured output | Agent services accept JSON output schemas | Reuse for typed workflow outputs |
| Knowledge retrieval | Knowledge Base supports documents, web links, notes, chunking, embeddings, and vector stores | Reuse with source visibility, publication, and citation controls |
| Agent memory | Buffer, window, and summary-style memory foundations exist | Do not use user-global memory for support; scope it to the Issue/thread |
| AI tools | Function and provider-built-in tools exist, including confirmation metadata | Restrict workflow use to explicit allowlists and execution policies |
| Workflow output binding | Existing node outputs can feed conditions and later actions | Extend with typed AI output paths |
| Durable execution | Versioned runs, tokens, timers, effect ledger, outbox, incidents, dead letters, and metrics exist | Reuse for AI attempts and downstream side effects |
| Support events | Communication replies and email events already enter the workflow event system | Reuse for Issue and conversation workflows |
| Support records | ERPNext Issue includes customer/contact/lead links, status, priority, type, SLA fields, resolution, and portal origin | Use Issue as the canonical support case |
| Human work | Create ToDo, add comment, notifications, assignment, email, and record updates exist | Reuse for review and handoff |
| Channel integrations | Customer Portal, Aircall, email, FinbyzReach, Asana, and outbound webhooks are installed or available | Add adapters only where the source exposes a stable event or API |
| AI workflow nodes | No published AI action contract exists in the workflow registry/runtime/UI | Implement |
| Support-agent session | No Issue-scoped AI session and handoff lifecycle exists | Implement |
| AI audit and cost trail | Workflow history does not yet expose prompt/model/knowledge versions, tokens, latency, citations, or confidence | Implement |

Relevant local implementation references:

- [AI Agent schema](../finbyzai/ai/doctype/ai_agent/ai_agent.json)
- [AI Agent controller](../finbyzai/ai/doctype/ai_agent/ai_agent.py)
- [Agent service](../finbyzai/ai/agent/agent_service.py)
- [Universal agent](../finbyzai/ai/agent/universal_agent.py)
- [Knowledge Base controller](../finbyzai/ai/doctype/knowledge_base/knowledge_base.py)
- [AI Tool schema](../finbyzai/ai/doctype/ai_tool/ai_tool.json)
- [Workflow node registry](../finbyzai/workflow_builder/registry.py)
- [Workflow engine](../finbyzai/workflow_builder/engine.py)
- [Workflow event integrations](../finbyzai/workflow_builder/integrations.py)
- [ERPNext Issue controller](../../erpnext/erpnext/support/doctype/issue/issue.py)

## 5. Target support experience

### 5.1 Canonical support object

Use **ERPNext Issue** as the case record. It already represents the operational lifecycle and includes:

- subject and description;
- customer, contact, lead, and sender identity;
- status, priority, and issue type;
- service-level agreement and response/resolution timing;
- portal origin;
- resolution details;
- linked Communication timeline.

The AI must not guess relationships among Lead, Contact, Customer, Opportunity, or a transaction. It should use explicit links and the same permitted relationship catalogue used by the workflow builder.

### 5.2 Support entry points

The initial production scope should support:

- new Issue created from Desk, email, or Customer Portal;
- inbound Communication linked to an Issue;
- a customer reply on an existing Issue;
- manual invocation by a support user;
- selected Aircall events when a call is matched to the Issue/customer and transcript or summary data is actually available.

Future adapters may support chat, SMS, social messaging, or other channels, but they must normalize into an Issue/Communication event rather than create channel-specific AI logic.

### 5.3 Core user journeys

#### Triage a new Issue

1. Issue creation enrolls the record.
2. AI summarizes the request and classifies issue type, language, sentiment, urgency, and safety risk.
3. A normal If/else branch applies business policy.
4. High-risk or low-confidence cases create a ToDo, assign a team/user, and notify a human.
5. Other cases continue to draft or knowledge-answer actions.

#### Draft a grounded response

1. AI reads only permitted Issue fields and linked customer-visible Communications.
2. The selected Knowledge Base supplies approved information.
3. AI returns an answer, citations, confidence, and missing-information questions.
4. A human reviews the draft during the first rollout phase.
5. The existing Send Email action sends the approved content and links the Communication to the Issue.

#### Respond automatically under policy

Automatic sending is enabled only when all configured conditions pass, for example:

- supported issue type;
- approved channel and audience;
- confidence at or above the configured threshold;
- at least one valid knowledge citation where grounding is required;
- no security, legal, billing dispute, cancellation, refund, abuse, or personally sensitive risk;
- no explicit request for a human;
- not outside the workflow's communication policy;
- no earlier failed automatic response in the same session.

#### Hand off to a person

A handoff should have a visible, structured reason. It can:

- stop further AI replies for the support session;
- assign the Issue to a user or team;
- create an urgent ToDo;
- add an internal timeline comment containing the summary and attempted answer;
- notify the assigned user;
- optionally send a customer-facing acknowledgement;
- continue measuring the SLA without losing workflow history.

## 6. Workflow-builder design

Add an **AI** category to the action catalogue. Use task-oriented labels rather than exposing model terminology first.

### 6.1 Initial actions

| Catalogue action | Purpose | Default side effect |
|---|---|---|
| Summarize support record | Summarize an Issue, selected fields, and permitted thread messages | None |
| Classify and extract | Produce typed intent, issue type, priority, language, sentiment, entities, and risk flags | None |
| Draft support reply | Create a reply draft using record/thread context | None |
| Answer from knowledge | Generate a grounded answer with source references and confidence | None |
| Run support agent | Process one conversation turn under an Issue-scoped policy | None unless an explicit response policy permits sending |

These can be friendly catalogue aliases backed by two runtime contracts:

- `action.ai_generate` for stateless summarize/classify/extract/draft/grounded-answer work;
- `action.ai_support_agent` for a stateful support turn and possible handoff request.

Do not add separate AI versions of Update Record, Send Email, Create ToDo, Add Comment, or Notify User. The existing actions remain the only normal path for those side effects.

### 6.2 Simple inspector

The setup panel should reveal fields progressively:

1. **What should AI do?** — summarize, classify/extract, draft, or answer from knowledge.
2. **What may it read?** — enrolled Issue, selected fields, linked Communications, and explicitly selected linked records.
3. **How should it respond?** — instructions, tone, language behavior, and output fields.
4. **Knowledge** — optional or required approved Knowledge Base.
5. **When AI is uncertain** — continue on a low-confidence path, hand off, or fail the step.
6. **Advanced** — model profile, maximum tokens, timeout, allowed tools, and retention.

The panel should show:

- a human-readable context summary;
- which fields leave the site/provider boundary;
- estimated token/cost range when the provider exposes pricing;
- a test button using a selected real record without executing downstream actions;
- generated preview, parsed output, citations, and validation errors;
- a mandatory failure-path choice for published workflows.

### 6.3 Canvas card

An AI node should use the same minimal card structure as other actions:

```text
AI
Classify and extract
Issue type, priority, language, sentiment
Knowledge: Support KB · Low confidence → Human review
```

Badges should communicate only actionable state:

- `Needs setup`
- `Knowledge required`
- `Human approval`
- `Low-confidence path missing`
- `Tested`
- `Runtime issue`

### 6.4 Typed outputs

AI output must be strict JSON validated against the published node schema. Later branches and actions should see friendly output selectors such as:

```json
{
  "summary": "Customer cannot complete checkout after authentication.",
  "intent": "checkout_failure",
  "issue_type": "Portal",
  "priority": "High",
  "language": "en",
  "sentiment": "frustrated",
  "risk_flags": [],
  "confidence": 0.91,
  "draft_reply": "...",
  "citations": [
    {
      "source_id": "KB-DOC-0042",
      "title": "Checkout troubleshooting",
      "locator": "section: payment-session"
    }
  ],
  "handoff": false,
  "handoff_reason": null
}
```

The schema may vary by action, but published workflows pin the schema. A changed Agent or prompt must not silently alter an active workflow's output contract.

## 7. Runtime contracts

### 7.1 `action.ai_generate`

Proposed configuration:

| Field | Meaning |
|---|---|
| `mode` | `summarize`, `classify_extract`, `draft_reply`, or `grounded_answer` |
| `ai_profile` | Published AI configuration/version |
| `input_source` | Enrolled record or compatible earlier output |
| `field_allowlist` | Explicit fields supplied as context |
| `include_thread` | Whether permitted linked Communications are included |
| `thread_limit` | Maximum messages/chars/tokens selected before summarization |
| `knowledge_base` | Published knowledge snapshot or current approved version |
| `instructions` | Workflow-owned task instructions |
| `output_schema` | Strict JSON Schema pinned in the workflow version |
| `confidence_threshold` | Threshold used by the dedicated low-confidence output |
| `timeout_seconds` | Bounded provider timeout |
| `retention_policy` | Allowed storage of request/response evidence |

Stable outputs:

- `result` — validated structured object;
- `text` — optional display text;
- `confidence` — normalized value when the mode supports it;
- `citations` — normalized source references;
- `model` and `provider` — non-secret execution identity;
- `usage` — input/output tokens and estimated cost where supported;
- `attempt_id` — audit reference;
- `status` — `completed`, `low_confidence`, or `failed`.

The node should expose normal success, low-confidence, and failure/timeout paths. A draft may save with incomplete paths; publication must enforce the configured production policy.

### 7.2 `action.ai_support_agent`

This action processes one support turn. Its configuration adds:

- support session policy;
- allowed channels;
- operating-hours behavior;
- maximum automatic turns before handoff;
- required human-handoff conditions;
- read-only and separately approved write/tool allowlists;
- whether a response is draft-only, approval-required, or eligible for automatic send.

Stable outputs add:

- `answer`;
- `decision` — `draft`, `respond`, `ask_clarification`, `handoff`, or `no_action`;
- `handoff_reason`;
- `knowledge_gaps`;
- `session_id`;
- `turn_number`.

Even when `decision` is `respond`, a downstream Send Email/communication action should perform delivery. The support-agent node decides; the workflow action executes.

### 7.3 Idempotency and retries

AI generation is treated as a bounded external effect:

- derive an effect key from workflow version, run token, node, attempt, AI profile version, input hash, and knowledge version;
- store the validated result before scheduling downstream actions;
- reuse the stored result on an identical retry;
- retry only known transient provider failures with bounded backoff;
- never repeat a downstream side effect because the AI provider timed out;
- route permanent parse, safety, permission, or provider failures to the configured failure path and incident trail.

Agent tools that can create or mutate external data must not run inside an automatically retried generation call. Prefer read-only retrieval tools and execute mutations through existing durable workflow nodes.

## 8. Data model proposals

These are proposed contracts, not finalized DocTypes.

### 8.1 AI Profile Version

An immutable published representation of an AI Agent configuration:

- source AI Agent;
- provider and model identifiers;
- system/task prompt versions;
- output schema;
- selected Knowledge Base version;
- allowed tools and confirmation policy;
- temperature/token/iteration limits;
- data-retention policy;
- content hash and publication metadata.

Editable `AI Agent` documents can remain the authoring surface, but active workflow versions must reference an immutable profile version.

### 8.2 AI Workflow Attempt

Either extend the existing Automation Action Attempt evidence or create a linked record containing:

- workflow, version, run, token, and node;
- AI profile/model/provider;
- context hash and redacted context manifest;
- knowledge version and normalized citations;
- status, latency, token use, and estimated cost;
- validated output or redacted output reference;
- safety result, confidence, retry count, and error category.

Raw prompts and customer content should not be copied into logs by default. Storage follows an explicit retention policy.

### 8.3 AI Support Session

Issue-scoped state for multiple conversation turns:

- Issue and customer identity;
- channel and latest Communication;
- AI profile version;
- state: active, awaiting customer, awaiting human, handed off, resolved, stopped;
- turn count and timestamps;
- last confidence and decision;
- handoff reason and assignee/team;
- compact memory summary with a source-message watermark;
- automatic-response policy and stop reason.

Memory must be keyed to this session/Issue, not merely to the current Frappe user and agent.

### 8.4 Knowledge publication metadata

Knowledge used for support needs:

- draft/approved/retired state;
- audience and role visibility;
- valid-from/valid-until dates;
- source owner;
- version/hash;
- last indexed and last reviewed timestamps;
- whether customer-facing quotation is allowed.

## 9. Security, permissions, and safety

### 9.1 Permission model

- Evaluate read access using the workflow execution user and the enrolled record.
- Resolve linked records only through explicit permitted links.
- Apply a field allowlist and field-level read permissions before constructing AI context.
- Exclude passwords, authentication secrets, API keys, private files, hidden/internal notes, and protected fields.
- Apply normal Frappe write permission and workflow authoring rules to downstream actions.
- Never grant the model a broader role than the workflow execution user.
- Keep site/tenant data isolated in provider requests, storage, cache, and vector retrieval.

### 9.2 Prompt-injection boundary

Customer messages, email HTML, attachments, web pages, and retrieved knowledge are untrusted data. The runtime must:

- separate system instructions from customer and retrieved content;
- state that instructions inside content must not be followed;
- prevent content from selecting or configuring tools;
- allow only server-defined tools with validated inputs;
- re-check permissions inside every tool call;
- cap retrieval results, content length, and recursive tool iterations;
- validate structured output after the model response;
- use normal workflow policy, not model prose, to approve a side effect.

### 9.3 Mandatory handoff conditions

The default support policy should hand off when:

- the customer asks for a human;
- confidence is below threshold;
- required knowledge is missing or conflicting;
- identity or record correlation is ambiguous;
- the request concerns security, fraud, legal threats, sensitive personal data, or abuse;
- the requested refund, cancellation, credit, write-off, order change, or account change exceeds policy;
- a provider/tool fails;
- the maximum automatic turns are reached;
- the Issue is reopened after an AI resolution;
- the workflow or agent is paused/disabled.

These defaults may be tightened per workflow but should not be silently weakened by a prompt.

## 10. Knowledge and citations

Grounded support answers need more than vector similarity:

1. Filter documents by approved state, audience, date, and execution permissions.
2. Retrieve a bounded set of chunks.
3. Preserve source document/version and chunk locator.
4. Require the answer action to return normalized citations.
5. Verify that each citation came from the retrieved set.
6. Route unsupported or conflicting answers to handoff.
7. Record unanswered questions as knowledge gaps.

The customer-facing message may render friendly source links only when the source is safe for that customer. Internal evidence remains available in the AI attempt and workflow history.

## 11. Human review and approval

Phase one should use a clear approval loop:

```text
Draft support reply
        │
        ▼
Create review task / approval token
        │
  ┌─────┴─────┐
Approve      Edit or reject
  │             │
  ▼             ▼
Send reply    Save feedback + handoff
```

The review surface should display:

- relevant Issue and recent thread context;
- draft answer;
- citations;
- confidence and risk flags;
- why the workflow selected this path;
- Approve and send, Edit and send, Reject, and Assign actions.

Reviewer edits are valuable evaluation data, but they must be stored with access controls and must not automatically retrain a model.

## 12. Testing in the builder

Extend the current non-mutating workflow test drawer. For an AI action it should:

- select a real permitted Issue;
- show exactly which fields and messages will be sent;
- use the pinned AI profile and knowledge version;
- execute AI generation only after an explicit test confirmation because it may incur cost;
- prevent email, record update, assignment, webhook, Asana, SMS, or other downstream effects;
- show raw structured output in an expandable technical section;
- show friendly result, citations, confidence, token use, latency, and estimated cost;
- highlight the predicted success, low-confidence, handoff, or failure path;
- allow saving the test case as a redacted regression fixture.

Workflow simulation should support a deterministic fixture mode so ordinary backend/frontend tests do not call live models.

## 13. History, operations, and measurement

### 13.1 Per-run evidence

Workflow history should answer:

- which AI profile, provider, and model ran;
- which record/thread and knowledge version were used;
- which allowed context fields were included;
- what validated result was returned;
- which path was selected and why;
- whether a person approved or edited the output;
- which downstream action performed the actual side effect;
- what failed, retried, timed out, or caused a handoff.

### 13.2 Operational metrics

Track at least:

- AI attempts, success, parse failure, timeout, and provider error;
- latency percentiles;
- input/output tokens and estimated cost;
- confidence distribution;
- grounded answers and citation coverage;
- handoff rate and reasons;
- draft approval, edit, and rejection rates;
- median reviewer edit distance;
- first-response and resolution time;
- automatic resolution and reopen rates;
- SLA breach rate;
- customer satisfaction where available;
- top missing knowledge topics;
- per-workflow and per-AI-profile performance.

Do not optimize only for fewer human handoffs. False resolution, unsafe answers, customer re-contact, and reopened Issues must remain visible.

## 14. Example workflows

### 14.1 New Issue triage

```text
Trigger: Issue created
  → AI: Classify and extract
  → If/else
      Security/legal/high-risk → Assign support lead → Create urgent ToDo → Notify
      Low confidence           → Assign triage team → Add AI summary comment
      Billing                  → Set Issue Type = Billing → Assign billing team
      Technical                → Set Issue Type = Technical → Continue to draft
      None                     → General support queue
```

### 14.2 Knowledge-grounded draft

```text
Trigger: Customer replied
  → AI: Summarize support record
  → AI: Answer from Support Knowledge Base
  → If confidence and citations meet policy
      Yes → Human approval → Send Email → Update Issue status to Replied
      No  → Create ToDo → Assign human → Add internal summary comment
```

### 14.3 Guarded automatic response pilot

```text
Trigger: New portal Issue
  → Filters: supported issue types + pilot customer segment
  → AI: Answer from knowledge
  → If policy passes
      Yes → Send Email → Record AI response metadata
      No  → Human handoff
```

Start the pilot with a narrow, low-risk issue type and a small audience. Expand only after measuring answer quality, handoffs, reopens, and customer outcomes. This mirrors HubSpot's guidance to deploy selectively and monitor escalations and knowledge gaps rather than enabling an agent everywhere at once.

### 14.4 Aircall-assisted follow-up

```text
Trigger: Completed inbound Aircall call matched to Issue/customer
  → If transcript/structured call notes exist
      Yes → AI: Summarize call → Add internal comment → Create follow-up ToDo
      No  → Create ToDo requesting call notes
```

Do not infer a transcript from a Call Log that contains only metadata.

## 15. Implementation sequence

### Phase 0 — Harden the existing AI foundation

- Add explicit server-side permission checks to agent, knowledge, and tool execution paths.
- Define approved workflow-safe tools; disable arbitrary function tools in workflow execution.
- Add immutable AI profile and knowledge publication/version semantics.
- Add redaction/context-manifest utilities.
- Add provider timeout, retry, budget, and circuit-breaker policies.
- Define structured error categories and retention settings.

**Exit condition:** a pinned AI profile can safely produce validated output for a permitted record in a background job with complete audit evidence.

### Phase 1 — Assistive AI actions

- Register `action.ai_generate` and friendly catalogue aliases.
- Implement summarize, classify/extract, and draft modes.
- Add typed output paths and earlier-output binding.
- Add simple inspector, test preview, low-confidence, and failure paths.
- Store AI attempt evidence and metrics.
- Do not allow automatic send.

**Exit condition:** a support user can build and test triage and reply-drafting workflows without any AI-initiated mutation.

### Phase 2 — Grounded support and approval

- Add approved Knowledge Base versions and normalized citations.
- Add grounded-answer mode and knowledge-gap detection.
- Add human approval token/task experience.
- Connect approval to existing Send Email and Issue update actions.
- Capture approve/edit/reject feedback.

**Exit condition:** a draft can be grounded, reviewed, sent, and fully audited end to end.

### Phase 3 — Stateful support agent and handoff

- Add Issue-scoped AI Support Session.
- Add `action.ai_support_agent` decision contract.
- Implement maximum-turn, operating-hours, channel, and handoff policies.
- Add workflow/user/team assignment and structured handoff reasons.
- Add selective auto-response policy behind a feature flag.

**Exit condition:** a narrow pilot can respond automatically and reliably hand off without losing context or duplicating messages.

### Phase 4 — Channels and controlled actions

- Add Customer Portal presentation and status continuity.
- Add Aircall transcript/summary adapter when real transcript data is available.
- Add other channel adapters through normalized Issue/Communication events.
- Add a small set of confirmed, policy-scoped business actions where needed.

**Exit condition:** channel behavior is consistent and no channel bypasses the workflow permission/audit boundary.

### Phase 5 — Optimization

- Canvas metrics and support-AI dashboard.
- Knowledge-gap review queue.
- Prompt/profile comparisons and controlled rollout percentages.
- Cost budgets and provider fallback.
- Quality evaluation sets and release gates.

## 16. Test strategy

### Backend

- Validate every AI mode's configuration and output schema.
- Reject unreadable fields, linked records, knowledge, agents, and tools.
- Verify redaction of secrets, private notes, and protected fields.
- Confirm Issue/thread-scoped memory isolation.
- Test prompt-injection content as untrusted input.
- Test malformed JSON, unsupported schema, missing citations, low confidence, timeout, rate limit, provider outage, and budget exhaustion.
- Prove effect-key idempotency and bounded retry behavior.
- Prove AI failure cannot repeat a sent email or ERP mutation.
- Confirm published profile/schema versions remain immutable.
- Confirm simulation and runtime select the same workflow paths for the same stored AI result.

### Frontend

- Configure each task through progressive fields.
- Select permitted record fields and linked Communications.
- Preview context disclosure and knowledge source.
- Display outputs, citations, confidence, cost, errors, and path prediction.
- Configure success, low-confidence, handoff, and failure paths.
- Save placeholders in draft and block publication when required policy is missing.
- Verify keyboard navigation, focus, resize/scroll behavior, dark mode, and narrow client screen widths.

### End to end

- New Issue → classify → assign/update.
- Customer reply → summarize → grounded draft → approve → send → linked Communication visible on Issue.
- Low confidence → human handoff with summary and ToDo.
- Explicit human request → immediate handoff.
- Provider timeout → no duplicate message and visible incident.
- Reopened Issue → stop automatic resolution and hand off.
- Customer Portal and email threads preserve one Issue-scoped session.
- Aircall flow behaves correctly with and without transcript data.
- Published workflow remains reproducible after Agent, prompt, model, or Knowledge Base edits.

### Quality and safety evaluation

Maintain a redacted support evaluation set containing:

- common intents and supported answers;
- ambiguous identity and relationship cases;
- missing/outdated/conflicting knowledge;
- multilingual requests;
- angry, abusive, and manipulative prompts;
- prompt injection in email, HTML, attachments, and knowledge;
- refund, cancellation, account-change, security, and legal escalation cases;
- false-positive and false-negative handoff examples.

Release gates should define minimum classification accuracy, grounded-answer support, citation validity, handoff recall for high-risk cases, maximum false-resolution rate, latency, and cost.

## 17. Production-readiness acceptance criteria

The first production release is ready only when:

- all AI execution is version-pinned and permission-scoped;
- no customer or retrieved content can select tools or authorize side effects;
- outputs are schema-validated and usable by branches/later actions;
- every published AI node has a failure policy;
- grounded answers preserve verified source references;
- support memory is isolated by Issue/session;
- human handoff stops automatic responses and carries full context;
- retries cannot duplicate emails, comments, assignments, or updates;
- test mode cannot execute downstream side effects;
- history exposes model/profile/knowledge identity, decision, output, latency, usage, and error category;
- retention and provider-data-sharing settings are documented and configurable;
- a narrow pilot passes agreed quality, safety, SLA, and cost thresholds.

## 18. Product decisions required before implementation

1. Which Issue types are safe for the first pilot?
2. Which providers/models are approved for customer data?
3. Must all first-release outputs receive human approval, or may one low-risk flow auto-send?
4. Which Issue and linked-record fields may leave the site?
5. Which internal comments and attachments are excluded from AI context?
6. Which Knowledge Bases are customer-facing and who approves them?
7. What confidence threshold and maximum automatic-turn count apply?
8. Which events immediately force human handoff?
9. How long are AI inputs, outputs, citations, and usage evidence retained?
10. What monthly token/cost budget and per-workflow limits apply?
11. Should the system support one provider initially or a tested fallback provider?
12. Which support teams/users receive handoffs outside operating hours?

## 19. Recommended first deliverable

Implement one complete, low-risk workflow before building a general autonomous agent:

> **When an Issue is created or a customer replies, summarize and classify it, route it by typed outputs, draft a knowledge-grounded reply, and require human approval before the existing Send Email action sends it.**

This deliverable proves the essential architecture—permissions, context selection, structured outputs, knowledge citations, durable execution, approval, linked communication, history, and metrics—while keeping customer-facing risk controlled. The stateful and selectively autonomous support agent can then reuse the same contracts instead of replacing them.

## 20. Source notes

External behavior references were reviewed from official HubSpot documentation on 2026-08-24:

- [Use AI to summarize data in workflows](https://knowledge.hubspot.com/workflows/use-hubspots-ai-to-summarize-data-in-workflows)
- [Use AI to manage data in workflows](https://knowledge.hubspot.com/workflows/use-ai-to-manage-data-in-workflows)
- [Choose workflow actions](https://knowledge.hubspot.com/workflows/choose-your-workflow-actions)
- [Set up the Customer Agent](https://knowledge.hubspot.com/customer-agent/set-up-the-customer-agent)
- [Set up and customize Customer Agent handoff](https://knowledge.hubspot.com/customer-agent/set-up-and-customize-the-customer-agents-handoff-process)
- [Deploy the Customer Agent to channels](https://knowledge.hubspot.com/customer-agent/deploy-the-customer-agent-to-channels)

The local working tree is the authority for current FinbyzAI and ERPNext behavior. Recommendations in this document are proposals until their API, schema, security, UX, and migration contracts are approved.
