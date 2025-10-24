from langchain_openai import OpenAIEmbeddings
from .base import BaseEmbedding
from .registry import register_embedding

@register_embedding("OpenAI")
class OpenAIEmbedding(BaseEmbedding):
    def __init__(self, model: str = "text-embedding-3-small", api_key: str = None):
        self.embedding = OpenAIEmbeddings(model=model, openai_api_key=api_key)

    def embed_query(self, text: str):
        return self.embedding.embed_query(text)

    def embed_documents(self, texts):
        return self.embedding.embed_documents(texts)