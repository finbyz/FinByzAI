# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from langchain.text_splitter import RecursiveCharacterTextSplitter
from finbyzai.ai.vectorstores.registry import create_vector_store
from finbyzai.ai.embeddings.registry import create_embedding
from finbyzai.ai.utils.knowledge_base_utils import extract_text_from_source


# ── Status constants ──────────────────────────────────────────────────────────
STATUS_QUEUE = "Queue"
STATUS_IN_PROGRESS = "In Progress"
STATUS_COMPLETED = "Completed"


class KnowledgeBase(Document):
    """
    KnowledgeBase DocType that integrates with different vector stores
    via the registry + factory pattern with intelligent text chunking.
    """

    # ── Naming ────────────────────────────────────────────────────────────────

    def autoname(self):
        self.name = frappe.scrub(self.title)

    # ── Hooks ─────────────────────────────────────────────────────────────────

    def on_update(self):
        """
        After a save: if there are unprocessed items AND we are not already
        running / already queued, set status → Queue and enqueue a job.
        """
        unprocessed = self._get_unprocessed_count()

        if unprocessed > 0 and self.status not in (STATUS_IN_PROGRESS, STATUS_QUEUE):
            frappe.db.set_value(
                "Knowledge Base",
                self.name,
                "status",
                STATUS_QUEUE,
                update_modified=False,
            )
            frappe.db.commit()
            frappe.enqueue(
                "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
                queue="long",
                kb_name=self.name,
                timeout=3600,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_unprocessed_count(self):
        """Count unprocessed items across all child tables."""
        links = sum(1 for l in (self.links or []) if not l.is_processed)
        docs = sum(1 for d in (self.documents or []) if not d.is_processed)
        notes = sum(1 for n in (self.notes or []) if not n.is_processed)
        return links + docs + notes

    def get_text_splitter(self):
        """Return a configured RecursiveCharacterTextSplitter."""
        return RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
            is_separator_regex=False,
        )

    def get_vector_store(self):
        """
        Build and return a vector-store instance.
        Raises frappe.ValidationError if the KB is mis-configured.
        """
        store_name = (self.vector_store or "").strip().lower()
        if not store_name:
            frappe.throw("No vector store configured for this Knowledge Base.")

        emb = _get_embeddings(self)
        if not emb:
            frappe.throw(
                "Embeddings not configured. "
                "Please set an embedding model on the Knowledge Base."
            )

        api_key = _get_provider_api_key(self)

        return create_vector_store(
            store_name=store_name,
            kb_name=self.name,
            description=self.description or "",
            embeddings=emb,
            api_key=api_key,
        )

    # ── Core processing ───────────────────────────────────────────────────────

    def process_items(self):
        """
        Process every unprocessed item in this Knowledge Base.

        State machine
        ─────────────
        Queue / Completed  →  In Progress  →  Completed   (all items ok)
                                           →  Queue        (some items failed)
                                           →  Queue        (fatal / misconfiguration)

        Per-item failures are logged individually and never abort the batch.
        """
        # Guard against concurrent runs
        if self.status == STATUS_IN_PROGRESS:
            frappe.throw("Knowledge Base is already being processed.")

        # Mark as running (direct DB write + commit avoids save() overhead)
        frappe.db.set_value(
            "Knowledge Base",
            self.name,
            "status",
            STATUS_IN_PROGRESS,
            update_modified=False,
        )
        frappe.db.commit()

        all_succeeded = True

        try:
            store = self.get_vector_store()  # may throw – caught below
            splitter = self.get_text_splitter()

            # ── Per-item processor ───────────────────────────────────────────
            def process_item(row, source, source_type, parent_table):
                """
                Extract → chunk → upsert → mark processed.
                Returns True on success; logs and returns False on any failure.
                """
                nonlocal all_succeeded

                # 1. Validate source is present
                if not source or not str(source).strip():
                    frappe.log_error(
                        f"KB '{self.name}': skipping {row.doctype} '{row.name}' — "
                        f"source field is empty.",
                        "FinbyzAI KB",
                    )
                    all_succeeded = False
                    return False

                try:
                    # 2. Extract text
                    result = extract_text_from_source(source, source_type)

                    if not result.get("success"):
                        frappe.log_error(
                            f"KB '{self.name}': extraction failed for "
                            f"[{source_type}] '{source}': {result.get('error')}",
                            "FinbyzAI KB",
                        )
                        all_succeeded = False
                        return False

                    content = (result.get("content") or "").strip()
                    if not content:
                        frappe.log_error(
                            f"KB '{self.name}': extracted content is empty for "
                            f"[{source_type}] '{source}'.",
                            "FinbyzAI KB",
                        )
                        all_succeeded = False
                        return False

                    # 3. Chunk
                    chunks = splitter.split_text(content)
                    if not chunks:
                        frappe.log_error(
                            f"KB '{self.name}': no chunks produced for "
                            f"[{source_type}] '{source}'.",
                            "FinbyzAI KB",
                        )
                        all_succeeded = False
                        return False

                    # 4. Build metadata & IDs
                    metadatas, ids = [], []
                    for i, chunk in enumerate(chunks):
                        meta = {
                            "kb": self.name,
                            "doc_type": source_type,
                            "chunk_index": i,
                        }
                        if source_type == "web_url":
                            meta["url"] = source
                        elif source_type == "file":
                            meta["file"] = source
                        else:  # note
                            meta["note_id"] = row.name

                        metadatas.append(meta)
                        ids.append(f"{self.name}_{parent_table}_{row.name}_{i}")

                    # 5. Upsert into vector store
                    store.upsert(texts=chunks, metadatas=metadatas, ids=ids)

                    # 6. Mark row as processed
                    frappe.db.set_value(row.doctype, row.name, "is_processed", 1)
                    frappe.db.commit()
                    return True

                except Exception:
                    frappe.log_error(
                        f"KB '{self.name}': unhandled error processing "
                        f"[{source_type}] '{source}'.\n{frappe.get_traceback()}",
                        "FinbyzAI KB",
                    )
                    all_succeeded = False
                    return False

            # ── Iterate all child tables ─────────────────────────────────────
            for link in self.links or []:
                if not link.is_processed:
                    process_item(link, link.url, "web_url", "links")

            for doc in self.documents or []:
                if not doc.is_processed:
                    process_item(doc, doc.file, "file", "documents")

            for note in self.notes or []:
                if not note.is_processed:
                    process_item(note, note.content, "note", "notes")

        except Exception:
            # Fatal error (e.g. bad vector-store config, network unreachable).
            # Reset to Queue so the scheduler will retry.
            frappe.log_error(
                f"KB '{self.name}': fatal error during processing.\n"
                f"{frappe.get_traceback()}",
                "FinbyzAI KB",
            )
            frappe.db.set_value("Knowledge Base", self.name, "status", STATUS_QUEUE)
            frappe.db.commit()
            raise  # propagate so the background-job wrapper can log it too

        # Set final status:
        #   all_succeeded=True  → Completed
        #   any failure          → Queue   (scheduler will retry unprocessed rows)
        final_status = STATUS_COMPLETED if all_succeeded else STATUS_QUEUE
        frappe.db.set_value("Knowledge Base", self.name, "status", final_status)
        frappe.db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_provider_api_key(kb: KnowledgeBase):
    """Return the plaintext API key for the KB's LLM provider, or None."""
    try:
        if not kb.provider:
            return None
        provider = frappe.get_doc("LLM Provider", kb.provider)
        return provider.get_password("api_key")
    except Exception:
        return None


