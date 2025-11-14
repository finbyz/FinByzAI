# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

from finbyzai.ai.utils.knowledge_base_utils import extract_text_from_source
import frappe
from frappe.model.document import Document
import os
from typing import List, Dict, Any

from langchain.text_splitter import RecursiveCharacterTextSplitter
from finbyzai.ai.vectorstores.registry import create_vector_store
from finbyzai.ai.embeddings.registry import create_embedding


class KnowledgeBase(Document):
    """
    KnowledgeBase DocType that integrates with different vector stores
    via the registry + factory pattern with intelligent text chunking.
    """
    def autoname(self):
        self.name = frappe.scrub(self.title)
    
    def get_text_splitter(self):
        """
        Get configured text splitter with optimal chunking parameters.
        
        Recommended chunk sizes:
        - OpenAI embeddings: 512-1000 tokens (typically 2000-4000 chars)
        - Cohere embeddings: 512 tokens (typically 2000 chars)
        - For general use: 1000 chars with 200 overlap works well
        """
        chunk_size = 1000
        chunk_overlap = 200
        
        # Use RecursiveCharacterTextSplitter as it's the recommended default
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
            is_separator_regex=False,
        )
        
    def get_vector_store(self):
        """Return a vector store instance based on this KB's config."""
        store_name = (self.vector_store or "").strip().lower()
        if not store_name:
            return None

        # get embeddings model (can be OpenAI, Google, etc.)
        emb = _get_embeddings(self)
        if not emb:
            raise frappe.ValidationError("Embeddings not configured")

        api_key = _get_provider_api_key(self)

        return create_vector_store(
            store_name=store_name,
            kb_name=self.name,
            description=self.description or "",
            embeddings=emb,
            api_key=api_key,
        )
        
    def on_update(self):
        store = self.get_vector_store()
        if not store:
            raise frappe.ValidationError("No vector_store selected on Knowledge Base")
        splitter = self.get_text_splitter()
        
        # Process documents (files)
        for document in self.documents:
            if document.is_processed: 
                continue
            
            # Extract text from file
            extraction_result = extract_text_from_source(document.file, 'file')
            if not extraction_result['success']:
                frappe.msgprint(f"Failed to extract text from {document.file}: {extraction_result['error']}")
                continue
            
            # Split text into chunks
            chunks = splitter.split_text(extraction_result['content'])
            
            # Prepare metadata and IDs for chunks
            metadatas = []
            ids = []
            for i, chunk in enumerate(chunks):
                metadatas.append({
                    "kb": self.name,
                    "file": document.file,
                    "chunk_index": i,
                    "doc_type": "file"
                })
                ids.append(f"{self.name}_{document.name}_{i}")
            
            # Upsert chunks to vector store
            store.upsert(
                texts=chunks,
                metadatas=metadatas,
                ids=ids
            )
            
            document.is_processed = 1
        # Process notes
        for note in self.notes:
            if note.is_processed: 
                continue
            
            # Split note content into chunks
            chunks = splitter.split_text(note.content)
            
            # Prepare metadata and IDs for chunks
            metadatas = []
            ids = []
            for i, chunk in enumerate(chunks):
                metadatas.append({
                    "kb": self.name,
                    "note_id": note.name,
                    "chunk_index": i,
                    "doc_type": "note"
                })
                ids.append(f"{self.name}_note_{note.name}_{i}")
            
            # Upsert chunks to vector store
            store.upsert(
                texts=chunks,
                metadatas=metadatas,
                ids=ids
            )
            
            note.is_processed = 1
        
        # Process web links
        for link in self.links:
            if link.is_processed: 
                continue
            
            # Extract text from web URL
            extraction_result = extract_text_from_source(link.url, 'web_url')
            if not extraction_result['success']:
                frappe.msgprint(f"Failed to extract text from {link.url}: {extraction_result['error']}")
                continue

            # Split text into chunks
            chunks = splitter.split_text(extraction_result['content'])
            
            # Prepare metadata and IDs for chunks
            metadatas = []
            ids = []
            for i, chunk in enumerate(chunks):
                metadatas.append({
                    "kb": self.name,
                    "url": link.url,
                    "chunk_index": i,
                    "doc_type": "web_link"
                })
                ids.append(f"{self.name}_link_{link.name}_{i}")
            
            # Upsert chunks to vector store
            store.upsert(
                texts=chunks,
                metadatas=metadatas,
                ids=ids
            )
            
            link.is_processed = 1
        
    
def _get_provider_api_key(kb: KnowledgeBase):
    try:
        if not kb.provider:
            return None
        provider = frappe.get_doc("LLM Provider", kb.provider)
        return provider.get_password("api_key")
    except Exception:
        return None


def _get_embeddings(kb: KnowledgeBase):
    # Resolve embeddings via the central embeddings registry
    from finbyzai.ai.embeddings.registry import create_embedding

    llm_name = getattr(kb, "embeding_model", None)
    if not llm_name:
        return None
    try:
        llm_doc = frappe.get_doc("LLM", llm_name)
    except Exception:
        return None

    provider_name = (llm_doc.provider or "")
    api_key = _get_provider_api_key(kb)
    model_name = llm_doc.name

    return create_embedding(provider_name, model=model_name, api_key=api_key)
