from .base import BaseVectorStore
from .registry import register_vector_store
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from langchain.agents import Tool
from langchain_core.tools import create_retriever_tool
from urllib.parse import urlparse
import uuid

import frappe


def _make_qdrant_client(url: str, api_key: str | None = None) -> QdrantClient:
    """Create a QdrantClient from a URL forcing REST-only (no gRPC).

    QdrantClient(url=...) defaults to prefer_grpc=True, which hangs
    when the server only serves the REST API.  Parsing the URL into
    host / port / scheme and passing prefer_grpc=False avoids the
    gRPC attempt entirely.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 6333)
    use_https = parsed.scheme == "https"
    return QdrantClient(
        host=host,
        port=port,
        https=use_https,
        api_key=api_key,
        prefer_grpc=False,
    )


def _str_ids_to_uuid(ids: list[str]) -> list[str]:
    """Convert string IDs to deterministic UUIDs (Qdrant v1.18+ requires UUID or uint)."""
    return [str(uuid.uuid5(uuid.NAMESPACE_DNS, id_)) for id_ in ids]


def _ensure_collection(client: QdrantClient, collection_name: str, vector_size: int = 768) -> None:
    """Create the Qdrant collection if it doesn't already exist."""
    try:
        client.get_collection(collection_name)
    except Exception:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=vector_size,
                distance=qdrant_models.Distance.COSINE,
            ),
        )


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
        self.client = _make_qdrant_client(url, api_key)
        _ensure_collection(self.client, kb_name)
        self.vs = QdrantVectorStore(
            client=self.client,
            collection_name=kb_name,
            embedding=embeddings,
            validate_collection_config=False,
        )

    def upsert(self, texts, metadatas, ids):
        uuids = _str_ids_to_uuid(ids)
        self.vs.add_texts(texts=texts, metadatas=metadatas, ids=uuids)
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
