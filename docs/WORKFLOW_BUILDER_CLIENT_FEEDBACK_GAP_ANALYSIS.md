# Workflow Builder Client Feedback and HubSpot Gap Analysis

| Metadata | Value |
|---|---|
| Document type | Living gap analysis |
| Review status | Implementation complete for the supplied requirements and client feedback batch 1; provider UAT remains an operational release gate |
| Project | `megasol` |
| Owning app | `finbyzai` |
| Assessment date | 2026-08-21 |
| Code baseline | Local `apps/finbyzai` worktree on branch `version-16`, including the complete workflow-builder implementation and test pass recorded below |
| Feedback batch | Initial requirements plus client feedback batch 1 |
| Audience | Client stakeholders and engineering |
| Knowledge-base indexing | Not performed because the user explicitly requested no MCP usage |

## Purpose and boundaries

This document compares the complete requirements visible in the supplied screenshots and the client's first written feedback with the Workflow Builder currently implemented in FinbyzAI. It is the finished evidence-based assessment for this feedback batch and the authoritative prioritized backlog. All implementation slices completed during the review are reflected in the matrices, totals, evidence register, direct client answers, and change log.

The assessment and implementation were performed against the local source tree. The linked Google document and Google Drive video could not be independently opened during this review, so requirements not visible in the supplied screenshots or pasted feedback are not inferred. The HubSpot comparison uses public HubSpot product documentation and the client-provided screenshots, not private account configuration. Future feedback should update this file instead of creating a parallel gap analysis.

The document itself does not define a technical contract. Statuses describe verified behavior in the accompanying local implementation; proposed capabilities remain recommendations until implemented and tested.

### Completion statement

All 100 visible requirements in this feedback batch now have an implemented FinbyzAI contract. “Implemented” means the UI, validation, runtime/integration boundary, and automated tests exist. It does not mean third-party accounts have been configured or live-delivery UAT has been performed; Aircall, email tracking, Meta/Instagram, SMS, webhook, and Asana still require valid site credentials and a controlled acceptance test. The 2026-08-21 fit audit additionally separates normal, advanced, destructive, and context-unavailable actions so capability breadth no longer makes the normal authoring path unnecessarily complex.

## HubSpot reference behavior

The client uses HubSpot Workflow Builder, so HubSpot behavior is the interaction and terminology reference unless it conflicts with an agreed FinbyzAI safety or platform constraint.

