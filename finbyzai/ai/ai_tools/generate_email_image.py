# Auto-generated LangChain tool for: generate_email_image
# Module: AI

from __future__ import annotations

import base64
import json
import re
from typing import Any
import frappe
from frappe import _
from langchain_core.tools import tool


def _resolve_image_agent() -> str:
	"""Get the configured Image Generation AI Agent from Followup Settings or default."""
	if frappe.db.exists("DocType", "Followup Settings"):
		fs = frappe.get_single("Followup Settings")
		configured = fs.get("custom_email_builder_image_agent")
		if configured and frappe.db.exists("AI Agent", configured):
			return configured
	if frappe.db.exists("AI Agent", "Email Builder Image Generator"):
		return "Email Builder Image Generator"
	return ""


@tool("generate_email_image", return_direct=False)
def generate_email_image_tool(input: Any) -> str:
	"""Generate an AI image based on a descriptive prompt, save it as a public Frappe File,
	and return the public URL (e.g. '/files/ai_email_img_abc123.png') to place into an email template image block.

	Args:
	    input (Any): The prompt describing the image to generate (str or dict with 'prompt').

	Returns:
	    str: Public URL of the generated image (e.g. '/files/ai_email_img_abc123.png').
	"""
	prompt = ""
	if isinstance(input, dict):
		prompt = input.get("prompt") or input.get("query") or input.get("input") or ""
	elif isinstance(input, str):
		try:
			parsed = json.loads(input)
			if isinstance(parsed, dict):
				prompt = parsed.get("prompt") or parsed.get("query") or parsed.get("input") or ""
			else:
				prompt = input
		except Exception:
			prompt = input
	else:
		prompt = str(input or "")

	prompt = prompt.strip()
	if not prompt:
		return ""

	agent_name = _resolve_image_agent()
	model = "openrouter/google/gemini-2.5-flash-image"
	api_key = None

	if agent_name and frappe.db.exists("AI Agent", agent_name):
		agent_doc = frappe.get_doc("AI Agent", agent_name)
		if agent_doc.llm:
			model = agent_doc.llm
		if agent_doc.llm_provider and frappe.db.exists("LLM Provider", agent_doc.llm_provider):
			provider_doc = frappe.get_doc("LLM Provider", agent_doc.llm_provider)
			api_key = provider_doc.get_password("api_key")

	if not api_key and frappe.db.exists("LLM Provider", "OpenRouter"):
		provider_doc = frappe.get_doc("LLM Provider", "OpenRouter")
		api_key = provider_doc.get_password("api_key")

	try:
		from litellm import image_generation

		response = image_generation(
			model=model,
			prompt=prompt,
			api_key=api_key,
		)

		data_list = response.get("data") if isinstance(response, dict) else getattr(response, "data", [])
		if not data_list:
			raise ValueError("No image data returned from image generation provider.")

		first_item = data_list[0] if isinstance(data_list, list) else data_list
		b64_data = first_item.get("b64_json") if isinstance(first_item, dict) else getattr(first_item, "b64_json", None)
		url_data = first_item.get("url") if isinstance(first_item, dict) else getattr(first_item, "url", None)

		content = None
		file_name = f"ai_email_img_{frappe.generate_hash(length=8)}.png"

		if b64_data:
			content = base64.b64decode(b64_data)
		elif url_data:
			import requests
			res = requests.get(url_data, timeout=30)
			if res.status_code == 200:
				content = res.content
			else:
				return url_data

		if content:
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": file_name,
					"content": content,
					"is_private": 0,
				}
			)
			file_doc.insert(ignore_permissions=True)
			return file_doc.file_url

		if url_data:
			return url_data

	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), f"AI Image Tool generation failed for prompt: {prompt}")
		return (
			f"IMAGE_GENERATION_FAILED: Could not generate image for prompt '{prompt}' due to: {exc}. "
			"Do NOT output a broken image block. Instead, design the email layout cleanly and professionally "
			"without this image using modern typography, card sections, borders, and clear spacing. "
			"Explain in your summary and change_notes that the image generation was unavailable but you crafted a professional layout."
		)
