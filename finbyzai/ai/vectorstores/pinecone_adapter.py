from .base import BaseVectorStore
from .registry import register_vector_store
from langchain_pinecone import PineconeVectorStore
from typing import List,Dict
from langchain_core.tools import create_retriever_tool
from langchain.agents import Tool
import frappe
import os



@register_vector_store("pinecone")
class PineconeAdapter(BaseVectorStore):
    def __init__(self, kb_name: str, description: str, embeddings, api_key: str = None, **kwargs):
        super().__init__(kb_name, description, embeddings, api_key, **kwargs)
        # Read settings inside adapter
        try:
            s = frappe.get_single("Pinecone Settings")
            pinecone_api_key = s.get_password("api_key")
            if pinecone_api_key:
                os.environ["PINECONE_API_KEY"] = pinecone_api_key
        except Exception:
            pass
        self.vs = PineconeVectorStore(index_name=kb_name, embedding=embeddings)

    def upsert(self, texts, metadatas:List[Dict], ids:List[str]):
        # texts: List[str], metadatas: List[Dict], ids: List[str]
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