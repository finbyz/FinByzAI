# Finbyz AI — Overview

**Scope**: Subproject of `megasol`  
**Repo path**: `apps/finbyzai`

## Purpose

Finbyz AI owns AI-assisted features and the durable Workflow Builder used to author, publish, execute, test, and operate record-based Frappe automations.

## Key Capabilities

- Frappe AI agents, conversations, tools, providers, and knowledge integrations.
- A React workflow canvas with typed triggers, conditions, transforms, actions, simulation, publication, enrollment, and operations views.
- A version-pinned backend runtime with durable runs, tokens, timers, effects, outbox delivery, incidents, dead letters, policies, and trace evidence.
- Permission-aware DocType/field authoring, safe external actions, and typed value transforms.

## Integration Points

The app runs on Frappe and ERPNext metadata, permissions, background workers, Redis, Email Queue, and SMS Settings. Webhooks use explicitly configured Automation Integration Secrets. The Workflow Builder route is served from the React production bundle under `/workflow`.

## Setup / Entry Points

- Backend package: `finbyzai/workflow_builder`
- Frontend source: `workflow/src`
- Workflow route: `/workflow`
- Site configuration and runtime readiness are exposed in Workflow Builder operations/preflight views.
