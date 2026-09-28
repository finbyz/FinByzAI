from langchain_openai import OpenAIEmbeddings

from .base import BaseEmbedding
from .registry import register_embedding


OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


@register_embedding("OpenRouter")
class OpenRouterEmbedding(BaseEmbedding):
    """OpenRouter's OpenAI-compatible embedding adapter."""

    def __init__(self, model: str, api_key: str | None = None):
        model_name = model.removeprefix("openrouter/")
        self.embedding = OpenAIEmbeddings(
            model=model_name,
            api_key=api_key,
            base_url=OPENROUTER_API_BASE,
        )

    def embed_query(self, text: str):
        return self.embedding.embed_query(text)

    def embed_documents(self, texts):
        return self.embedding.embed_documents(texts)