| Area | HubSpot behavior used as reference | FinbyzAI alignment decision |
|---|---|---|
| Workflow object | A workflow enrolls one selected object type, such as Contact, Company, Deal, Lead, or Order. The object type controls which triggers and actions make sense and cannot be changed after selection. [HubSpot workflow object types](https://knowledge.hubspot.com/workflows/understand-workflow-object-types) | Treat immutable `primary_doctype` as the workflow object. A Lead workflow enrolls Leads; an Opportunity workflow enrolls Opportunities; neither is silently treated as a Contact workflow. Event options are now filtered by primary DocType and by enrollment-versus-wait usage. |
| Enrollment | HubSpot separates event-based, filter-based, schedule, webhook, and manual enrollment. Event groups are OR alternatives; an event can have event-property filters and additional record filters. [HubSpot enrollment triggers](https://knowledge.hubspot.com/workflows/set-your-workflow-enrollment-triggers), [HubSpot event enrollment](https://knowledge.hubspot.com/workflows/set-event-enrollment-triggers) | Keep one start boundary in the graph, but expose distinct trigger types. Version-2 event enrollment supports up to 20 OR event groups, event-specific criteria, and optional record criteria. |
| If/then branches | Branches are named and evaluated in displayed order; the first matching branch wins. Conditions within a filter group use AND, separate filter groups use OR, and a permanent None met branch catches unmatched records. HubSpot supports up to 20 branches and allows branch reordering.      [HubSpot if/then branches](https://knowledge.hubspot.com/workflows/use-if-then-branches-in-workflows) | Version-2 If/else uses the same ordered first-match model, editable names, up to 20 criteria branches, simple AND groups separated by OR, reordering, and a derived None path. Older arbitrary nested expressions remain executable without being offered for new authoring. |
| Delays | HubSpot distinguishes set-duration, calendar date/time, date-property, day/time, and event-occurrence delays. Event occurrence can use the enrolled object or an earlier action output, supports event-property filters, and can wait for a set maximum or as long as possible. Only events occurring after the record enters the delay count. Outcome branching is optional; without it, event and timeout continue to the same action. [HubSpot delays](https://knowledge.hubspot.com/workflows/use-delays) | Version-2 event waits now use the same sequence and semantics: choose enrolled record or exact earlier-action output, event, event filters, finite/indefinite maximum, then optional timeout path. Exact source identities are indexed durably, overdue events cannot beat the timeout, and existing version-1 two-output waits remain compatible. |
| Insert, copy, and move | HubSpot uses visible insertion points. Actions can be cloned or moved to a selected “Place here” point; branch copying is restricted to valid branch endpoints. [HubSpot clone and move actions](https://knowledge.hubspot.com/workflows/clone-and-move-workflow-actions) | Every connection and unfinished output now has a visible `+` insertion point. Clicking it opens a placement-aware catalogue; choosing a step connects or rewires the path atomically. Manual edge drawing is off by default and remains an explicitly enabled advanced mode. Individual steps can also be structurally relocated, and an exclusive connected downstream section can be copied, pasted, or duplicated with fresh identities. |
| Automated email | HubSpot's Send email action selects a saved automated email or creates one, and the email editor supplies reusable templates, visual editing, personalization preview, desktop/mobile preview, test sending, subject, sender, and reply-to controls. Future workflow sends use the saved email's current content. [HubSpot automated emails](https://knowledge.hubspot.com/marketing-email/create-automated-emails-to-use-in-workflows), [HubSpot test email](https://knowledge.hubspot.com/marketing-email/send-a-test-marketing-email) | Send email now selects a compatible enabled Frappe Email Template, can create/open the installed visual builder, previews against a real workflow record on desktop/mobile, and sends a rate-limited explicit test. The saved Email Template remains the reusable source of truth for future sends; quick inline content remains available for one-off and legacy nodes. |
| Testing and lifecycle | HubSpot supports criteria testing and safe record-based workflow testing, then review/publish and history inspection. [HubSpot workflow testing](https://knowledge.hubspot.com/workflows/test-your-workflow), [HubSpot workflow settings](https://knowledge.hubspot.com/workflows/manage-your-workflow-settings), [HubSpot workflow history](https://knowledge.hubspot.com/workflows/understand-your-workflow-details-page) | Preserve FinbyzAI's non-mutating simulation, immutable publication versions, run path/error evidence, and explicit lifecycle controls while simplifying client-facing wording. |

### Workflow object and event-source model

The event dropdown must not imply that every Frappe record is a HubSpot Contact. The implemented model now has three explicit layers:

1. **Workflow object:** `primary_doctype` is the one record type enrolled in the workflow. It is the FinbyzAI equivalent of HubSpot's workflow object type.
2. **Occurrence source:** native Frappe record triggers observe the enrolled DocType directly; business events come from a normalized integration signal; a wait can target either that enrolled record or the exact message/record output from a guaranteed earlier action.
3. **Record resolution:** enrolled-record events carry the exact `record_doctype` and `record_name`; action-output waits store the exact source DocType and record/message name. An email address, phone number, customer, or contact link is not silently guessed by the generic engine.

This separation answers the client's question about “Contact created” for Lead, Opportunity, Customer, and other DocTypes. Use **Record created** on the chosen primary DocType: in a Lead workflow it means Lead created; in an Opportunity workflow it means Opportunity created; in a Customer workflow it means Customer created. Use **Record changed** or **When filter criteria is met** for property/lifecycle changes. For example, Lead qualification should normally be modeled as `Lead.qualification_status = Qualified`, not as a universal contact event.

HubSpot similarly separates event occurrence from record state: events enroll when something happens, while filter criteria enroll when the selected object's state is true. HubSpot also lets an event delay use either the enrolled object or output from an earlier workflow action. [HubSpot enrollment trigger comparison](https://knowledge.hubspot.com/workflows/set-your-workflow-enrollment-triggers), [HubSpot event delays](https://knowledge.hubspot.com/workflows/use-delays)

### ERPNext/Frappe object mapping

| FinbyzAI primary DocType | HubSpot-style role | Native lifecycle behavior | Event/association rule |
|---|---|---|---|
| `Contact` | Contact/person | Record created, changed, or criteria met operate on Contact fields such as `email_id`, `phone`, `mobile_no`, and `unsubscribed`. | Contact list, email, call, form, and commerce events still require a producer to resolve the event to this exact Contact. |
| `Lead` | Lead/person | Native triggers operate on Lead fields including `email_id`, `phone`, `mobile_no`, `status`, and `qualification_status`. | “Lead qualified” is offered only for Lead event flows; native filter criteria on `qualification_status` is the preferred enrollment mechanism. |
| `Opportunity` | Deal | Native triggers operate on Opportunity, including `opportunity_from`/`party_name`, `contact_person`, `contact_email`, `status`, and `sales_stage`. | It is not treated as a Contact. An email event must identify the Opportunity or correlate to an earlier Send email output in that Opportunity run. |
| `Customer` | Company/customer | Native triggers operate on Customer, including `lead_name`, `customer_primary_contact`, `email_id`, and `mobile_no`. | Customer/contact association is integration-owned; the engine does not guess which linked Contact should enroll. |
| `Sales Order` | Order | Use Record created for an order-based workflow. | “A contact/customer ordered” is a different event: a commerce adapter must map `Sales Order.customer` or another party identity to the enrolled Contact/Lead/Customer. |
| Any supported DocType | Custom object | Record created, changed, and filter criteria work generically through Frappe metadata and permissions. | Web Form can target a DocType and Communication can reference a DocType, but form/call producers must pass the actual target record to the normalized event API. |

The schemas behind this mapping are code-reviewed ERPNext/Frappe fields, not inferred HubSpot fields. E17

## Status and priority definitions

| Status | Meaning |
|---|---|
| **Implemented** | The requested outcome is available end to end in the current product, although its wording or visual design may differ. |
| **Partial** | Useful foundations exist, but an important behavior, coverage area, or usability expectation is still missing. |
| **Missing** | No complete user-facing capability matching the requirement was found. |
| **Needs product decision** | The requirement is ambiguous enough that product semantics must be agreed before implementation. |

| Priority | Meaning |
|---|---|
| **P0** | Direct acceptance blocker in the first client feedback or a prerequisite for normal workflow authoring. |
| **P1** | Required core capability with substantial business impact. |
| **P2** | Important breadth, administration, or integration work after the P0/P1 foundation. |
| **P3** | Future requirement or lower-priority extension. |

## Executive summary

The implementation now follows the HubSpot authoring semantics requested by the client while preserving Frappe's DocType and permission model. It includes mixed OR enrollment triggers, object-aware native event producers, ordered named multi-criteria branches with permanent None, readable/durable waits and drip batches, rich transforms, automatic completion, drag/drop insertion and rewiring, connected-section copy/paste, a resizable and independently scrollable inspector, lifecycle/testing/history controls, folders, six-month retention, communication policies, Contact/data actions, Instagram, and Asana actions.

The latest whole-feature fit review keeps everyday steps visible by default, places uncommon operational tools behind one **Show advanced actions** control, marks permanent deletion as destructive, and disables actions that cannot work for the selected workflow object or execution user. Trigger configuration remains exclusively on the enrollment boundary and all delay implementations remain behind one plain-language **Delay** entry. The canvas now opens full width: the action catalogue appears only after **Add action** or a canvas `+`, the step inspector appears only after selecting a step, and opening either closes the other. This removes the permanent two-sidebar layout while retaining the independently scrolling, resizable inspector for complex steps.

### Final fit-and-impact audit

The 2026-08-21 completion pass reconciled user-visible options with their persisted schema and runtime behavior, then exercised the result on the live site. It corrected these cross-layer defects:

- operation-dependent fields no longer invalidate valid Random number transforms or assigned/all-user notifications;
- Update record preserves explicit clear operations but blocks clearing a static mandatory Frappe field in both authoring validation and the UI;
- node summaries now describe actual delay, drip, deduplication, record, communication, subflow, Asana, and terminal behavior rather than generic or misleading fallbacks;
- Send email selects only enabled outgoing Email Accounts and the Connections view derives current template, sender, Reach topic, secret, SMS, Asana, subflow, DocType, and field dependencies from the draft;
- every workflow email first enforces native Frappe/CRM global suppression; an optional FinbyzReach Subscription Topic adds topic-wise Lead suppression without changing FinbyzReach or its `/manage_subscriptions` page; and
- stale external effects are never blindly resent. A scheduled recovery marks an ambiguous delivery `UNKNOWN_COMMIT`, pauses the run, and creates operator reconciliation evidence. Tokenless runs whose immutable workflow/version was removed are non-destructively quarantined as failed instead of remaining falsely active forever.

### Authoring fit strategy

| Tier | Purpose | Examples | Presentation rule |
|---|---|---|---|
| Core | Common journey-building work | If/else, deduplicate, update/create record, ToDo, comment, notifications, email/SMS, standard delays | Visible immediately and recommended for normal authoring. |
| Advanced | Valid but uncommon, technical, or operational work | Random split, drip batching, transforms, associations, round robin, copy/merge, subflow control, outgoing webhook, Instagram, Asana | Hidden behind one explicit advanced control, but included in search. |
| Destructive | Irreversible record mutation | Delete record | Shown only with advanced actions, labeled destructive, permission checked, terminal, and blocked from insertion between two steps. |
| Unavailable | Cannot work in the current workflow context | Merge Contact in a Lead workflow, Asana without its app, copy/delete without execution-user permission | Visible only when advanced/search reveals it, disabled with the exact reason; runtime compatibility for existing graphs is unchanged. |

### Coverage totals

The client-requirement matrices below contain 100 independently assessed requirements. The broader HubSpot feature-family comparison is intentionally reported separately and is not added to these totals, so future HubSpot product changes cannot silently change the original client acceptance baseline.

| Status | Count | Share |
|---|---:|---:|
| **Implemented** | 100 | 100.0% |
| **Partial** | 0 | 0.0% |
| **Missing** | 0 | 0.0% |
| **Needs product decision** | 0 | 0.0% |
| **Total** | 100 | 100% |

### Release gates, not implementation gaps

1. Configure and UAT the site's outgoing email/SMS providers and confirm which delivery statuses they write to Frappe `Communication`.
2. Configure and UAT the existing Aircall and Customer Portal apps against production-like records.
3. Configure an allowlisted Meta endpoint/credential and Instagram consent records before enabling Instagram actions.
4. Enable and authenticate `asana_integration`, then verify task, update, subtask, and project operations with the client's workspace fields.
5. Obtain client acceptance for the implemented HubSpot-style interaction details, especially list-membership occurrence semantics and syntax-only email verification.

### Foundations worth retaining

- Draft revisioning, explicit save, autosave, undo/redo, validation, safe simulation, versioned publish, pause/resume/disable, and workflow cloning.
- Durable run, token, timer, outbox, incident, dead-letter, and execution-trace models.
- Permission-aware DocType, field, and object-aware event catalogues rather than hard-coded Contact assumptions.
- Nested AND/OR/NOT condition expressions, re-enrollment policies, suppression, goal conditions, and lifecycle reevaluation.
- Working deduplication, business-hours waits, value transforms, generic record actions, email/SMS, and compatible subflows.

## Code evidence register

Matrix rows reference these evidence IDs. Line anchors identify the reviewed baseline and may move as implementation changes.

| Evidence | Source | Finding |
|---|---|---|
| **E01** | [Backend node registry](../finbyzai/workflow_builder/registry.py#L51) | Defines nodes, stable event topics, enrolled-object profiles, DocType/usage-aware event availability, producer/setup guidance, version-2 named criteria branches, deterministic percentage splits, and hidden legacy nodes. |
| **E02** | [Graph validation and condition evaluator](../finbyzai/workflow_builder/schema.py#L79) | Validates legacy and version-2 branch contracts, up to twenty named condition trees plus None, percentage totals/handles, typed/filterable events and waits, workflow-object event compatibility, acyclic graphs, and global limits. |
| **E03** | [Frappe document hooks](../finbyzai/hooks.py#L147), [event capture](../finbyzai/workflow_builder/events.py#L25), and [installed-app event adapters](../finbyzai/workflow_builder/integrations.py#L1) | Capture generic document events; release exact enrolled-record/earlier-action waits after commit for record updates and ToDo completion; normalize Email Group, Web Form, Lead qualification, Aircall, portal, email/reply, unsubscribe, Sales Order, and abandoned-cart events. |
| **E04** | [Runtime node execution](../finbyzai/workflow_builder/engine.py#L928) and [path completion](../finbyzai/workflow_builder/engine.py#L1139) | Executes criteria, delays, transforms, and actions; event waits persist indexed source identity, finite/indefinite timer state, and race-safe event/timeout outcomes; a path with no next node completes automatically. |
| **E05** | [Step inspector](../workflow/src/components/Inspector.tsx#L65) | Configures named criteria branches through plain AND groups separated by OR, duration/date waits, and a HubSpot-style event-wait sequence: data source, compatible event, exact earlier action when applicable, event filters, finite/indefinite maximum, and optional timeout path. |
| **E06** | [Condition expression editor](../workflow/src/components/InspectorHelpers.tsx#L179) | Supports nested AND/OR/NOT rules for one condition expression. |
| **E07** | [Workflow canvas](../workflow/src/components/WorkflowCanvas.tsx#L105) | Provides compact visible `+` insertion points on connections and exact branch-output endpoints, color-separated branch paths, automatic lane layout, explicit legacy-unconnected-node treatment, placement-aware catalogue selection, atomic rewiring, structural action relocation, opt-in advanced manual links, clipboard commands, and per-output derived END markers. |
| **E08** | [Step catalogue](../workflow/src/components/NodeCatalog.tsx#L43) | Exposes accessible click-to-add and native drag sources, hides enrollment/legacy nodes, provides one Delay entry, keeps advanced/destructive actions progressive, and explains context-unavailable actions before insertion. |
| **E09** | [Editor state and commands](../workflow/src/state/WorkflowContext.tsx#L421) | Implements save, autosave, simulation, atomic insert/relocate, node and connected-section copy/paste/duplicate, undo/redo, and conflict recovery. |
| **E10** | [Workflow pages](../workflow/src/pages/WorkflowPages.tsx#L203) | Implements folder-aware list/create/move, clone, lifecycle state, action timing, authorized sender/response settings, test dialog, publish flow, version compare/restore, run history, and detailed execution paths. |
| **E11** | [Enrollment operations](../workflow/src/pages/EnrollmentPage.tsx#L109), [schedule engine](../finbyzai/workflow_builder/bulk.py#L1), and [incoming webhooks](../finbyzai/workflow_builder/webhooks.py#L1) | Provides permission-aware backfill; once/hourly/daily/weekly/monthly/annual/date-field calendar schedules with timezone, DST, overlap, and execution limits; and managed authenticated, rate-limited, idempotent inbound-webhook enrollment. |
| **E12** | [Workflow API](../finbyzai/workflow_builder/api.py#L91) and [run retrieval](../finbyzai/workflow_builder/engine.py#L2130) | Exposes event catalogues filtered by primary DocType and trigger/wait usage, idempotent correlated event enrollment, durable wait release, and detailed run/enrollment evidence. |
| **E13** | [Automation Workflow DocType](../finbyzai/workflow_builder/doctype/automation_workflow/automation_workflow.json#L1), [Automation Settings](../finbyzai/workflow_builder/doctype/automation_settings/automation_settings.json#L1), and [history maintenance](../finbyzai/workflow_builder/maintenance.py#L1) | Stores folder metadata, enforces a minimum 180-day execution-history window, and purges eligible details daily without breaking enrollment ledgers or aggregates. |
| **E14** | [Asana integration guide](../../asana_integration/docs/ASANA_TECHNICAL_GUIDE.md#L1) and [external action execution](../finbyzai/workflow_builder/external.py#L1) | A first-class Workflow Builder action uses the installed Asana client for task create/update, subtask creation, and project creation with resolved payload fields and observable output. |
| **E15** | [External action execution](../finbyzai/workflow_builder/external.py#L1) | Implements Frappe email, SMS, signed webhook, consent-aware Instagram/Meta delivery, and Asana effects with bounded I/O and stable engine result contracts. |
| **E16** | [Workflow authoring service](../finbyzai/workflow_builder/authoring.py#L1) | Implements immutable publication, one subscription per mixed trigger, folders, sender validation, whole-workflow cloning, version comparison, and restore-to-draft. |
| **E17** | [ERPNext Lead schema](../../erpnext/erpnext/crm/doctype/lead/lead.json), [Opportunity schema](../../erpnext/erpnext/crm/doctype/opportunity/opportunity.json), [Customer schema](../../erpnext/erpnext/selling/doctype/customer/customer.json), [Sales Order schema](../../erpnext/erpnext/selling/doctype/sales_order/sales_order.json), and [Frappe Contact schema](../../frappe/frappe/contacts/doctype/contact/contact.json) | Confirms the real object-specific lifecycle, party, contact, email, phone, qualification, and order fields used for the mapping above. |
| **E18** | [Workflow email service](../finbyzai/workflow_builder/emailing.py), [external email execution](../finbyzai/workflow_builder/external.py), [Send email inspector](../workflow/src/components/Inspector.tsx#L134), [Reach suppression rules](../../finbyzreach/finbyzreach/email_marketing.py), and [visual Email Template Builder](../../finbyzreach/finbyzreach/email_template_builder/api.py#L299) | Selects primary-DocType-compatible templates and enabled outgoing senders, renders saved personalization, supports preview/test delivery and visual authoring, always checks native Frappe/CRM global suppression, optionally reads a Reach topic after resolving a Lead, keeps workflow unsubscribe links on Frappe's standard endpoint, and records suppression/content identifiers for audit and later event correlation. Reach's preference page and source remain topic-only and unchanged. |

## Detailed gap matrix

### A. Enrollment triggers

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| TR-01 | Workflow can have no automatic trigger and be started manually | **Implemented** | `trigger.manual` plus manual enrollment UI/API starts a published workflow for a selected record. E01, E10 | Keep the trigger internally for graph/runtime consistency; label it as manual start so it matches client language. | P1 |
| TR-02 | Multiple enrollment triggers on one workflow | **Implemented** | `trigger.any` keeps one enrollment boundary while allowing 2–20 document-created, document-changed, criteria, and business-event trigger groups joined by OR; publication creates one active subscription per group. E01, E02, E05, E12, E16 | Schedule remains a workflow-level durable enrollment mode rather than an event group. | P1 |
| TR-03 | Contact is in a certain list | **Implemented** | Contact and Lead workflows expose a typed Email Group membership occurrence. New members and re-subscriptions resolve matching `email_id` records and emit an idempotent event with a selectable Email Group. E01, E03, E05, E12 | “In list” is intentionally occurrence-based for automatic enrollment; operators can use the existing backfill tool when already-existing members must enter. | P1 |
| TR-04 | Contact fulfils specific criteria using contact fields | **Implemented** | “When filter criteria is met” evaluates permission-safe metadata fields on the selected primary DocType, with nested conditions and transition-aware insert/update enrollment; preview/backfill uses the same field access boundary. E01, E03, E05, E06, E11 | Protected/system fields remain intentionally unavailable. | P0 |
| TR-05 | Contact changed, with access to contact fields | **Implemented** | Record changed observes the primary DocType and supports up to fifty permitted watch fields plus optional current-state criteria and stored changed-field evidence. The safe catalogue includes normal scalar, rich-text, code/JSON, attachment, link, and temporal fields while excluding password/system-only data. E01, E03, E05 | Child-table mutation should use child/association actions rather than exposing unrestricted table internals as scalar predicates. | P0 |
| TR-06 | Contact created, with access to contact fields | **Implemented** | Record created runs after a new primary-DocType record commits and exposes that DocType's permitted fields; Lead, Opportunity, Customer, Contact, and eligible custom DocTypes retain their own identities. E01, E03, E05 | Permission and blocked-DocType controls remain mandatory. | P0 |
| TR-07 | Form submitted | **Implemented** | The generic post-insert hook detects authoritative Frappe Web Form saves and emits the exact form name, submission mode, target DocType, and target record for typed enrollment/waits. E01, E03, E12 | Non-Frappe forms should call the same normalized event service from their adapter. | P1 |
| TR-08 | Contact called us | **Implemented** | A terminal inbound Aircall `Call Log` emits one normalized event. Stored links target Lead, Opportunity, and Customer; Aircall's normalized phone matcher targets Contact. The picker exposes the event only for those four DocTypes. E01, E03, E05, E12 | Keep matching rules and terminal statuses aligned with the Aircall integration. | P1 |
| TR-09 | Email hard bounced | **Implemented** | A linked Communication transition to `Bounced` emits a hard-bounce event with record, Communication, Email Queue, message, and email-type context. E01, E03, E12 | The configured provider must write Frappe's delivery status. | P1 |
| TR-10 | Email soft bounced | **Implemented** | A linked Communication transition to `Soft-Bounced` emits an idempotent soft-bounce event with message correlation. E01, E03, E12 | Provider UAT is required. | P1 |
| TR-11 | Email clicked | **Implemented** | A linked Communication transition to `Clicked` emits a typed, message-correlated event usable for enrollment or a post-email wait. E01, E03, E12 | Link-level detail is available when the provider supplies it through the normalized event API. | P1 |
| TR-12 | Email opened | **Implemented** | A linked Communication transition to `Opened` emits a typed, message-correlated event. E01, E03, E12 | Open tracking remains subject to provider/privacy limitations. | P1 |
| TR-13 | Email complained | **Implemented** | `Marked As Spam` is normalized to the complaint event for the exact referenced record and message. E01, E03, E12 | Provider UAT is required. | P1 |
| TR-14 | Email unsubscribed | **Implemented** | Frappe Email Unsubscribe records and `Recipient Unsubscribed` Communication status emit typed unsubscribe events with email-type filters and exact record resolution. E01, E03, E12 | Consent-purpose mapping can be extended without changing the stable topic. | P1 |
| TR-15 | Contact created a store login | **Implemented** | Customer workflows receive a normalized event when a new Customer Portal website-user session resolves through Portal User or a linked Contact to that Customer. The event is intentionally not shown for Lead or Opportunity. E01, E03, E05, E12 | If login events are later required for a different identity object, add an explicit association contract instead of guessing. | P3 |
| TR-16 | Contact ordered something | **Implemented** | A new ERPNext Sales Order emits an order-created event for its Customer. Orders converted from a Shopping Cart Quotation are identified as Customer Portal orders; Customer workflows can filter by source, order type, or Sales Order. E01, E03, E05, E12 | Keep Sales Order as the authoritative transaction and add other commerce sources through adapters. | P3 |
| TR-17 | Contact started an order but did not finish it | **Implemented** | An hourly adapter treats an unchanged draft Shopping Cart Quotation older than 24 hours and not converted into a Sales Order as abandoned, then signals its Lead, Customer, and/or Contact idempotently. E01, E03, E05, E12 | The 24-hour product default can later become a site setting. | P3 |
| TR-18 | Trigger/event choices respect Lead, Opportunity, Customer, Contact, and other workflow objects | **Implemented** | `primary_doctype` is the immutable enrolled object. The API exposes separate trigger and wait event catalogues filtered by DocType traits/exact type; the inspector names native triggers for the current object and explains producer/resolution rules; graph validation rejects known core topics used on an incompatible object. E01, E02, E05, E12, E17 | Keep topic keys stable and add new object/event mappings only with an authoritative producer and association rule. | P0 |

### B. Branching, waits, transforms, and orchestration

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| LG-01 | One if/else action can create many business branches | **Implemented** | New `condition.if_else` version 2 supports ordered named criteria branches; binary version 1 remains executable. E01, E02, E04 | Keep the versioned compatibility contract. | P0 |
| LG-02 | Every branch can be named | **Implemented** | Each version-2 branch has an editable display name separate from its stable handle, shown in the inspector and canvas. E05, E07 | Keep names limited and unique for overview clarity. | P0 |
| LG-03 | Support up to ten branches | **Implemented** | UI and server validation allow one through twenty named branches plus None, matching HubSpot and exceeding the client's stated ten-branch example. E02, E05 | Keep the 20-branch limit aligned in both layers. | P0 |
| LG-04 | Multiple filter criteria for each branch | **Implemented** | Every named path has one or more filter groups. Conditions inside a group use AND; separate groups use OR. Paths are evaluated top-to-bottom and the first match wins. Older nested/NOT expressions remain executable and can be deliberately replaced, but new If/else authoring stays simple. E02, E04, E05, E06 | Keep the AND/OR labels and first-match semantics explicit. | P0 |
| LG-05 | Always include a none branch | **Implemented** | Version-2 validation/runtime require a non-removable `none` output for unmatched records. E02, E04, E05, E07 | Keep None derived from the branch contract. | P0 |
| LG-06 | Remove the separate Value branch from normal authoring | **Implemented** | Value branch and manual Complete are hidden for new authoring while legacy nodes remain viewable and executable. E01, E08 | Retain legacy compatibility until old versions age out. | P0 |
| LG-07 | Deduplicate | **Implemented** | Version 2 checks one or more selected fields with explicit all/any matching and exposes duplicate/unique paths plus matched-field evidence; version 1 remains compatible. E01, E02, E04 | Keep destructive merge as a separate action. | P1 |
| LG-08 | Duration waits accept minutes, hours, days, and weeks | **Implemented** | Duration and event-timeout editors accept seconds, minutes, hours, days, or weeks and normalize them to durable runtime seconds. E02, E05 | Keep the one-year validation ceiling visible. | P0 |
| LG-09 | Wait until a specific date and time entered by the user | **Implemented** | Wait-until supports a literal date/time mode alongside record-field mode; past values continue immediately. E02, E04, E05 | Add workflow-wide timezone display later if product requires it. | P0 |
| LG-10 | Wait until a date/time stored on the contact | **Implemented** | Record-field mode offers permitted Date/Datetime fields and gives an explicit runtime error when the selected record has no date value. E04, E05 | Keep literal and field modes visually distinct. | P1 |
| LG-11 | Wait-for-event uses a discoverable dropdown | **Implemented** | Authors first choose this workflow record or output from an earlier action, then see only compatible grouped events. Action-output events require the exact guaranteed earlier Send email, Create/Copy record, or Create ToDo step; every known event explains its producer and matching rule. E01, E02, E05, E12 | Saved custom/legacy topics remain visible without weakening the typed contract for new authoring. | P0 |
| LG-12 | Useful wait events: email click, qualified lead, mail-type unsubscribe, etc. | **Implemented** | The object-aware catalogue includes requested CRM, form, Aircall, reply, email, portal, order, and abandonment events plus generic Record updated and earlier Create ToDo completion. Event-property filters, set maximum/as-long-as-possible waits, and optional timeout paths match HubSpot delay semantics. E01, E02, E03, E04, E05, E12 | Provider-backed statuses still require configured integrations. | P1 |
| LG-13 | Business-hours wait | **Implemented** | Durable business-hours logic supports timezone, weekdays, hours, and optional holiday calendar. E04, E05 | Keep the behavior; replace raw calendar/timezone text with metadata pickers when polishing. | P1 |
| LG-14 | Transform a value for reuse without modifying the contact | **Implemented** | Transform supports coalesce, concatenate, case conversion, parse/format number, phone, currency, deterministic random number, and reusable arithmetic using literal, record, or prior-node inputs. E01, E04, E05 | It never mutates the enrolled record unless a later Update action consumes its output. | P2 |
| LG-15 | Explain Transform value clearly in the UI | **Implemented** | The catalogue and inspector explain that the step prepares reusable text/number/phone/currency/calculated output without changing the record and expose operation-specific controls. E01, E05 | Keep the distinction from Update record visible. | P0 |
| LG-16 | Call another workflow/subflow | **Implemented** | A compatible active workflow can be selected; execution may wait for completion, and cycles/mismatches are rejected. E01, E05, E16 | Keep the capability and rename/help-text it as “Run another workflow” for client clarity. | P1 |
| LG-17 | Completion is automatic and visually obvious | **Implemented** | Runtime completes paths with no matching outgoing connection, Complete is hidden from new authoring, and the canvas derives a non-editable END marker for every unconnected output, including each branch. Legacy Complete nodes remain compatible. E01, E04, E07, E08 | Keep END presentation derived rather than persisted. | P0 |
| LG-18 | Wait for an email event from a specific preceding Send email step | **Implemented** | With Earlier action selected as the data source, a version-2 wait offers only guaranteed compatible steps, stores the chosen Send email's exact Email Queue identity, and ignores provider events for every other message. The same source contract supports Record updated after Create/Copy record and Task completed after Create ToDo. E01, E02, E03, E04, E05 | Provider UAT must confirm its tracking events update the linked Communication. | P0 |

### C. Canvas authoring and workflow lifecycle

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| UX-01 | Drag a block from “Add a step” onto the canvas | **Implemented** | Catalogue entries remain native drag sources, while the primary accessible flow is now: select a visible `+`, choose a step, and let the editor place it. E07, E08 | Guided click insertion is the default; dragging remains a power-user shortcut. | P0 |
| UX-02 | Drop a new block underneath/between existing steps | **Implemented** | Every real connection has a centered compact `+` and every unfinished output has a branch-labelled `+` above its derived END. New structural edits auto-arrange into separate lanes; `Tidy layout` cleans existing graphs and moves legacy orphan nodes into a clearly marked separate column. The catalogue shows the selected placement and a cancel action. E07, E08, E09 | No manual connection is required in the normal authoring flow. | P0 |
| UX-03 | Automatically connect and rewire inserted steps | **Implemented** | Atomic graph commands replace `A → B` with `A → new → B` or connect the exact unfinished branch output. Catalogue clicks resolve a deterministic valid placement and the command layer rejects orphan nodes and terminal steps placed mid-path; undo/redo and autosave treat insertion as one change. E07, E09 | Preserve this connected-graph invariant for future authoring commands. | P0 |
| UX-04 | Move nodes easily on the canvas | **Implemented** | React Flow dragging updates and persists node positions. E07, E09 | Retain free movement while adding structural insertion/reorder behavior. | P1 |
| UX-05 | Reorder actions/branches structurally, not only visually | **Implemented** | Dropping an eligible action near an edge atomically heals its previous path and inserts it at the new location; branch paths have explicit up/down reordering while ordinary dragging still controls layout. E05, E07, E09 | Cyclic or ambiguous multi-incoming structures are deliberately not auto-reparented. | P0 |
| UX-06 | Copy and paste actions or branch sections | **Implemented** | Nodes and exclusive connected downstream sections support copy, paste, and duplicate controls/shortcuts. IDs and internal edges are remapped, and copying stops safely at shared convergence points. E07, E09 | Keep section boundaries deterministic. | P0 |
| UX-07 | Show END automatically on every path | **Implemented** | The canvas renders non-editable derived END pills and dashed connectors for every unconnected output, including empty branch paths; they are not stored as runtime nodes. E04, E07 | Keep virtual markers synchronized with output connections. | P0 |
| UX-08 | Undo and redo authoring changes | **Implemented** | The editor records graph history and exposes Undo/Redo controls. E09, E10 | Extend the same command history to insertion, structural moves, and clipboard operations. | P1 |
| UX-09 | Manual Save button | **Implemented** | The editor exposes Save and revision-aware persistence. E09, E10 | Keep it as an explicit confidence control. | P1 |
| UX-10 | Autosave | **Implemented** | Dirty drafts are locally recovered and automatically persisted with revision conflict protection. E09 | Keep visible save state and conflict recovery. | P1 |
| UX-11 | Test a workflow using a selected contact/record | **Implemented** | Safe simulation selects a record and predicts the path without live mutation; a selected node can also be tested. E09, E10 | Confirm with the client that non-mutating simulation is the desired meaning of “Run test.” | P1 |
| UX-12 | Simple Draft/Publish control | **Implemented** | The header shows the draft/publication state and a direct Publish/Activate action while preserving mandatory validation, immutable versions, and pause/resume/disable controls. E10, E16 | A destructive toggle is intentionally avoided because published history is immutable. | P1 |
| UX-13 | Clone a complete workflow | **Implemented** | The workflow list can clone a workflow with fresh node/edge IDs. E10, E16 | Keep separate from step-level copy/paste. | P2 |
| UX-14 | Manually connect graph edges | **Implemented** | Connection handles, edge deletion, and edge reconnection are disabled in the normal editor. An explicit `Manual links` advanced toggle exposes them for exceptional graph repair. E07, E09 | Keep the guided `+` flow as the default so authors never need to wire ordinary steps. | P2 |

### D. Workflow settings, history, and organization

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| ST-01 | Allow or prevent contact re-entry | **Implemented** | Publish settings support Never, After completion, and Always re-enrollment policies, enforced by the ledger/runtime. E04, E10 | Move or mirror this control into a clearer Workflow settings area. | P1 |
| ST-02 | Allow multiple opportunities/executions for one contact | **Implemented** | Opportunity workflows naturally use each Opportunity as a distinct enrolled record; `ALWAYS` re-enrollment additionally permits separate event occurrences for the same record while durable occurrence keys prevent duplicate delivery. E04 | The primary DocType remains the identity boundary. | P2 |
| ST-03 | Stop the workflow when a contact responds | **Implemented** | A version-pinned communication policy listens for received Communications on the exact enrolled record, cancels active tokens/timers/runs, writes response evidence, and resolves waiting parents safely. E03, E04, E10 | The source Communication must have the correct reference DocType/name. | P1 |
| ST-04 | Communication timezone setting | **Implemented** | Workflow policies now expose a version-pinned IANA timezone for the action execution window. E04, E10, E13 | Replace free text with a searchable timezone catalogue during UI polish. | P1 |
| ST-05 | Restrict communication to a time window | **Implemented** | A version-pinned action execution window applies allowed weekdays, local start/end time, timezone, and optional ERPNext Holiday List to every action node. Outside the window, the token waits durably until the next opening; branches and delay calculations are not postponed. E04, E10 | If the client wants only outbound communications restricted, add a scope selector; current HubSpot-aligned behavior covers workflow actions generally. | P1 |
| ST-06 | Default sender name | **Implemented** | Workflow Communication settings store a version-pinned default sender name consumed by Send email unless the action overrides it. E10, E15, E16 | Keep sender identity visible during review. | P2 |
| ST-07 | Default sender email | **Implemented** | Workflow Communication settings accept only a syntactically valid address belonging to an enabled outgoing Email Account, then pass it to Frappe Email Queue. E10, E15, E16 | This prevents arbitrary From spoofing. | P2 |
| ST-08 | Default SMS from number | **Implemented** | A version-pinned default SMS sender name/number is passed through Frappe's configured provider hook; the UI warns that the provider must authorize it. E10, E15 | Providers that ignore sender identities remain provider-limited. | P2 |
| ST-09 | Mark workflow conversations as read | **Implemented** | The response policy can mark the exact triggering Communication read, and a separate action marks received Communications linked to the enrolled record read. E01, E04, E10 | Scope is deliberately Frappe Communication, not an undefined cross-provider inbox. | P2 |
| ST-10 | Enrollment history showing who entered | **Implemented** | Workflow run/enrollment views show record, source, status, timestamps, decisions, selected path, attempts, and errors with filtering and pagination. E10, E12 | Technical identifiers remain available for support. | P1 |
| ST-11 | Retain enrollment data for six months | **Implemented** | Automation Settings enforces at least 180 days. A daily purge removes expired terminal run detail in dependency order while retaining enrollment ledgers and aggregate metrics. E13 | Administrators may choose a longer period. | P1 |
| ST-12 | Execution logs show path and errors | **Implemented** | Run detail includes executed path, timeline, attempts, errors, enrollment evidence, and lifecycle reevaluations. E10, E12 | Keep; add client-friendly filtering/export only if requested. | P1 |
| ST-13 | Organize workflows into folders | **Implemented** | Workflows store slash-separated folder paths; create/list/search/filter and audited move-to-folder operations are exposed in the UI/API. E10, E13, E16 | Folder access inherits workflow permissions. | P1 |

### E. Contact and record actions

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| CA-01 | Create Contact | **Implemented** | Generic Create record can select Contact and map permission-safe required/writable fields. E01, E05 | Provide a Contact-labelled shortcut/preset if discoverability remains poor. | P1 |
| CA-02 | Edit Contact | **Implemented** | Update record changes writable fields on the enrolled Contact. E01, E05 | Add Contact wording/presets without duplicating runtime logic. | P1 |
| CA-03 | Merge Contact by email | **Implemented** | Merge Contact can use `email_id` to find exactly one earlier canonical Contact and calls Frappe's permission-checked merge/rename path; zero or ambiguous matches fail safely. E01, E04 | Publish review should treat merge as destructive. | P1 |
| CA-04 | Merge Contact by phone number | **Implemented** | Merge Contact can match permitted Contact phone/mobile fields and rejects incomplete or ambiguous identity. E01, E04 | Store normalized phone data when the business requires normalized matching. | P1 |
| CA-05 | Merge Contact by email and phone number | **Implemented** | Merge Contact accepts multiple fields with all/any semantics and records the matched fields in its effect output. E01, E02, E04 | Exact stored values are authoritative; fuzzy matching is deliberately excluded. | P1 |
| CA-06 | Copy Contact | **Implemented** | Copy record uses Frappe's document-copy mechanism under read/create permissions so metadata-managed exclusions and child handling remain authoritative. E01, E04 | The new document receives a fresh identity. | P2 |
| CA-07 | Delete Contact | **Implemented** | Generic Delete record permanently deletes the enrolled record and terminates that path. E01, E04 | Keep behind strong publish warnings and permissions. | P1 |
| CA-08 | Assign User | **Implemented** | Owner can be updated through writable fields, and round-robin assignment supports users/groups. E01, E05 | Add a simple dedicated assignment preset if needed. | P1 |
| CA-09 | Remove assigned User | **Implemented** | Unassign record closes all open Frappe assignments/ToDos for the enrolled record and returns the affected count. E01, E04 | Ownership is not silently changed. | P2 |
| CA-10 | Add Notes | **Implemented** | Add comment remains available for the timeline, and Create note creates a Note document with a link back to the enrolled record. E01, E04, E05 | Authors choose the desired persistence model explicitly. | P1 |
| CA-11 | Email verification | **Implemented** | Verify email resolves a literal/record/prior-step value and returns a deterministic syntax-valid result and reason without making an untrusted external network call. E01, E04, E05 | Mailbox existence/deliverability is explicitly outside this scoped action and may be added as a provider integration later. | P2 |
| CA-12 | Match/deduplicate by one selected field | **Implemented** | Deduplicate supports one scalar match field and unique/duplicate paths. E01, E04 | Retain as the lightweight check distinct from destructive merge. | P1 |
| CA-13 | Match/deduplicate using multiple fields | **Implemented** | Version-2 Deduplicate accepts multiple permitted fields and explicit all/any exact matching; its output lists which fields participated. E01, E02, E04, E05 | Legacy single-field nodes remain executable. | P2 |

### F. Communication actions

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| CM-01 | Send email | **Implemented** | Authors can select a compatible standard or visual Email Template, create/open the installed visual builder, personalize against a real enrolled record, preview desktop/mobile output, send a rate-limited test, select an enabled outgoing Email Account, override subject/reply-to, optionally select a Reach Subscription Topic, or retain quick inline content. The requested **Require current email consent** option and runtime gate remain removed. Live execution always enforces native Frappe global and applicable Lead/Contact opt-outs; when the recipient resolves to a Lead, the optional Reach topic adds topic-wise suppression. A suppressed action completes with an auditable reason, keeps unsubscribe links on Frappe's standard endpoint, and never mutates Reach's topic preference page. E01, E04, E05, E10, E15, E18 | The saved template is the reusable source of truth. Provider engagement status is normalized through Communication and still requires provider UAT. | P1 |
| CM-02 | Send SMS | **Implemented** | Consent-aware SMS uses configured Frappe SMS Settings and respects the version-pinned workflow action window. E01, E04, E10, E15 | Add provider-approved sender selection and delivery-event producers. | P1 |
| CM-03 | Send Instagram direct message | **Implemented** | A consent-aware Instagram action resolves recipient/message values and sends the Meta messaging payload through the existing allowlisted HTTPS, rate-limit, idempotency, and secret boundary. E01, E02, E05, E15 | Meta account credentials and allowed host must be configured and UAT-approved. | P2 |
| CM-04 | Internal notification to assigned users | **Implemented** | Notify user offers an Assigned users audience, resolves open ToDo assignees, removes disabled/duplicate users, and reports recipients. E01, E04, E05 | An empty audience fails visibly. | P1 |
| CM-05 | Internal notification to all users | **Implemented** | Notify user offers All system users with a bounded enabled-System-User query and one notification per recipient. E01, E04, E05 | This broad audience remains explicit at authoring time. | P2 |
| CM-06 | Internal notification to a specific user | **Implemented** | Notify user accepts a selected user, subject, and message. E01, E05 | Keep and share the future recipient-source model. | P1 |

### G. Internal actions and data utilities

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| IN-01 | Wait action | **Implemented** | Fixed, until-date, until-event, and business-hours waits are durable. E01, E04 | Resolve the usability gaps recorded in LG-08 through LG-12. | P1 |
| IN-02 | Goal Event | **Implemented** | Versioned goal conditions can prevent or complete runs automatically, and Complete goal is a visible terminal action with named output evidence for explicit milestones. E01, E04, E05, E10 | Use the policy for global goals and the action for path-specific goals. | P1 |
| IN-03 | Split | **Implemented** | Random percentage split supports two to twenty named paths whose percentages total 100. A deterministic run/step hash makes selection stable across retries and simulation exposes the selected path and bucket. E01, E02, E04, E05 | Add aggregate experiment reporting later; runtime routing and authoring are complete. | P2 |
| IN-04 | Go To | **Implemented** | Go To selects an existing destination step, adds validated hidden adjacency, and executes that convergence without requiring a manually drawn long edge; cycles remain rejected. E01, E02, E04, E05 | Loops remain intentionally unsupported. | P2 |
| IN-05 | Convert text to number | **Implemented** | Transform parses common decimal/grouping forms into a numeric output with deterministic invalid-input behavior. E01, E02, E04, E05 | Input remains explicit and bounded. | P2 |
| IN-06 | Format number | **Implemented** | Transform formats numeric input with configured precision/grouping into reusable text. E01, E02, E04, E05 | Formatting does not mutate the source field. | P2 |
| IN-07 | Format phone number | **Implemented** | Transform normalizes phone input to a validated international-style representation. E01, E02, E04, E05 | Country-specific display formatting can be added later if requested. | P2 |
| IN-08 | Format currency | **Implemented** | Transform produces currency-labelled text with configured precision. E01, E02, E04, E05 | Currency remains an explicit author input. | P2 |
| IN-09 | Generate random number | **Implemented** | Transform generates a bounded integer/decimal using a stable workflow execution seed, so retries reproduce the same value. E01, E02, E04, E05 | Determinism is required for retry safety. | P3 |
| IN-10 | Math operation | **Implemented** | Transform performs reusable add/subtract/multiply/divide output, while Numeric adjust remains the explicit record-mutating action. E01, E02, E04, E05 | Divide-by-zero fails clearly. | P2 |
| IN-11 | Add/run another workflow | **Implemented** | Call subflow runs a compatible active workflow, optionally waiting for completion. E01, E05 | Rename in client language and preserve cycle protection. | P1 |
| IN-12 | Remove from workflow | **Implemented** | The action cancels other active runs and their durable timers/tokens for the selected compatible workflow; selecting current workflow also terminates the current path. E01, E02, E04, E05, E12 | Cancellation is audited in normal run evidence. | P2 |
| IN-13 | Drip by batch size and interval | **Implemented** | Drip in batches uses a locked persistent cursor per published version/node to release a configured batch immediately and hold overflow until the next readable interval. E01, E02, E04, E05 | Cursor state is version-pinned and retry-safe. | P2 |

### H. Asana actions

| ID | Requirement | Status | Current implementation | Gap and recommendation | Priority |
|---|---|---|---|---|---|
| AS-01 | Create Asana Task | **Implemented** | A first-class Asana action resolves a non-empty field payload, injects the configured workspace when needed, calls the installed integration client, and returns GID/name/link. E01, E02, E05, E14, E15 | Requires enabled Asana Settings. | P2 |
| AS-02 | Update Asana Task | **Implemented** | The same action supports update with a required literal/record/prior-output target GID and observable result. E01, E02, E05, E14, E15 | Provider errors retain retry-safe classification. | P2 |
| AS-03 | Create Asana Subtask | **Implemented** | Create subtask requires a parent task GID, injects it into the resolved payload, and calls the installed Asana task client. E01, E02, E05, E14, E15 | Parent existence is provider-validated. | P2 |
| AS-04 | Create Asana Project | **Implemented** | Create project calls the Asana Projects API using the installed integration's authenticated client and returns project identifiers. E01, E02, E05, E14, E15 | Requires the authenticated account to have project permission. | P2 |
| AS-05 | Access all relevant Asana fields | **Implemented** | Payload values can be literals, enrolled-record fields, or guaranteed prior-step outputs, allowing the installed Asana API to receive relevant writable task/subtask/project fields without pretending read-only fields are writable. E01, E02, E05, E14, E15 | The 128 KiB payload and integration permissions are enforced boundaries. | P2 |

## Broader HubSpot workflow feature-family comparison

This matrix covers every functional family presented by the current public HubSpot workflow enrollment, action, settings, movement, testing, and history documentation reviewed through 2026-08-21. HubSpot editions and connected apps expose many subscription-specific individual actions; those are grouped by behavior instead of pretending each vendor-branded action belongs in FinbyzAI core. Sources: [enrollment triggers](https://knowledge.hubspot.com/workflows/set-your-workflow-enrollment-triggers), [workflow actions](https://knowledge.hubspot.com/workflows/choose-your-workflow-actions), [workflow settings](https://knowledge.hubspot.com/workflows/manage-your-workflow-settings), [clone and move](https://knowledge.hubspot.com/workflows/clone-and-move-workflow-actions), [testing](https://knowledge.hubspot.com/workflows/test-your-workflow), and [details/history](https://knowledge.hubspot.com/workflows/understand-your-workflow-details-page).

| ID | HubSpot feature family | FinbyzAI status | Fit and implementation decision |
|---|---|---|---|
| HP-01 | Manual enrollment | **Implemented** | Manual trigger plus permission-aware record enrollment. |
| HP-02 | Filter-criteria enrollment | **Partial** | Nested field criteria work on the primary DocType; HubSpot's broader activity, association-label, marketing, consent, and commerce filter universe depends on local DocTypes/adapters. |
| HP-03 | Event-occurrence enrollment | **Implemented** | Typed OR/mixed trigger groups, event filters, record filters, correlation, idempotency, and native producers cover the requested Frappe/Aircall/email/commerce events. |
| HP-04 | Incoming-webhook enrollment | **Implemented** | Managed endpoints support generated keys, bearer or HMAC-SHA256 authentication, exact record-name or permitted unique-field mapping, payload filters, request/rate limits, idempotency, durable outbox receipt, secret rotation, and non-sensitive receipt history. |
| HP-05 | Scheduled enrollment | **Implemented** | Durable once, hourly ERP Advanced, daily, weekly, monthly day/weekday, annual fixed-date, and annual Date-field schedules use timezone-aware calendar arithmetic, DST checks, audience filters, overlap/catch-up policies, limits, and automatic one-time disablement. |
| HP-06 | Re-enrollment controls | **Implemented** | Never, after completion, and every distinct occurrence are versioned and idempotently enforced. |
| HP-07 | Unenroll when no longer eligible | **Implemented** | Optional policy reevaluates eligibility before each node and cancels the active path with evidence. |
| HP-08 | Suppression/exclusion lists | **Implemented** | Central version-aware suppression rules reject enrollment before effects are created. |
| HP-09 | Workflow goal | **Implemented** | Goal criteria prevent unnecessary enrollment and complete active runs before the next node. |
| HP-10 | Workflow-wide action execution window | **Implemented** | Weekdays, local hours, IANA timezone, and optional ERPNext Holiday List durably postpone action nodes only. |
| HP-11 | Fixed-duration delay | **Implemented** | Readable seconds/minutes/hours/days/weeks authoring uses durable timers. |
| HP-12 | Delay until calendar date/time | **Implemented** | Literal local date/time is supported. |
| HP-13 | Delay until date-property value | **Implemented** | A permitted Date/Datetime field can drive the durable due time. |
| HP-14 | Delay until event occurrence with maximum wait | **Implemented** | Enrolled-object and earlier-action-output sources, compatible typed events, event filters, exact message/record correlation, set or indefinite maximum wait, and optional timeout paths are wired. Only events occurring while the timer is active count, and a matching event received after its due time follows timeout. |
| HP-15 | Delay until day/time or working window | **Implemented** | Business-hours delay supports weekdays, times, timezone, and Holiday List exclusions. |
| HP-16 | Named AND/OR if/then branches with None | **Implemented** | Up to twenty ordered first-match criteria paths, independent nested filters, reordering, and permanent None. |
| HP-17 | Branch on one property or prior action output | **Implemented** | The normal named If/else predicate can choose either a permitted enrolled-record field or a compatible guaranteed earlier-step output, without restoring the confusing separate Value Branch. |
| HP-18 | Random percentage branch | **Implemented** | Two to twenty named paths total 100; deterministic allocation prevents retry drift. |
| HP-19 | Go to another workflow | **Implemented** | Compatible active subflows can run asynchronously or block until completion, with cycle protection. |
| HP-20 | Go to another action | **Implemented** | A plain-language Go To action selects an existing destination, participates in reachability validation, and remains acyclic. |
| HP-21 | Edit/copy/clear CRM properties and numeric adjustment | **Implemented** | Update record supports set, clear, record-field copy, compatible earlier-output copy, and collection append/remove where the Frappe field permits it; numeric adjustment is atomic. Explicit linked-record reads supply controlled cross-object values without guessing relationships. |
| HP-22 | Create CRM records, tasks, notes, line items, quotes, invoices | **Implemented** | Generic permission-aware DocType creation covers supported ERPNext records and child mappings, with dedicated ToDo, comment, Note, copy, and Asana actions where their semantics differ. |
| HP-23 | Delete records | **Implemented** | Permission-checked delete is terminal and audited, with explicit destructive semantics. |
| HP-24 | Associations, labels, ownership rotation, skills | **Partial** | Link/unlink association and atomic user/group round robin exist; association labels and skills-based routing are not core capabilities. |
| HP-25 | Email, SMS, internal notifications | **Implemented** | Email matches the requested HubSpot-style flow with reusable standard/visual templates, in-place visual-template creation, saved-template reuse, record personalization, desktop/mobile preview, explicit test send, sender/reply-to controls, unsubscribe handling, and traceable queue output. Per the latest client instruction, email has no workflow consent toggle/gate. SMS and Instagram retain their explicit consent controls. |
| HP-26 | Conversation assignment/read-state | **Implemented** | Response policy and an explicit action mark linked received Frappe Communications read; assignment remains represented by normal Frappe ToDos. |
| HP-27 | Format and transform data | **Implemented** | Text, numeric parsing/formatting, phone, currency, deterministic random values, reusable math, coalesce, and concatenation are available. |
| HP-28 | Outgoing webhook | **Implemented** | Signed, allowlisted, idempotent HTTPS delivery is durable and observable. |
| HP-29 | Custom code | **Missing** | Deliberately excluded from the generic builder security boundary; approved logic should use reviewed app nodes or signed webhooks. |
| HP-30 | AI/agent actions | **Missing** | HubSpot-specific AI agents, prompts, research, summarization, ICP, and prospecting actions are not reproduced; add only explicit local AI use cases with data-governance approval. |
| HP-31 | Marketing audiences, campaigns, static lists, events | **Missing** | FinbyzAI has event/list signals but no full marketing-audience and campaign action family. Keep these in integration modules rather than generic runtime core. |
| HP-32 | Connected-app actions | **Partial** | Stable node-extension and webhook boundaries plus ERPNext/Asana adapters exist; dedicated Slack, Zoom, Google Chat, Salesforce, NetSuite, DocuSign, and other HubSpot marketplace actions are not bundled. |
| HP-33 | Clone/move one action or all following actions | **Implemented** | Individual steps relocate structurally; one step or its exclusive connected downstream section can be copied/pasted/duplicated safely. |
| HP-34 | Clone complete workflow | **Implemented** | Whole-workflow clone remaps node/edge identities safely. |
| HP-35 | Revision history, comparison, restore | **Implemented** | Immutable published versions can be compared and any selected version restored into the editable draft without mutating history. |
| HP-36 | Workflow folders and saved organizational views | **Implemented** | Slash-separated folder metadata supports create, list filtering/search, and audited move-to-folder behavior alongside status views. |
| HP-37 | Record-based criteria/path testing | **Implemented** | Non-mutating simulation selects a real record, evaluates the graph, reports observed/predicted/skipped confidence and timestamps, exposes trigger/path evidence, and now returns the same public output keys used by runtime so output-based branches can be tested accurately. |
| HP-38 | Review, publish, pause, resume, disable | **Implemented** | Draft validation, immutable publication, health preflight, activation, pause/resume, and disable are explicit. |
| HP-39 | Enrollment history, action logs, path, errors, export | **Implemented** | Run detail includes decisions, path, attempts, errors, event timeline, pagination/export support, and a configurable minimum 180-day retention window. |
| HP-40 | Workflow health/performance notifications | **Partial** | Canvas node/branch counts, aggregate performance, incidents, dead letters, preflight, and operator recovery are implemented. Configurable proactive performance-summary recipients remain a separate administration enhancement. |

### Deliberate platform adaptations

- HubSpot “Contact” is not a universal record in Frappe. The workflow's immutable primary DocType is authoritative, and event adapters must resolve that exact record.
- Provider- or marketplace-specific actions remain integrations. The core builder supplies permission, idempotency, durable execution, audit, and extension boundaries.
- Custom code is not exposed inside the generic builder because it would bypass the permission-safe authoring model. Reviewed app code or signed webhooks are the supported escape hatches.
- HubSpot subscription packaging is not copied. A row is implemented only when the business behavior works in FinbyzAI, not merely because a similarly named UI item exists.

## Answers to the client's direct questions

### What does Transform value do?

Transform value prepares a reusable value for later steps without changing the Contact or other enrolled record. Current examples are:

- choose the first non-empty value from several inputs;
- join values using a separator;
- convert text to uppercase or lowercase;
- parse or format numbers;
- normalize a phone number;
- format currency;
- perform reusable arithmetic; or
- generate a deterministic random value that stays stable on retry.

Each input can be a fixed value, a field from the enrolled record, or output from a previous guaranteed step. The result becomes `value` and can be used by later actions such as Update record, Create record, Send email, SMS, webhook, Instagram, or Asana. The catalogue and inspector now state plainly that this step does not change the record.

### Does Call subflow trigger another workflow?

Yes. The UI now calls this “Run another workflow.” It runs another published, active workflow with the same primary DocType as a nested subflow. The author can choose whether the parent waits for the child to finish. The server prevents missing, inactive, incompatible, self-referencing, and cyclic subflows.

### Should Complete be placed manually?

No. The runtime marks a run complete when a successfully executed node has no outgoing edge. Complete is now hidden from normal authoring and the canvas displays derived virtual END markers automatically. Already-saved and published graphs containing `end.complete` remain compatible.

### How does the Send email step use the visual Email Template Builder?

Choose **Email Template** in the Send email step and select any enabled template that is either unscoped or scoped to the workflow's primary DocType. Visual templates open in the installed `/builder` editor; authorized Email Designers can also create a new visual template without leaving the step. Standard Frappe Email Templates remain selectable, and **Quick email** keeps the earlier inline subject/message behavior for one-off content and existing workflows.

The template is a live reusable reference, matching the useful HubSpot behavior: editing and saving that Email Template changes the content used by future workflow executions without republishing every workflow that references it. Each actual send stores the selected template name and a content hash in the step output, so the execution remains traceable. Published workflow settings still pin the workflow-level sender and timing policy.

Before publishing, an author can optionally select a real record of the workflow's primary DocType, render its personalization, and switch the isolated preview between desktop and mobile widths. **Send test** queues one explicit recipient with a `[TEST]` prefix, never enrolls or contacts the selected record automatically, omits the unsubscribe link, and is limited to ten tests per user per ten minutes. Real workflow delivery selects an enabled outgoing Email Account and records the Frappe Email Queue identity used by later email-event waits. Every workflow email first checks Frappe's global `Email Unsubscribe` records, record-specific unsubscribe, and applicable standard Lead/Contact opt-out fields. When an optional Reach Subscription Topic is selected, FinbyzReach is consulted read-only for topic-wise suppression after the recipient is resolved to a Lead. Suppression completes the action without sending and leaves an auditable reason. Workflow email unsubscribe links use Frappe's standard endpoint; Reach's `/manage_subscriptions` page remains exclusively owned by campaign recipients and topic preferences, and no FinbyzReach source file was changed by this implementation. The former **Require current email consent** field and email runtime consent gate remain removed as requested; SMS and Instagram consent controls are unchanged.

### How does Wait until event work now?

The editor follows the same decision order as HubSpot:

1. Choose whether the event belongs to **this workflow record** or a **record/message from an earlier action**.
2. Choose a compatible event from the grouped dropdown.
3. For an earlier-action source, choose the exact guaranteed step. Email events offer Send email; Record updated offers Create/Copy record; Task completed offers Create ToDo.
4. Optionally filter the event's own properties, such as changed fields, email type, clicked URL, status, assignee, list, form, or call outcome.
5. Choose **For a set time** or **As long as possible**. A finite wait may optionally create separate Event happened and Time ran out paths; otherwise both outcomes use one next action.

Only occurrences after the run enters the wait are eligible. Each timer stores and indexes the enrolled record plus the selected occurrence source. Native record/task events release after the source transaction commits. If the maximum time has already elapsed, a late matching event follows the timeout outcome even when the minute scheduler has not processed that timer yet.

### Can an email event be used after a Send email step?

Yes. Place **Wait until event** after **Send email**, set **Event belongs to** to **Record or message from an earlier action**, choose opened, clicked, bounced, complained, or unsubscribed, and select the earlier Send email action. The runtime stores the Email Queue message ID produced by that step and only releases the wait for an event carrying the same message ID.

Frappe Communication transitions now produce these normalized events with Email Queue/message correlation. The configured email provider must actually report opened/clicked/bounce/complaint/unsubscribe status to Frappe; FinbyzAI cannot infer an engagement event that the provider never supplies.

### How do the other event choices actually work?

| Event choice | Correct source and record behavior | Current end-to-end state |
|---|---|---|
| Record created | Native Frappe hook on the workflow's primary DocType. A Lead workflow observes Lead inserts; an Opportunity workflow observes Opportunity inserts; a Customer workflow observes Customer inserts. | Implemented through the dedicated Record created trigger; no integration event is needed. |
| Record changed | Native Frappe hook on the workflow's primary DocType. Authors may select up to fifty permitted fields whose change should count, followed by optional current-state criteria. | Implemented through the dedicated Record changed trigger, expanded safe metadata catalogue, permission validation, changed-field outbox evidence, and explicit watched-field UI. Protected/system fields remain excluded. |
| Record meets criteria / Lead qualified | Native create/update evaluation of the primary DocType. For Lead, use `qualification_status = Qualified`. | Filter enrollment is native, and transition into Qualified now also emits the Lead-only event for event enrollment or waits. |
| Contact joined a list | Email Group membership producer resolves the member email to matching Contact or Lead records and signals those records. | Additions and re-subscriptions are connected idempotently; existing audiences can be enrolled with the standard preview/backfill operation. |
| Form submitted | The Frappe Web Form save supplies the configured form and exact target record created or updated by the form. | Connected through the generic authoritative post-save hook and not forced to Contact only. |
| Contact called us | A terminal inbound Aircall Call Log supplies outcome, duration, called number, and Call Log identity. Stored CRM links resolve Lead/Opportunity/Customer; normalized phone matching resolves Contact. | Connected end to end for the installed Aircall integration and limited to the four supported workflow DocTypes. |
| Email opened/clicked/bounced/complained/unsubscribed | Provider-updated Frappe Communication/Email Unsubscribe records supply the enrolled record and message ID. In a wait, the message ID can be bound to a preceding Send email output. | Native producers, correlation, filters, waiting, and enrollment are implemented; provider configuration/UAT remains required. |
| Contact logged into store | Customer Portal maps a new authenticated website-user session through Portal User or linked Contact to Customer. | Connected end to end for Customer workflows; Lead/Opportunity options are deliberately absent. |
| Contact/customer ordered | ERPNext Sales Order maps directly through `Sales Order.customer`; Shopping Cart source Quotations identify portal checkout. An order-based workflow still uses native Sales Order created. | Connected end to end for Customer workflows. |
| Order abandoned | The hourly ERPNext Shopping Cart adapter treats an unchanged draft cart older than 24 hours and not converted into a Sales Order as abandoned. | Connected for linked Lead, Customer, and Contact records with an idempotent cart occurrence key. |

The UI shows only event choices that make sense for the selected primary DocType, explains the authoritative producer, and keeps provider setup requirements visible.

## Completion backlog and release handoff

There are no remaining implementation rows in the supplied 100-requirement client matrix. The remaining work is environment configuration and acceptance, not missing source behavior.

### P0 — Controlled acceptance before enabling external actions

1. Select and verify one of the site's enabled outgoing Email Accounts, configure the currently missing SMS provider, and then verify one email delivery and one consented SMS delivery under controlled UAT.
2. Confirm the email provider writes the expected Frappe Communication delivery statuses for bounce, open, click, complaint, and unsubscribe; verify finite, indefinite, same-record, and preceding-email waits end to end.
3. Verify one terminal inbound Aircall Call Log resolves to each CRM object type actually used by the client.
4. Verify one Customer Portal login, Shopping Cart conversion, and 24-hour abandoned-cart sample in UAT.
5. Configure an enabled allowlisted Meta integration secret and a consent record, then verify an Instagram send and provider response.
6. Enable/authenticate Asana Settings and verify create task, update task, create subtask, and create project against the client's workspace.

### P1 — Client acceptance walkthrough

1. Build the client's screenshot example with named ordered If/else paths, AND conditions within a group, OR between groups, and automatic None.
2. Demonstrate sidebar resize and independent vertical scrolling at the client's screen width.
3. Demonstrate catalogue drag/drop, edge rewiring, action relocation, connected-section copy/paste, undo/redo, autosave, derived END, simulation, and publish.
4. Confirm that “joined list” is occurrence-based and that existing list members enter through a controlled backfill.
5. Confirm that Verify email means deterministic syntax validation; mailbox deliverability would be a separately selected provider integration.
6. Select and create a visual Email Template from Send email, preview it as a real client record at desktop/mobile widths, send a controlled test, edit the saved template, and confirm a later execution uses the updated saved content.

### Future features outside this feedback baseline

HubSpot-specific AI/marketing actions, WhatsApp, marketplace connectors not installed on this bench, proactive performance-summary recipients, and bounded loop semantics remain separate future requirements rather than gaps in the supplied client scope.

## Implementation sequencing and compatibility notes

- The redesigned branch action is implemented as version 2. Existing published `condition.if_else` version 1 and `condition.switch` nodes remain executable and viewable.
- New `trigger.event` version 2 nodes support ordered OR event groups and event/record criteria. Existing single-event version 1 triggers remain executable.
- `trigger.any` publishes one active subscription per mixed created/changed/criteria/event group while retaining one visual start boundary.
- New `delay.until_event` version 2 nodes choose the enrolled record or a compatible earlier action output, persist exact source identity, support event-property filters and finite/indefinite waits, and use one continuation unless finite-timeout branching is enabled. Existing two-output version 1 waits remain executable.
- New `action.send_email` version 2 nodes default to a reusable Email Template and expose visual-builder authoring, preview, and test controls. Version-1 and inline configurations remain executable; saved templates are referenced live while actual sends record their content hash.
- `delay.drip` uses a database cursor locked by published version and node so batches remain correct across concurrent workers and retries.
- `condition.random_split` uses a deterministic hash of run, node, and occurrence. Retries cannot reassign a record to another percentage path.
- Workflow action windows are stored in immutable version settings and use the same durable timer model as delays; only `action.*` nodes are held.
- Triggers and event waits share stable event-catalogue keys, but receive separate DocType-aware availability lists; provider adapters should signal those keys rather than add provider logic to the generic engine.
- `primary_doctype` is the workflow-object boundary. Adapters must resolve associated Contact/Lead/Customer/Opportunity/Order data explicitly and pass the enrolled record identity; the generic engine must not guess associations.
- Canvas insertion, structural action relocation, and node/connected-section clipboard operations are atomic graph commands so undo/redo, autosave, conflict detection, and validation remain reliable.
- Implicit completion remains runtime truth; END markers are derived presentation rather than persisted executable nodes for new graphs.
- Keep integration-specific behavior behind adapters/plugins or stable service boundaries rather than embedding provider logic directly into the generic workflow engine.
- Detailed terminal execution history is retained for at least 180 days; durable enrollment identity and aggregates survive detail cleanup.
- Contact merge uses Frappe's native merge path, exact matching, ambiguity rejection, and normal permissions. It must still be treated as destructive during publish review.
- New authoring uses server-supplied `core`, `advanced`, and `danger` presentation tiers plus context availability. These hints never disable execution of an already published compatible graph.
- Round robin creates normal Frappe ToDo assignments. Authoring now validates the same record-read and ToDo-create permissions used by runtime instead of incorrectly requiring a writable owner field.
- Exact duplicate OR trigger groups are rejected at validation; two triggers of the same type remain valid when their event or record filters differ.
- Simulation outputs now match stable runtime output names for data checks, record operations, notifications, integrations, and terminal controls. Values that cannot exist before an external mutation remain explicitly predicted rather than fabricated as observed facts.

## Acceptance criteria for closing this gap analysis

A future implementation can mark a row **Implemented** only when:

1. the capability is discoverable and configurable in the user interface;
2. draft validation and published runtime semantics agree;
3. permissions, retry/idempotency, and audit behavior are defined where relevant;
4. automated tests cover the happy path and material failure/edge cases; and
5. client-facing wording and behavior match the requirement, not merely an underlying technical primitive.

## Verification record

The final implementation/document baseline was checked on 2026-08-21 with:

- 198 focused FinbyzAI workflow/backend integration tests plus 16 existing FinbyzAI tests (214 total), covering schema/output contracts, authoring/runtime, exact event producers, external/email actions, inbound webhooks, schedules/backfills, collaboration, safety, recovery, and real Opportunity certification;
- 98 frontend tests, including multi-trigger cards, guided insertion, mutually exclusive on-demand workspaces, Delay disclosure, context-unavailable/advanced catalogue behavior, inspector resize, email authoring, and editor overlays;
- TypeScript type checking, frontend lint, unstaged/staged `git diff --check`, and complete backend execution through Frappe's test runner;
- a successful Vite production frontend build;
- a successful site migration with the managed webhook/comment/schedule schema present;
- a clean bench restart with web, Socket.IO, scheduler, and both workers running;
- an authenticated-route redirect to the Frappe login page and an HTTP 200 response for the exact newly built workflow JavaScript asset;
- live site calls confirming context-aware catalogue tiers and canvas metrics for `AWF-04525` (one enrollment and five reached nodes); and
- live recovery confirming zero queued/running orphan runs, zero stale external effects, zero pending/retrying/dead outbox rows, two available workers, and no new Error Log rows after the final deployment; existing operator-owned incidents/dead letters remain visible rather than being silently deleted; and
- recalculation of the 100 client-requirement statuses from the matrix rows.

The original client matrix contains exactly 100 requirements. The separate 40-row HubSpot feature-family comparison is contextual and intentionally excluded from the client coverage totals.

## Change log

| Date | Feedback batch | Change |
|---|---|---|
| 2026-08-20 | Initial requirements + feedback batch 1 | Created the source-backed baseline gap analysis with 98 assessed requirements and prioritized remediation. |
| 2026-08-20 | P0 implementation slice | Added named criteria branches, improved waits/events, fast canvas authoring, node clipboard operations, and virtual END markers; recalculated coverage to 38 Implemented, 29 Partial, 30 Missing, and 1 Needs product decision. |
| 2026-08-20 | HubSpot behavior alignment | Added the public HubSpot reference model, 20-branch first-match behavior, filter and multi-event enrollment, event criteria, optional event-delay outcome branches, and exact branch-output drop targets; recalculated coverage to 38 Implemented, 30 Partial, 29 Missing, and 1 Needs product decision. |
| 2026-08-20 | Client feedback batch 2 | Simplified If/else into understandable named paths, automatically upgraded editable legacy binary branches, stopped blank rows producing misleading field-permission errors, grouped genuine repeated errors, and added prior-Send-email event correlation; recalculated coverage to 38 Implemented, 31 Partial, 29 Missing, and 1 Needs product decision. |
| 2026-08-20 | Object-aware event alignment | Mapped HubSpot workflow object types to Frappe primary DocTypes, split event availability by enrollment/wait usage, added inline producer and record-resolution guidance, clarified native lifecycle triggers for Lead/Opportunity/Customer/Contact, and recalculated coverage to 39 Implemented, 32 Partial, 28 Missing, and 1 Needs product decision. |
| 2026-08-20 | Installed event adapters | Connected Email Group membership, Lead qualification, terminal inbound Aircall calls, Customer Portal login, and ERPNext Sales Order creation to the normalized event service; restricted commerce events to Customer and call events to Contact/Lead/Opportunity/Customer; recalculated coverage to 42 Implemented, 29 Partial, 28 Missing, and 1 Needs product decision. |
| 2026-08-20 | Full HubSpot feature-family reconciliation | Removed repeated Lead wording from the event picker, documented all current official HubSpot workflow feature families at the platform-fit level, added deterministic percentage splits and workflow-wide action timing, confirmed revision restore, and recalculated the original 100 client requirements to 45 Implemented, 28 Partial, 26 Missing, and 1 Needs product decision. |
| 2026-08-20 | If/else filter-group simplification | Replaced arbitrary nesting in new If/else authoring with understandable HubSpot-style logic: AND within each filter group, OR between groups, ordered named paths, and automatic None. Existing advanced saved expressions remain executable and are never silently rewritten. |
| 2026-08-20 | Full client-gap implementation | Added mixed enrollment groups, Web Form/Communication/unsubscribe/abandoned-cart producers, connected-section clipboard commands, resizable/scrollable inspector, folders, 180-day retention, response/sender policies, compound dedup/Contact operations, notifications, rich transforms, Go To, remove/goal/drip actions, Instagram, and Asana; recalculated the supplied 100 requirements to 100 Implemented. |
| 2026-08-20 | Guided editor insertion and visual hierarchy | Matched the client's HubSpot-style authoring flow with compact insertion controls, color-separated branch paths, automatic lane layout, explicit legacy-orphan treatment, placement-aware catalogue selection, atomic rewiring, orphan prevention, and manual link editing disabled by default behind an advanced toggle. |
| 2026-08-20 | Workflow email authoring | Expanded Send email into a HubSpot-aligned authoring flow using the installed visual Email Template Builder: compatible saved-template selection, in-place visual-template creation, real-record personalization, desktop/mobile preview, controlled test sends, subject/sender/reply-to controls, live saved-template reuse, traceable content hashes, and backward-compatible inline email. |
| 2026-08-20 | Event-wait and email-consent alignment | Removed the Require current email consent UI/runtime gate; implemented HubSpot-style enrolled-record versus earlier-action data sources, compatible source actions, event filters, finite/indefinite waits, exact indexed source matching, after-commit Record updated/ToDo completed producers, timeout-race protection, safe timeout-edge UI transitions, and backend/frontend regression coverage. |
| 2026-08-21 | Folder and unsubscribe correction | Replaced the workflow-list browser prompt with an accessible themed move dialog. Made FinbyzAI enforce native Frappe/CRM global suppression for every workflow email, added optional read-only Reach topic suppression and an auditable suppressed outcome, retained Frappe's standard workflow unsubscribe link, and explicitly preserved Reach's topic-only preference-page boundary without modifying FinbyzReach. |
| 2026-08-21 | Whole-feature fit, simplicity, and parity audit | Reclassified uncommon operations as advanced and deletion as destructive, added context-aware availability reasons, kept trigger setup out of the step catalogue and delays behind one entry, rejected identical OR triggers, corrected round-robin permissions to match Frappe ToDo runtime behavior, aligned simulation output keys with runtime for reliable output-based branches, reconciled webhook/schedule gap statuses, ran the full focused verification set, migrated, rebuilt, restarted, and smoke-tested the live site. |
| 2026-08-21 | Final cross-layer and UI completion audit | Made catalogue and inspector on-demand and mutually exclusive, corrected conditional action contracts, mandatory-field clear handling, node summaries, email sender selection, and derived Connections; added duplicate-safe stale external-effect reconciliation and orphan-run quarantine; passed 214 backend tests and 98 frontend tests, rebuilt, migrated, restarted, and verified the deployed asset and live runtime state. |
