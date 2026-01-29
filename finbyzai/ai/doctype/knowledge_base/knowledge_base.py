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
        """No synchronous processing - use process_items() method via API."""
        pass
    
    def process_items(self):
        """Process all unprocessed items in this Knowledge Base."""
        if self.is_processing:
            frappe.throw("Knowledge Base is already being processed.")
        
        # Lock the KB
        self.is_processing = 1
        self.save(ignore_permissions=True)
        frappe.db.commit()
        
        try:
            store = self.get_vector_store()
            if not store:
                frappe.throw("No vector store configured for this Knowledge Base.")
                
            splitter = self.get_text_splitter()

            def process_item(row, source, source_type, parent_table):
                """Process single item."""
                try:
                    result = extract_text_from_source(source, source_type)
                    if not result.get('success'):
                        return False
                    
                    chunks = splitter.split_text(result['content'])
                    metadatas = []
                    ids = []
                    for i, chunk in enumerate(chunks):
                        meta = {"kb": self.name, "doc_type": source_type, "chunk_index": i}
                        if source_type == 'web_url':
                            meta["url"] = source
                        elif source_type == 'file':
                            meta["file"] = source
                        else:
                            meta["note_id"] = row.name
                        metadatas.append(meta)
                        ids.append(f"{self.name}_{parent_table}_{row.name}_{i}")
                    
                    store.upsert(texts=chunks, metadatas=metadatas, ids=ids)
                    
                    frappe.db.set_value(row.doctype, row.name, "is_processed", 1)
                    frappe.db.commit()
                    return True
                    
                except Exception as e:
                    frappe.log_error(f"Item processing error ({self.name}): {str(e)}", "FinbyzAI")
                    return False

            # Process all unprocessed items
            for link in self.links:
                if not link.is_processed:
                    process_item(link, link.url, 'web_url', 'links')

            for doc in self.documents:
                if not doc.is_processed:
                    process_item(doc, doc.file, 'file', 'documents')

            for note in self.notes:
                if not note.is_processed:
                    process_item(note, note.content, 'note', 'notes')
                    
        finally:
            # Always unlock the KB
            frappe.db.set_value("Knowledge Base", self.name, "is_processing", 0)
            frappe.db.commit()


def _get_provider_api_key(kb: KnowledgeBase):
    try:
        if not kb.provider:
            return None
        provider = frappe.get_doc("LLM Provider", kb.provider)
        return provider.get_password("api_key")
    except Exception:
        return None


def _get_embeddings(kb: KnowledgeBase):
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


@frappe.whitelist()
def process_knowledge_base(kb_name):
    """Enqueue background job to process Knowledge Base items."""
    if not frappe.db.exists("Knowledge Base", kb_name):
        frappe.throw(f"Knowledge Base '{kb_name}' not found")
    
    kb = frappe.get_doc("Knowledge Base", kb_name)
    
    if kb.is_processing:
        frappe.msgprint("Knowledge Base is already being processed.")
        return {"status": "already_processing"}
    
    frappe.enqueue(
        "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
        queue="long",
        kb_name=kb_name,
        timeout=3600
    )
    
    return {"status": "enqueued", "kb_name": kb_name}


def _run_process_items(kb_name):
    """Background job wrapper to call process_items on KB."""
    try:
        kb = frappe.get_doc("Knowledge Base", kb_name)
        kb.process_items()
    except Exception as e:
        frappe.db.set_value("Knowledge Base", kb_name, "is_processing", 0)
        frappe.db.commit()
        frappe.log_error(f"KB Processing Error ({kb_name}): {str(e)}\n{frappe.get_traceback()}", "FinbyzAI")

