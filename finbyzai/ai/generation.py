"""Model-aware generation controls shared by agents and workflows."""

import re

import frappe
import litellm
from langchain_litellm import ChatLiteLLM
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexGeminiConfig

LEVELS = ("Default", "Low", "Medium", "High")
SAMPLING = ("temperature", "top_p", "top_k")


class GenerationPolicy:
    def __init__(self, model):
        self.model = model
        self.native = model.removeprefix("openrouter/")
        self.info = self._model_info()

    @property
    def levels(self):
        try:
            supported = litellm.get_supported_openai_params(model=self.model) or []
        except (ValueError, litellm.BadRequestError):
            return ["Default"]
        known = self.info.get("supports_reasoning")
        if known is None:
            known = self._known_reasoning_model()
        if "reasoning_effort" not in supported or not known:
            return ["Default"]
        levels = list(LEVELS)
        if re.search(r"gemini-[3-9](?:[.-]|$)", self.native):
            native_model = self.native.split("/")[-1]
            levels = [level for level in levels if level == "Default" or
                      VertexGeminiConfig._map_reasoning_effort_to_thinking_level(
                          level.lower(), native_model
                      )["thinkingLevel"] == level.lower()]
        for level in levels.copy():
            if self.info.get(f"supports_{level.lower()}_reasoning_effort") is False:
                levels.remove(level)
        return levels

    @property
    def ignores_sampling(self):
        gemini = re.search(r"gemini-(\d+)(?:\.(\d+))?", self.native)
        if gemini and (int(gemini[1]), int(gemini[2] or 0)) >= (3, 6):
            return True
        if "gpt-5-chat" in self.native:
            return False
        if self.info.get("supports_none_reasoning_effort"):
            return False
        return bool(re.search(r"(?:^|/)openai/(?:o[134](?:-|$)|gpt-5(?:[.-]|$))", self.native))

    def validate(self, level):
        level = normalize_level(level)
        if level == "Default":
            return level
        if level not in self.levels:
            raise ValueError(
                f"{self.model} does not support Thinking Level {level}. "
                f"Choose {', '.join(self.levels)}."
            )
        return level

    def resolve(self, params, level="Default"):
        params = dict(params)
        level = self.validate(level)
        if level != "Default":
            if params.get("thinking") or params.get("thinking_budget"):
                raise ValueError("Thinking Level cannot be combined with a thinking budget.")
            params["reasoning_effort"] = level.lower()
        effort = params.get("reasoning_effort")
        if effort:
            self.validate(str(effort).title())
            if params.get("thinking") or params.get("thinking_budget"):
                raise ValueError("Reasoning effort cannot be combined with a thinking budget.")
        anthropic = "anthropic/" in self.native
        thinking = params.get("thinking")
        if anthropic and effort:
            native_model = self.native.split("anthropic/", 1)[1]
            thinking = AnthropicConfig._map_reasoning_effort(effort, native_model, "anthropic")
        if anthropic and thinking and thinking.get("type") != "disabled":
            budget = thinking.get("budget_tokens")
            limit = params.get("max_completion_tokens") or params.get("max_tokens")
            if not limit:
                limit = AnthropicConfig.get_max_tokens_for_model(self.native.split("anthropic/", 1)[1])
            if budget and budget >= limit:
                raise ValueError(
                    f"{self.model} needs Max Tokens greater than its thinking budget ({budget}). "
                    "Increase Max Tokens or choose a lower Thinking Level."
                )
        reasoning_sampling = "openai/" in self.native and effort and self.info.get("supports_none_reasoning_effort")
        if self.ignores_sampling or reasoning_sampling or (anthropic and thinking and thinking.get("type") != "disabled"):
            for key in SAMPLING:
                params.pop(key, None)
        return params

    def _model_info(self):
        # Use the installed map: capability checks must not fetch account data.
        for name in (self.model, self.native, self.native.split("/", 1)[-1]):
            if name in litellm.model_cost:
                return litellm.model_cost[name]
        return {}

    def _known_reasoning_model(self):
        return bool(re.search(
            r"gemini-(?:2\.5|[3-9])(?:[.-]|$)|(?:^|/)openai/(?:o[134](?:-|$)|gpt-5(?:[.-]|$))"
            r"|anthropic/claude-(?:3-7|(?:sonnet|opus)-4)", self.native
        ))


class ProviderChatLiteLLM(ChatLiteLLM):
    thinking_level: str = "Default"

    @property
    def _identifying_params(self):
        return {**super()._identifying_params, "thinking_level": self.thinking_level}

    def completion_with_retry(self, run_manager=None, **kwargs):
        level = kwargs.pop("thinking_level", self.thinking_level)
        params = GenerationPolicy(kwargs.get("model") or self.model).resolve(kwargs, level)
        return super().completion_with_retry(run_manager=run_manager, **params)

    async def acompletion_with_retry(self, run_manager=None, **kwargs):
        level = kwargs.pop("thinking_level", self.thinking_level)
        params = GenerationPolicy(kwargs.get("model") or self.model).resolve(kwargs, level)
        return await super().acompletion_with_retry(run_manager=run_manager, **params)


def normalize_level(level):
    level = str(level or "Default").title()
    if level not in LEVELS:
        raise ValueError(f"Unknown Thinking Level: {level}")
    return level


def validate_generation(model, level, max_tokens=None):
    try:
        return GenerationPolicy(model).resolve({"max_tokens": max_tokens}, level)
    except ValueError as error:
        frappe.throw(str(error))


@frappe.whitelist()
def generation_capabilities(model):
    frappe.get_doc("LLM", model).check_permission("read")
    policy = GenerationPolicy(model)
    return {"thinking_levels": policy.levels, "ignores_sampling": policy.ignores_sampling}
