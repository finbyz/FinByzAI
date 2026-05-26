from .base import BaseVectorStore
from .registry import register_vector_store
from langchain_core.tools import create_retriever_tool
from langchain_chroma import Chroma
from langchain_core.tools import Tool
import frappe
from pathlib import Path
from langchain_core.tools import tool


@register_vector_store("chroma")
@register_vector_store("chromadb")
class ChromaAdapter(BaseVectorStore):
    def __init__(self, kb_name: str,description:str, embeddings, api_key: str = None, persist_directory: str = None, **kwargs):
        super().__init__(kb_name, description, embeddings, api_key, **kwargs)

        site_path = Path(frappe.get_site_path())
        private_folder = site_path / "private"
        pd = persist_directory or (private_folder / "chroma_db")
        self.vs = Chroma(collection_name=kb_name, embedding_function=embeddings, persist_directory=pd)

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
        """Delete all vectors matching the metadata filter using Chroma's where clause."""
        try:
            self.vs._collection.delete(where=filter)
        except Exception as e:
            import frappe

            frappe.log_error(f"ChromaAdapter delete error: {e}", "FinbyzAI KB")

    def as_tool(self):
        @tool(description=self.description)
        def kb_search(query: str):
            docs = self.vs.similarity_search(query, k=5)

            return [
                {
                    "id": d.metadata.get("id"),
                    "metadata": d.metadata,
                    "content": d.page_content
                }
                for d in docs
            ]
        return kb_search