def _get_embeddings(kb: KnowledgeBase):
    """
    Build and return an embeddings instance for this KB, or None.

    Validates that:
    - An embedding model is set on the KB.
    - The selected LLM has `is_embedding_model = 1`.
    """
    llm_name = getattr(kb, "embeding_model", None)
    if not llm_name:
        return None

    try:
        llm_doc = frappe.get_doc("LLM", llm_name)
    except Exception:
        frappe.log_error(
            f"KB '{kb.name}': embedding model '{llm_name}' not found in LLM doctype.",
            "FinbyzAI KB",
        )
        return None

    # Guard: don't try to use a generative/chat model as an embedding model
    if not getattr(llm_doc, "is_embedding_model", False):
        frappe.log_error(
            f"KB '{kb.name}': LLM '{llm_name}' is not an embedding model "
            f"(is_embedding_model=0). "
            f"Please select a proper embedding model (e.g. gemini-embedding-001).",
            "FinbyzAI KB",
        )
        frappe.throw(
            f"'{llm_name}' is not an embedding model. "
            f"Please select a model with 'Is Embedding Model' enabled on the Knowledge Base."
        )

    return create_embedding(
        llm_doc.provider or "",
        model=llm_doc.name,
        api_key=_get_provider_api_key(kb),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public API + background-job wrappers
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def process_knowledge_base(kb_name):
    """
    Whitelisted endpoint: enqueue a background job to process a Knowledge Base.
    """
    if not frappe.db.exists("Knowledge Base", kb_name):
        frappe.throw(f"Knowledge Base '{kb_name}' not found.")

    current_status = frappe.db.get_value("Knowledge Base", kb_name, "status")

    if current_status == STATUS_IN_PROGRESS:
        return {"status": "already_processing", "kb_name": kb_name}

    frappe.db.set_value("Knowledge Base", kb_name, "status", STATUS_QUEUE)
    frappe.db.commit()

    frappe.enqueue(
        "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
        queue="long",
        kb_name=kb_name,
        timeout=3600,
    )

    return {"status": "enqueued", "kb_name": kb_name}


def _run_process_items(kb_name):
    """
    Background-job entry point.

    process_items() manages its own status transitions and logs per-item errors.
    This wrapper only catches the re-raised fatal exception to log the job failure.
    """
    try:
        kb = frappe.get_doc("Knowledge Base", kb_name)
        kb.process_items()
    except Exception:
        # process_items already set status → Queue and logged the root cause.
        frappe.log_error(
            f"Background job failed for KB '{kb_name}'.\n{frappe.get_traceback()}",
            "FinbyzAI KB",
        )


def process_queued_knowledge_bases():
    """
    Scheduler job (runs hourly).

    Picks up KBs in Queue status that were not started by an on_update trigger
    (e.g. after a worker restart or a missed enqueue).
    Uses enqueue() rather than calling process_items() synchronously so the
    scheduler thread is never blocked.
    """
    kbs = frappe.get_all(
        "Knowledge Base",
        filters={"status": ["in", [STATUS_QUEUE, STATUS_IN_PROGRESS]]},
        fields=["name", "status"],
    )

    for kb_row in kbs:
        kb_name = kb_row["name"]
        try:
            kb = frappe.get_doc("Knowledge Base", kb_name)

            # Another worker is already running — leave it alone
            if kb.status == STATUS_IN_PROGRESS:
                continue

            # Nothing actually left to do — clean up stale Queue status
            if kb._get_unprocessed_count() == 0:
                frappe.db.set_value("Knowledge Base", kb_name, "status", STATUS_COMPLETED)
                frappe.db.commit()
                continue

            frappe.enqueue(
                "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
                queue="long",
                kb_name=kb_name,
                timeout=3600,
            )

        except Exception:
            frappe.log_error(
                f"Scheduler: error queuing KB '{kb_name}'.\n{frappe.get_traceback()}",
                "FinbyzAI KB",
            )


# ──────────────────────────────────────────────────────────────────────────────
# Sitemap helper
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def fetch_sitemap_urls(sitemap_url):
    """
    Fetch all page URLs from a sitemap XML.
    Recursively follows sub-sitemaps in sitemap index files.
    """
    import requests
    import xml.etree.ElementTree as ET

    visited = set()

    def _fetch(url):
        if not url or url in visited:
            return []
        visited.add(url)

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except Exception as e:
            frappe.log_error(f"Sitemap fetch error ({url}): {e}", "FinbyzAI KB")
            return []

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            frappe.log_error(f"Sitemap parse error ({url}): {e}", "FinbyzAI KB")
            return []

        ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
        namespace = {"ns": ns}
        page_urls = []

        # Sitemap index → recurse
        sub_sitemaps = root.findall("ns:sitemap", namespace) or [
            e for e in root.iter() if e.tag.endswith("}sitemap") or e.tag == "sitemap"
        ]
        for sitemap_elem in sub_sitemaps:
            loc = sitemap_elem.find("ns:loc", namespace) or next(
                (c for c in sitemap_elem if c.tag.endswith("loc")), None
            )
            if loc is not None and loc.text:
                page_urls.extend(_fetch(loc.text.strip()))

        # Regular sitemap → collect <url><loc>
        url_elems = (
            root.findall("ns:url", namespace)
            or root.findall(f"{{{ns}}}url")
            or [e for e in root.iter() if e.tag.endswith("}url") or e.tag == "url"]
        )
        for url_elem in url_elems:
            loc = (
                url_elem.find("ns:loc", namespace)
                or url_elem.find(f"{{{ns}}}loc")
                or next((c for c in url_elem if c.tag.endswith("loc")), None)
            )
            if loc is not None and loc.text:
                page_urls.append(loc.text.strip())

        # Last-resort fallback
        if not page_urls and not sub_sitemaps:
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text:
                    page_urls.append(elem.text.strip())

        return page_urls

    if not sitemap_url:
        return []

    return sorted(set(_fetch(sitemap_url)))
