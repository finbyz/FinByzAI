from .base import BaseVectorStore
from .registry import register_vector_store
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain.agents import Tool
from langchain_core.tools import create_retriever_tool
import frappe


@register_vector_store("qdrant")
class QdrantAdapter(BaseVectorStore):
    def __init__(self, kb_name: str, description: str, embeddings, api_key: str = None, url: str = None, **kwargs):
        super().__init__(kb_name, description, embeddings, api_key, **kwargs)
        # Read settings inside adapter
        try:
            s = frappe.get_single("Qdrant Settings")
            url = (s.url or url or "http://localhost:6333")
            api_key = s.get_password("api_key") or api_key
        except Exception:
            url = url or "http://localhost:6333"
        self.client = QdrantClient(url=url, api_key=api_key)
        self.vs = QdrantVectorStore(client=self.client, collection_name=kb_name, embedding=embeddings)

    def upsert(self, texts, metadatas, ids):
        self.vs.add_texts(texts=texts, metadatas=metadatas, ids=ids)
        return len(texts)

    def search(self, query, k=5):
        docs_and_scores = self.vs.similarity_search_with_score(query, k=k)
        return [
            {"id": d.metadata.get("id"), "score": score, "metadata": d.metadata}
            for d, score in docs_and_scores
        ]

    def delete(self, filter):
        """Delete all vectors matching the metadata filter using Qdrant's filter selector."""
        try:
            from qdrant_client.http import models as qdrant_models

            must_conditions = [
                qdrant_models.FieldCondition(
                    key=f"metadata.{key}", match=qdrant_models.MatchValue(value=value)
                )
                for key, value in filter.items()
            ]
            self.client.delete(
                collection_name=self.kb_name,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(must=must_conditions)
                ),
            )
        except Exception as e:
            frappe.log_error(f"QdrantAdapter delete error: {e}", "FinbyzAI KB")

    def as_tool(self):
        tool = create_retriever_tool(
            retriever=self.vs.as_retriever(),
            name=self.kb_name,
            description=self.description
        )

        return Tool(
            name=tool.name,
            func=lambda query: tool.run({"query": query}),
            description=self.description,
        )
