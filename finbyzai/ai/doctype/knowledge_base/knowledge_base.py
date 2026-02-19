# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

from finbyzai.ai.utils.knowledge_base_utils import extract_text_from_source
import frappe
from frappe.model.document import Document
from typing import List, Dict, Any

from langchain.text_splitter import RecursiveCharacterTextSplitter
from finbyzai.ai.vectorstores.registry import create_vector_store
from finbyzai.ai.embeddings.registry import create_embedding


# Status constants
STATUS_QUEUE = "Queue"
STATUS_IN_PROGRESS = "In Progress"
STATUS_COMPLETED = "Completed"


class KnowledgeBase(Document):
    """
    KnowledgeBase DocType that integrates with different vector stores
    via the registry + factory pattern with intelligent text chunking.
    """
    def autoname(self):
        self.name = frappe.scrub(self.title)
    
    def on_update(self):            
        unprocessed_count = self._get_unprocessed_count()
        
        if unprocessed_count > 0 and self.status == STATUS_COMPLETED:
            # New items added, change to Queue
            frappe.db.set_value("Knowledge Base", self.name, "status", STATUS_QUEUE, update_modified=False)
    
    def _get_unprocessed_count(self):
        """Count unprocessed items across all child tables."""
        links = len([l for l in (self.links or []) if not l.is_processed])
        docs = len([d for d in (self.documents or []) if not d.is_processed])
        notes = len([n for n in (self.notes or []) if not n.is_processed])
        return links + docs + notes
    
    def get_text_splitter(self):
        """Get configured text splitter with optimal chunking parameters."""
        chunk_size = 1000
        chunk_overlap = 200
        
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
    
    def process_items(self):
        """Process all unprocessed items in this Knowledge Base."""
        if self.status == STATUS_IN_PROGRESS:
            frappe.throw("Knowledge Base is already being processed.")
        
        # Set status to In Progress
        self.status = STATUS_IN_PROGRESS
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
            
            # Set status to Completed
            frappe.db.set_value("Knowledge Base", self.name, "status", STATUS_COMPLETED)
            frappe.db.commit()
                    
        except Exception as e:
            # On error, set back to Queue so scheduler can retry
            frappe.db.set_value("Knowledge Base", self.name, "status", STATUS_QUEUE)
            frappe.db.commit()
            raise


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
    
    if kb.status == STATUS_IN_PROGRESS:
        frappe.msgprint("Knowledge Base is already being processed.")
        return {"status": "already_processing"}
    
    # Set to Queue first
    frappe.db.set_value("Knowledge Base", kb_name, "status", STATUS_QUEUE)
    frappe.db.commit()
    
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
        frappe.db.set_value("Knowledge Base", kb_name, "status", STATUS_QUEUE)
        frappe.db.commit()
        frappe.log_error(f"KB Processing Error ({kb_name}): {str(e)}\n{frappe.get_traceback()}", "FinbyzAI")


def process_queued_knowledge_bases():
    """
    Scheduler job: Process all Knowledge Bases in Queue or In Progress status.
    Runs hourly to pick up any missed or failed processing.
    """
    kbs = frappe.get_all(
        "Knowledge Base",
        filters={"status": ["in", [STATUS_QUEUE, STATUS_IN_PROGRESS]]},
        pluck="name"
    )
    
    for kb_name in kbs:
        try:
            kb = frappe.get_doc("Knowledge Base", kb_name)
            
            # Skip if already In Progress (another job might be running)
            if kb.status == STATUS_IN_PROGRESS:
                continue
                
            # Check if there are actually unprocessed items
            if kb._get_unprocessed_count() == 0:
                frappe.db.set_value("Knowledge Base", kb_name, "status", STATUS_COMPLETED)
                frappe.db.commit()
                continue
            
            # Process the KB
            kb.process_items()
            
        except Exception as e:
            frappe.log_error(f"Scheduler KB Processing Error ({kb_name}): {str(e)}", "FinbyzAI")

@frappe.whitelist()
def fetch_sitemap_urls(sitemap_url):
    """
    Fetch URLs from a sitemap XML.
    Recursively follows sub-sitemaps in sitemap index files.
    """
    import requests
    import xml.etree.ElementTree as ET

    visited = set()

    def _fetch(url):
        """Recursively collect page URLs from a sitemap or sitemap index."""
        if not url or url in visited:
            return []
        visited.add(url)

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except Exception as e:
            frappe.log_error(f"Sitemap Fetch Error ({url}): {str(e)}", "FinbyzAI")
            return []

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            frappe.log_error(f"Sitemap Parse Error ({url}): {str(e)}", "FinbyzAI")
            return []

        ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
        namespace = {"ns": ns}
        page_urls = []

        # ── Sitemap Index: recurse into each child sitemap ──────────────────
        sub_sitemaps = root.findall("ns:sitemap", namespace)
        if not sub_sitemaps:
            # Fallback: tag-based search ignoring namespace prefix
            sub_sitemaps = [
                e
                for e in root.iter()
                if e.tag.endswith("}sitemap") or e.tag == "sitemap"
            ]

        for sitemap_elem in sub_sitemaps:
            loc = sitemap_elem.find("ns:loc", namespace)
            if loc is None:
                # Fallback: any child <loc>
                loc = next((c for c in sitemap_elem if c.tag.endswith("loc")), None)
            if loc is not None and loc.text:
                page_urls.extend(_fetch(loc.text.strip()))

        # ── Regular sitemap: collect <url><loc> entries ──────────────────────
        url_elems = root.findall("ns:url", namespace)
        if not url_elems:
            url_elems = root.findall(f"{{{ns}}}url")
        if not url_elems:
            url_elems = [
                e for e in root.iter() if e.tag.endswith("}url") or e.tag == "url"
            ]

        for url_elem in url_elems:
            loc = url_elem.find("ns:loc", namespace)
            if loc is None:
                loc = url_elem.find(f"{{{ns}}}loc")
            if loc is None:
                loc = next((c for c in url_elem if c.tag.endswith("loc")), None)
            if loc is not None and loc.text:
                page_urls.append(loc.text.strip())

        # ── Final fallback: grab every <loc> in this document ────────────────
        if not page_urls and not sub_sitemaps:
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text:
                    page_urls.append(elem.text.strip())

        return page_urls

    if not sitemap_url:
        return []

    all_urls = _fetch(sitemap_url)
    return sorted(list(set(all_urls)))
