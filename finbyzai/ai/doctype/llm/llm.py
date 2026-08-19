# Copyright (c) 2025, sandeep and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe
from langchain_litellm import ChatLiteLLM
from langchain_core.language_models.base import (
	LanguageModelInput,
)
from litellm import image_generation,embedding
from typing import TYPE_CHECKING, Any, Optional
from langchain_core.runnables import RunnableConfig
import os
from finbyzai.ai.doctype.llm.image_llm import ImageGeneration


class LLM(Document):
	@property
	def llm(self):
		provider = frappe.get_doc("LLM Provider", self.provider)
		base_url = self.base_url or provider.base_url

		kwargs = {
			"api_key": provider.get_password("api_key"),
			"model": self.name,
		}
		if base_url:
			kwargs["api_base"] = base_url
			kwargs["custom_llm_provider"] = "openai"

		if self.supports_image_generation:
			if self.provider == "Google":
				os.environ['GEMINI_API_KEY'] = provider.get_password("api_key")

			image_kwargs = {
				"api_key": provider.get_password("api_key"),
			}
			if base_url:
				image_kwargs["api_base"] = base_url

			return ImageGeneration(self.name, **image_kwargs)
		return ChatLiteLLM(**kwargs)


	def get_embeding_function(self):
		provider = frappe.get_doc("LLM Provider", self.provider)
		base_url = self.base_url or provider.base_url

		kwargs = {
			"model": self.name,
			"api_key": provider.get_password("api_key")
		}
		if base_url:
			kwargs["api_base"] = base_url
			kwargs["custom_llm_provider"] = "openai"

		return embedding(**kwargs)

class ImageLiteLLM:
	"""
	Simple wrapper around LiteLLM's image_generation API
	that behaves like a LangChain-style LLM with invoke().
	"""
	def __init__(self, api_key: str, model: str, *args, **kwargs):
		self.api_key = api_key
		self.model = model

	def invoke(
		self,
		input: LanguageModelInput,
		config: Optional[RunnableConfig] = None,
		*,
		stop: Optional[list[str]] = None,
		**kwargs: Any
	):
		prompt: str = ""

		if hasattr(input, "to_string"):
			prompt = input.to_string()

		elif isinstance(input, (list, tuple)):
			try:
				prompt = "\n".join(
					f"{m.role.upper()}: {m.content}" if hasattr(m, "role") else f"{m[0].upper()}: {m[1]}"
					for m in input
				)
			except Exception:
				raise ValueError("Invalid message format. Expected (role, content) tuples or BaseMessage objects.")

		elif isinstance(input, str):
			prompt = input

		else:
			raise ValueError(f"Unsupported LanguageModelInput type: {type(input)}")

		# Call LiteLLM image generation API
		response = image_generation(
			prompt=prompt,
			model=self.model,
			api_key=self.api_key,
			**kwargs,
		)
		return response
