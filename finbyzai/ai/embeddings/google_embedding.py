from langchain_google_genai import GoogleGenerativeAIEmbeddings
from .base import BaseEmbedding
from .registry import register_embedding

@register_embedding("Google")
@register_embedding("Google Generative AI")
class GoogleEmbedding(BaseEmbedding):
    def __init__(self, model: str = "models/embedding-001", api_key: str = None):
        self.embedding = GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key)

    def embed_query(self, text: str):
        return self.embedding.embed_query(text)

    def embed_documents(self, texts):
        return self.embedding.embed_documents(texts)
