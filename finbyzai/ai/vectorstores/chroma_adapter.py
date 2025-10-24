from .base import BaseVectorStore
from .registry import register_vector_store
from langchain_core.tools import create_retriever_tool
from langchain_chroma import Chroma
from langchain.agents import Tool
import frappe
from pathlib import Path



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

    def as_tool(self):
        tool = create_retriever_tool(
            retriever=self.vs.as_retriever(),
            name=self.kb_name,
            description=self.description
        )
        
        return Tool(
            name=tool.name,
            func=lambda query: tool.run({"query": query}),
            description=self.description
        )