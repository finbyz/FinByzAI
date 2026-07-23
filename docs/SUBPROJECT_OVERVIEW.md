# Finbyz AI Subproject Overview

Generated from the local bench on 2026-07-17.

## Registry

| Field | Value |
| --- | --- |
| Project | `megasol` |
| App key | `finbyzai` |
| Display name | Finbyz AI |
| Registry description | AI-powered features app |
| Repo path | `apps/finbyzai` |

## Purpose

AI-Powered Agents, Tools, and Knowledge Base Platform

## Source Layout

| Area | Local findings |
| --- | --- |
| Frappe modules | `FinByz AI`, `AI` |
| Important directories | `ai`, `config`, `finbyz_ai`, `fixtures`, `public`, `templates`, `docs`, `finbyzai` |
| Frappe hook integrations | Document event hooks, Scheduled jobs |

## Feature Signals

- No high-level feature classifier matched; inspect the modules, DocTypes, and existing docs below.

## Frappe Data Model

### DocTypes

- `AI Agent`
- `AI Agent Tool`
- `AI Conversation`
- `AI Conversation Message`
- `AI Links`
- `AI Note`
- `AI Tool`
- `Chat Message`
- `Gemini Cache`
- `Knowledge Base`
- `Knowledge Document`
- `LLM`
- `LLM Provider`
- `Pinecone Settings`
- `Qdrant Settings`
- `Reddit Campaign`
- `Supabase Settings`

### Pages

- None found in the local source tree.

## Public and Frontend Assets

- No public JavaScript files found outside generated/dist folders.

## Existing Documentation

- `README.md`

## Test Coverage Pointers

- `finbyzai/ai/doctype/ai_agent/test_ai_agent.py`
- `finbyzai/ai/doctype/ai_conversation/test_ai_conversation.py`
- `finbyzai/ai/doctype/ai_tool/test_ai_tool.py`
- `finbyzai/ai/doctype/gemini_cache/test_gemini_cache.py`
- `finbyzai/ai/doctype/knowledge_base/test_knowledge_base.py`
- `finbyzai/ai/doctype/llm/test_llm.py`
- `finbyzai/ai/doctype/llm_provider/test_llm_provider.py`
- `finbyzai/ai/doctype/pinecone_settings/test_pinecone_settings.py`
- `finbyzai/ai/doctype/qdrant_settings/test_qdrant_settings.py`
- `finbyzai/ai/doctype/reddit_campaign/test_reddit_campaign.py`
- `finbyzai/ai/doctype/supabase_settings/test_supabase_settings.py`

## Maintenance Notes

- Keep this file aligned with `project-memory.yaml` whenever the app key, description, or repo path changes.
- Add focused feature docs under `apps/finbyzai/docs` when implementing workflows that span multiple modules or DocTypes.
- Re-run documentation indexing with `index_docs({"project": "megasol", "app": "finbyzai", "force": true})` after significant documentation changes.
