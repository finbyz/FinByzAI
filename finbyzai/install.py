"""Installation and migration hooks for finbyzai."""

import json

import frappe


LLM_PROVIDERS = [
    {"provider": "Anthropic", "disabled": 0},
    {"provider": "DeepSeek", "disabled": 0},
    {"provider": "Google", "disabled": 0},
    {"provider": "OpenAI", "disabled": 0},
    {"provider": "Perplexity", "disabled": 0},
]

LLMS = [
    # ── Anthropic ──
    {"name": "anthropic/claude-3-5-haiku-latest", "provider": "Anthropic", "title": "Anthropic Claude Haiku 3.5", "size": "Small", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "anthropic/claude-3-7-sonnet-latest", "provider": "Anthropic", "title": "Anthropic Claude Sonnet 3.7", "size": "Medium", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "anthropic/claude-opus-4-20250514", "provider": "Anthropic", "title": "Anthropic Claude Opus 4", "size": "Large", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "anthropic/claude-opus-4-1-20250805", "provider": "Anthropic", "title": "Anthropic Claude Opus 4.1", "size": "Large", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "anthropic/claude-sonnet-4-20250514", "provider": "Anthropic", "title": "Anthropic Claude Sonnet 4", "size": "Medium", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},

    # ── DeepSeek ──
    {"name": "deepseek/deepseek-chat", "provider": "DeepSeek", "title": "DeepSeek Chat", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "deepseek/deepseek-reasoner", "provider": "DeepSeek", "title": "DeepSeek Reasoner (R1)", "size": "Large", "is_reasoning": 1, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "deepseek/deepseek-coder", "provider": "DeepSeek", "title": "Deepseek Coder", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},

    # ── Google ──
    {"name": "gemini-embedding-001", "provider": "Google", "title": "Gemini Embedding 001", "size": "Medium", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 1, "enabled": 1},
    {"name": "gemini/gemini-3-pro-preview", "provider": "Google", "title": "Gemini 3 Pro Preview", "size": "Medium", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.5-pro-preview-06-05", "provider": "Google", "title": "Google Gemini 2.5 Pro (Preview)", "size": "Medium", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 0},
    {"name": "gemini/gemini-2.5-flash-preview-05-20", "provider": "Google", "title": "Google Gemini 2.5 Flash (Preview)", "size": "Small", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 0},
    {"name": "gemini-2.5-flash-image-preview", "provider": "Google", "title": "Gemini 2.5 Flash Image", "size": "Medium", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.5-flash-preview-04-17", "provider": "Google", "title": "Gemini 2.5 Flash Preview", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.5-flash-image-preview", "provider": "Google", "title": "Gemini 2.5 Flash Image Preview", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini-1.5-pro-vision", "provider": "Google", "title": "Gemini 1.5 Pro Vision", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.5-pro", "provider": "Google", "title": "Google Gemini 2.5 Pro", "size": "Medium", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.5-flash-lite", "provider": "Google", "title": "Google Gemini 2.5 Flash-Lite", "size": "Very Small", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/imagen-4.0-generate-001", "provider": "Google", "title": "Google Imagen 40.0 generate 001", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-1.5-flash-002", "provider": "Google", "title": "Gemini-1.5 Flash 002", "size": "Very Small", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.0-flash-exp-image-generation", "provider": "Google", "title": "Google Gemini image model", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini/gemini-2.5-flash", "provider": "Google", "title": "Google Gemini 2.5 Flash", "size": "Small", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "gemini-2.5-flash-image", "provider": "Google", "title": "Gemini 2.5 Flash Image", "size": "Medium", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},

    # ── OpenAI ──
    {"name": "text-embedding-3-small", "provider": "OpenAI", "title": "OpenAI Text Embedding 3 Small", "size": "Small", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 1, "enabled": 1},
    {"name": "openai/o3", "provider": "OpenAI", "title": "OpenAI o3", "size": "Medium", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 0},
    {"name": "openai/gpt-4o", "provider": "OpenAI", "title": "OpenAI GPT-4o", "size": "Medium", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "openai/gpt-5", "provider": "OpenAI", "title": "OpenAI GPT-5", "size": "Medium", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 0},
    {"name": "openai/gpt-5-mini", "provider": "OpenAI", "title": "OpenAI GPT-5 mini", "size": "Small", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 0},
    {"name": "openai/o4-mini", "provider": "OpenAI", "title": "OpenAI o4 mini", "size": "Small", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "openai/gpt-5-nano", "provider": "OpenAI", "title": "OpenAI GPT-5 nano", "size": "Very Small", "is_reasoning": 1, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "openai/gpt-4.1-nano", "provider": "OpenAI", "title": "OpenAI GPT-4.1 nano", "size": "Very Small", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "gpt-image-1", "provider": "OpenAI", "title": "GPT Image 1", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "dall-e-3", "provider": "OpenAI", "title": "DALL-E 3", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "dall-e-2", "provider": "OpenAI", "title": "DALL-E 2", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 1, "is_embedding_model": 0, "enabled": 1},
    {"name": "openai/gpt-4o-mini", "provider": "OpenAI", "title": "OpenAI GPT-4o mini", "size": "Small", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "openai/gpt-4.1", "provider": "OpenAI", "title": "OpenAI GPT-4.1", "size": "Medium", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "openai/gpt-4.1-mini", "provider": "OpenAI", "title": "OpenAI GPT-4.1 mini", "size": "Small", "is_reasoning": 0, "supports_vision": 1, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "text-embedding-3-large", "provider": "OpenAI", "title": "OpenAI Text Embedding 3 Large", "size": "Large", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 1, "enabled": 1},

    # ── Perplexity ──
    {"name": "perplexity/sonar-deep-research", "provider": "Perplexity", "title": "Perplexity Sonar Deep Research", "size": "Very Small", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "perplexity/sonar-reasoning", "provider": "Perplexity", "title": "Perplexity Sonar Reasoning", "size": "Very Small", "is_reasoning": 1, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "perplexity/sonar-pro", "provider": "Perplexity", "title": "Perplexity Sonar Pro", "size": "Very Small", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "perplexity/sonar", "provider": "Perplexity", "title": "Perplexity Sonar", "size": "Very Small", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "perplexity/r1-1776", "provider": "Perplexity", "title": "Perplexity r1-1776", "size": "Very Small", "is_reasoning": 0, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
    {"name": "perplexity/sonar-reasoning-pro", "provider": "Perplexity", "title": "Perplexity Sonar Reasoning Pro", "size": "Very Small", "is_reasoning": 1, "supports_vision": 0, "supports_image_generation": 0, "is_embedding_model": 0, "enabled": 1},
]

BUILTIN_AI_TOOLS = [
    {
        "name": "Web Search",
        "tool_type": "Provider Built-in",
        "tool_key": "web_search",
        "enabled": 1,
        "execution_side": "Provider",
        "cost_sensitive": 1,
        "requires_confirmation": 0,
        "description": (
            "Search the public web for current information using the model "
            "provider's managed search capability."
        ),
        "configuration_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "provider_mappings": [
            {
                "provider": "Google",
                "model_pattern": r"^gemini/gemini-(2\.0|2\.5|3).*",
                "transport_strategy": "LiteLLM Parameter",
                "enabled": 1,
                "request_template": {"web_search_options": {}},
                "compatibility_rules": {},
                "response_mapping": {},
            }
        ],
    }
]


def after_migrate():
    """Create default providers, models, and built-in tool definitions."""
    _sync_llm_providers()
    _sync_llms()
    _sync_builtin_ai_tools()


def _sync_llm_providers():
    """Ensure all default LLM Providers exist."""
    for provider_data in LLM_PROVIDERS:
        if frappe.db.exists("LLM Provider", provider_data["provider"]):
            continue
        doc = frappe.get_doc({
            "doctype": "LLM Provider",
            "provider": provider_data["provider"],
            "disabled": provider_data["disabled"],
        })
        doc.insert(ignore_permissions=True)


def _sync_llms():
    """Ensure all default LLMs exist."""
    for llm_data in LLMS:
        if frappe.db.exists("LLM", llm_data["name"]):
            continue
        doc = frappe.get_doc({
            "doctype": "LLM",
            **llm_data,
        })
        doc.insert(ignore_permissions=True)


def _sync_builtin_ai_tools():
    """Create missing standard tool definitions without overwriting user changes."""
    for tool_data in BUILTIN_AI_TOOLS:
        if frappe.db.exists("AI Tool", tool_data["name"]):
            continue

        doc = frappe.new_doc("AI Tool")
        for fieldname, value in tool_data.items():
            if fieldname in ("name", "provider_mappings"):
                continue
            if fieldname == "configuration_schema":
                value = json.dumps(value, indent=2)
            setattr(doc, fieldname, value)
        doc.name = tool_data["name"]

        for mapping in tool_data["provider_mappings"]:
            row = dict(mapping)
            for fieldname in (
                "request_template",
                "response_mapping",
                "compatibility_rules",
            ):
                row[fieldname] = json.dumps(row[fieldname], indent=2)
            doc.append("provider_mappings", row)

        doc.insert(ignore_permissions=True)
