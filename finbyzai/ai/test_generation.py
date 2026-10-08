"""Request-boundary tests; no site or paid provider calls are required."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import frappe
from langchain_litellm import ChatLiteLLM
from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexGeminiConfig
from litellm.llms.anthropic.chat.transformation import AnthropicConfig

from finbyzai.ai.generation import GenerationPolicy, ProviderChatLiteLLM


class TestGenerationPolicy(unittest.TestCase):
    def test_model_capabilities(self):
        cases = {
            "gemini/gemini-2.5-flash": ["Default", "Low", "Medium", "High"],
            "gemini/gemini-3-pro-preview": ["Default", "Low", "High"],
            "gemini/gemini-3.8-flash": ["Default", "Low", "Medium", "High"],
            "openai/o3": ["Default", "Low", "Medium", "High"],
            "openai/gpt-4o": ["Default"],
            "anthropic/claude-3-7-sonnet-latest": ["Default", "Low", "Medium", "High"],
            "openrouter/google/gemini-2.5-flash": ["Default", "Low", "Medium", "High"],
            "perplexity/sonar": ["Default"],
        }
        for model, levels in cases.items():
            with self.subTest(model=model):
                self.assertEqual(GenerationPolicy(model).levels, levels)

    def test_default_and_unsupported_levels(self):
        self.assertNotIn("reasoning_effort", GenerationPolicy("unknown/model").resolve({}))
        for model, level in [("openai/gpt-4o", "High"), ("perplexity/sonar", "Low"),
                             ("gemini/gemini-3-pro-preview", "Medium")]:
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "does not support"):
                GenerationPolicy(model).resolve({}, level)

    def test_sampling_and_zero_temperature(self):
        sampling = {"temperature": 0, "top_p": 0.9, "top_k": 10}
        for model in ["gemini/gemini-3.8-flash", "gemini/gemini-4-flash",
                      "openrouter/google/gemini-3.8-flash", "openai/o3"]:
            with self.subTest(model=model):
                self.assertEqual(GenerationPolicy(model).resolve(sampling), {})
        for model in ["gemini/gemini-2.5-flash", "openai/gpt-4o"]:
            self.assertEqual(GenerationPolicy(model).resolve(sampling), sampling)

    def test_google_translation(self):
        config = VertexGeminiConfig()
        for model, key in [("gemini-2.5-flash", "thinkingBudget"), ("gemini-3.8-flash", "thinkingLevel")]:
            mapped = config.map_openai_params({"reasoning_effort": "low"}, {}, model, False)
            self.assertIn(key, mapped["thinkingConfig"])

    def test_anthropic_budget_and_sampling(self):
        policy = GenerationPolicy("anthropic/claude-3-7-sonnet-latest")
        with self.assertRaisesRegex(ValueError, "thinking budget"):
            policy.resolve({"max_tokens": 512}, "Low")
        params = policy.resolve({"max_tokens": 100000, "temperature": 0, "top_p": 0.8}, "High")
        self.assertEqual(params["reasoning_effort"], "high")
        self.assertNotIn("temperature", params)
        self.assertNotIn("top_p", params)
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            policy.resolve({"thinking": {"type": "enabled", "budget_tokens": 1024}}, "Low")

    def test_sync_and_async_final_boundary(self):
        llm = ProviderChatLiteLLM(model="gemini/gemini-3.8-flash", api_key="test")
        params = {"model": llm.model, "temperature": 0.4, "top_p": 0.9, "top_k": 10,
                  "thinking_level": "Low", "tools": [{"type": "function"}], "cached_content": "cachedContents/test"}
        with patch.object(ChatLiteLLM, "completion_with_retry", return_value="ok") as call:
            self.assertEqual(llm.completion_with_retry(**params), "ok")
            forwarded = call.call_args.kwargs
        with patch.object(ChatLiteLLM, "acompletion_with_retry", new_callable=AsyncMock) as call:
            asyncio.run(llm.acompletion_with_retry(**params))
            self.assertEqual(call.call_args.kwargs, forwarded)
        self.assertEqual(forwarded["reasoning_effort"], "low")
        self.assertEqual(forwarded["cached_content"], "cachedContents/test")
        self.assertEqual(forwarded["tools"], params["tools"])
        for key in ["temperature", "top_p", "top_k", "thinking_level"]:
            self.assertNotIn(key, forwarded)

    def test_invoke_bind_and_model_copy(self):
        llm = ProviderChatLiteLLM(model="gemini/gemini-3.8-flash", api_key="test")
        response = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        with patch("litellm.completion", return_value=response) as call:
            result = llm.model_copy(update={"temperature": 0.1}).bind(top_p=0.8, top_k=10).invoke("hello", thinking_level="High")
        self.assertEqual(result.content, "ok")
        self.assertEqual(call.call_args.kwargs["reasoning_effort"], "high")
        self.assertNotIn("temperature", call.call_args.kwargs)
        self.assertNotIn("top_p", call.call_args.kwargs)
        self.assertNotIn("top_k", call.call_args.kwargs)

    def test_streaming_boundary(self):
        llm = ProviderChatLiteLLM(model="gemini/gemini-3.8-flash", api_key="test", thinking_level="Low", streaming=True)
        chunks = [{"choices": [{"delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}]
        with patch("litellm.completion", return_value=iter(chunks)) as call:
            self.assertEqual("".join(chunk.content for chunk in llm.stream("hello", temperature=0.2)), "ok")
        self.assertTrue(call.call_args.kwargs["stream"])
        self.assertNotIn("temperature", call.call_args.kwargs)

    def test_cache_uses_shared_adapter(self):
        from finbyzai.ai.doctype.gemini_cache.gemini_cache import GeminiCache
        provider = SimpleNamespace(get_password=lambda field: "test", api_base=None)
        cache = SimpleNamespace(llm="gemini/gemini-3.8-flash", cache_name="cachedContents/test")
        with patch("frappe.get_value", return_value="Google"), patch("frappe.get_doc", return_value=provider):
            llm = GeminiCache._llm.fget(cache)
        self.assertIsInstance(llm, ProviderChatLiteLLM)
        self.assertEqual(llm.model_kwargs["cached_content"], cache.cache_name)

    def test_copilot_override_validation_and_zero(self):
        from finbyzai.copilot.agent import model
        agent = SimpleNamespace(llm="openai/o3", thinking_level="High", temperature=0, max_tokens=10000)
        settings = SimpleNamespace(default_model="openai/gpt-4o")
        with patch("finbyzai.copilot.agent.access.require_read", side_effect=lambda dt, name, **kw: name), \
             patch("frappe.get_doc", return_value=SimpleNamespace(llm=Mock())), \
             patch("frappe.throw", side_effect=lambda message: (_ for _ in ()).throw(ValueError(message))):
            with self.assertRaisesRegex(ValueError, "does not support"):
                model(agent, settings, override="openai/gpt-4o")
        llm = Mock()
        with patch("finbyzai.copilot.agent.access.require_read", side_effect=lambda dt, name, **kw: name), \
             patch("frappe.get_doc", return_value=SimpleNamespace(llm=llm)):
            model(agent, settings)
        self.assertEqual(llm.bind.call_args.kwargs["temperature"], 0)

    def test_async_invoke_and_stream(self):
        response = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        llm = ProviderChatLiteLLM(model="gemini/gemini-3.8-flash", api_key="test", thinking_level="Medium")
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=response) as call:
            self.assertEqual(asyncio.run(llm.ainvoke("hello", temperature=0.4)).content, "ok")
            self.assertNotIn("temperature", call.call_args.kwargs)

        async def chunks():
            yield {"choices": [{"delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}

        async def consume():
            return "".join([chunk.content async for chunk in llm.astream("hello", top_p=0.9)])

        llm = llm.model_copy(update={"streaming": True})
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=chunks()) as call:
            self.assertEqual(asyncio.run(consume()), "ok")
            self.assertNotIn("top_p", call.call_args.kwargs)
            self.assertEqual(call.call_args.kwargs["reasoning_effort"], "medium")

    def test_workflow_snapshot_and_old_snapshot(self):
        from finbyzai.workflow_builder.ai_support import build_profile_snapshot, _invoke_profile
        model = SimpleNamespace(name="gemini/gemini-3.8-flash", provider="Google", enabled=1,
                                is_embedding_model=0, supports_image_generation=0, llm=Mock())
        provider = SimpleNamespace(disabled=0)
        with patch("frappe.get_doc", side_effect=lambda dt, name: model if dt == "LLM" else provider), \
             patch("finbyzai.workflow_builder.ai_support._has_doc_permission", return_value=True), \
             patch("finbyzai.workflow_builder.ai_support._knowledge_snapshot", return_value=None):
            snapshot = build_profile_snapshot(execution_user="Administrator", inline_config={
                "model": model.name, "thinking_level": "High", "temperature": 0,
            })
        self.assertEqual(snapshot["thinking_level"], "High")
        self.assertEqual(snapshot["temperature"], 0)
        snapshot.pop("thinking_level")
        with patch("frappe.get_doc", return_value=model), \
             patch("finbyzai.workflow_builder.ai_support._assert_provider_circuit_closed"), \
             patch("finbyzai.workflow_builder.ai_support.int_setting", side_effect=lambda field, default: default), \
             patch("finbyzai.workflow_builder.ai_support._provider_prompt", return_value=("system", "hello")):
            _invoke_profile(snapshot, {}, {}, [], {})
        self.assertEqual(model.llm.model_copy.call_args.kwargs["update"]["thinking_level"], "Default")

    def test_thinking_is_part_of_cache_identity(self):
        llm = ProviderChatLiteLLM(model="gemini/gemini-3.8-flash", api_key="test")
        self.assertNotEqual(llm._identifying_params, llm.model_copy(update={"thinking_level": "High"})._identifying_params)

    def test_workflow_agent_snapshot_preserves_level(self):
        from finbyzai.workflow_builder.ai_support import build_profile_snapshot
        agent = frappe._dict(name="Agent", modified="today", agent_type="Chat Agent",
                             llm="openai/o3", thinking_level="Low", tools=[], temperature=0, max_tokens=2048,
                             messages=[frappe._dict(type="system", content_type="text", content="Help.")])
        model = frappe._dict(name="openai/o3", provider="OpenAI", enabled=1,
                             is_embedding_model=0, supports_image_generation=0)
        provider = frappe._dict(disabled=0)
        with patch("frappe.get_doc", side_effect=lambda dt, name: {"AI Agent": agent, "LLM": model, "LLM Provider": provider}[dt]), \
             patch("finbyzai.workflow_builder.ai_support._has_doc_permission", return_value=True), \
             patch("finbyzai.workflow_builder.ai_support._knowledge_snapshot", return_value=None):
            snapshot = build_profile_snapshot("Agent", execution_user="Administrator")
        self.assertEqual(snapshot["thinking_level"], "Low")

    def test_anthropic_default_limit_is_validated(self):
        with patch.object(AnthropicConfig, "get_max_tokens_for_model", return_value=512):
            with self.assertRaisesRegex(ValueError, "thinking budget"):
                GenerationPolicy("anthropic/claude-3-7-sonnet-latest").resolve({}, "Low")


if __name__ == "__main__":
    unittest.main()
