from .base import BaseVectorStore
from .registry import register_vector_store
from langchain_pinecone import PineconeVectorStore
from typing import List, Dict
from langchain_core.tools import create_retriever_tool
from langchain.agents import Tool
from pinecone import Pinecone, ServerlessSpec
import frappe
import os
import re
from langchain.tools import tool


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
        
        # Sanitize index name (Pinecone requirements: lowercase, alphanumeric, hyphens)
        index_name = self._sanitize_index_name(kb_name)
        
        # Initialize Pinecone and ensure index exists
        self._ensure_index_exists(index_name, embeddings)
        
        # Initialize the vector store
        self.vs = PineconeVectorStore(index_name=index_name, embedding=embeddings)

    def _sanitize_index_name(self, name: str) -> str:
        """Convert name to valid Pinecone index name format."""
        # Convert to lowercase and replace invalid characters with hyphens
        name = name.lower()
        name = re.sub(r'[^a-z0-9-]', '-', name)
        # Remove consecutive hyphens
        name = re.sub(r'-+', '-', name)
        # Remove leading/trailing hyphens
        name = name.strip('-')
        # Ensure it's not empty
        if not name:
            name = "default-index"
        return name

    def _ensure_index_exists(self, index_name: str, embeddings):
        """Check if index exists, create if it doesn't."""
        pc = Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
        
        # Get list of existing indexes
        existing_indexes = [index.name for index in pc.list_indexes()]
        
        if index_name not in existing_indexes:
            # Get embedding dimension
            # Create a sample embedding to determine dimension
            sample_text = "sample"
            sample_embedding = embeddings.embed_query(sample_text)
            dimension = len(sample_embedding)
            
            # Create the index
            pc.create_index(
                name=index_name,
                dimension=dimension,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud="aws",  # or "gcp", "azure"
                    region="us-east-1"  # adjust to your preferred region
                )
            )
            frappe.log_error(f"Created Pinecone index: {index_name} with dimension {dimension}")

    def upsert(self, texts, metadatas: List[Dict], ids: List[str]):
        """Upsert texts into the vector store."""
        self.vs.add_texts(texts=texts, metadatas=metadatas, ids=ids)
        return len(texts)

    def search(self, query, k=5):
        """Search for similar documents."""
        docs_and_scores = self.vs.similarity_search_with_score(query, k=k)
        return [
            {"id": d.metadata.get("id"), "score": score, "metadata": d.metadata}
            for d, score in docs_and_scores
        ]
    
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
