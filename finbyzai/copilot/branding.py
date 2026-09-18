# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Provider logos for the model picker.

The app ships a monochrome mark per provider in `finbyzai/public/provider_logos/`
(from simple-icons, CC0-1.0, so redistributing them is fine). A provider is matched
by its `LLM Provider` name first and by the model id second, because a gateway like
OpenRouter serves models from everyone: `openrouter/anthropic/claude-3-haiku` should
show Anthropic's mark, not OpenRouter's.

An `LLM Provider.logo` upload always wins, so a self-hosted or in-house provider can
carry its own badge without touching this file.
"""

import frappe

ASSET_PATH = "/assets/finbyzai/provider_logos"

# Matched against the provider name and the model id, longest key first so
# "googlegemini" cannot lose to "google".
LOGOS = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt-": "openai",
    "o1-": "openai",
    "o3-": "openai",
    "o4-": "openai",
    "google": "google",
    "gemini": "google",
    "gemma": "google",
    "openrouter": "openrouter",
    "mistral": "mistral",
    "magistral": "mistral",
    "voxtral": "mistral",
    "meta": "meta",
    "llama": "meta",
    "deepseek": "deepseek",
    "qwen": "qwen",
    "alibaba": "qwen",
    "nvidia": "nvidia",
    "nemotron": "nvidia",
    "ollama": "ollama",
    "huggingface": "huggingface",
    "perplexity": "perplexity",
    "sonar": "perplexity",
    "xai": "xai",
    "grok": "xai",
    "azure": "azure",
    "microsoft": "azure",
    "phi-": "azure",
    "bedrock": "aws",
    "amazon": "aws",
    "aws": "aws",
}


def logo_for(provider: str | None = None, model: str | None = None) -> str | None:
    """A logo URL for this provider/model, or None when nothing matches."""
    uploaded = _uploaded(provider)
    if uploaded:
        return uploaded

    slug = _match_model(model) or _match(provider)
    return f"{ASSET_PATH}/{slug}.svg" if slug else None


def _match_model(model: str | None) -> str | None:
    """Match a model id from its most specific segment outwards.

    Model ids read gateway-first — `openrouter/anthropic/claude-3-haiku` — so a plain
    substring search finds "openrouter" and shows the wrong mark. Reading the segments
    right to left puts the actual vendor first and leaves the gateway as the fallback.
    """
    if not model:
        return None
    for segment in reversed(str(model).split("/")):
        slug = _match(segment)
        if slug:
            return slug
    return _match(model)


def _match(text: str | None) -> str | None:
    if not text:
        return None
    text = str(text).lower()
    for key in sorted(LOGOS, key=len, reverse=True):
        if key in text:
            return LOGOS[key]
    return None


def _uploaded(provider: str | None) -> str | None:
    if not provider:
        return None
    try:
        return frappe.get_cached_value("LLM Provider", provider, "logo") or None
    except Exception:
        return None
