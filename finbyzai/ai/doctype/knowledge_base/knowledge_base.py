# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import whitelist

from finbyzai.ai.vectorstores.registry import create_vector_store, available_vector_stores
from typing import List, Dict, Any


class KnowledgeBase(Document):
    """
    KnowledgeBase DocType that integrates with different vector stores
    via the registry + factory pattern.
    """
    def autoname(self):
        self.name = frappe.scrub(self.title)
        
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

    @whitelist()
    def upsert(self) -> int:
        """Upsert all documents in this KB into the configured vector store."""
        store = self.get_vector_store()
        if not store:
            raise frappe.ValidationError("No vector_store selected on Knowledge Base")

        items = []
        for row in self.documents or []:
            if row.is_process: continue
            if row.text_content:
                items.append({
                    "id": f"{self.name}-{row.name}",
                    "text": row.text_content,
                    "metadata": {
                        "kb": self.name,
                        "row_name": row.name,
                        "file": row.file,
                    },
                })
                row.is_process = True
        self.save()
        return store.upsert(
            texts=[it["text"] for it in items],
            metadatas=[it["metadata"] for it in items],
            ids=[it["id"] for it in items],
        )

    @whitelist()
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search this KB via vector store (if configured)."""
        store = self.get_vector_store()
        if not store:
            return self.simple_search(query, limit)

        return store.search(query, k=limit)

    def simple_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Fallback keyword search if no vector store configured."""
        results: List[Dict[str, Any]] = []
        if not query or not self.documents:
            return results

        needle = query.strip().lower()
        for row in self.documents:
            text = (row.text_content or "").lower()
            if needle in text:
                idx = text.find(needle)
                snippet = row.text_content[max(0, idx - 60): idx + len(needle) + 60]
                results.append({
                    "id": row.name,
                    "score": text.count(needle),
                    "snippet": snippet,
                })

        return sorted(results, key=lambda r: r["score"], reverse=True)[:limit]


# --- helpers (same as you already had) ---

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
