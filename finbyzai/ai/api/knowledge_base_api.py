# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Permission-aware endpoints for Knowledge Base management."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from finbyzai.ai.doctype.knowledge_base.knowledge_base import make_source_id

READ = "read"
WRITE = "write"


def _get_knowledge_base(name_or_title: str, permission_type: str = READ):
    """Resolve a record visible to the caller and enforce row permission."""
    value = (name_or_title or "").strip()
    if not value:
        frappe.throw(_("Knowledge Base is required."))

    filters_to_try = ({"name": value}, {"title": value}, {"name": frappe.scrub(value)})
    for filters in filters_to_try:
        names = frappe.get_list("Knowledge Base", filters=filters, pluck="name", limit=1)
        if not names:
            continue
        doc = frappe.get_doc("Knowledge Base", names[0])
        doc.check_permission(permission_type)
        return doc

    # Do not disclose records hidden by user permissions.
    frappe.throw(_("Knowledge Base {0} was not found.").format(frappe.bold(value)))


def get_kb_name(name_or_title: str) -> str:
    """Compatibility helper for callers that still need only the record name."""
    return _get_knowledge_base(name_or_title).name


def _serialize(kb, *, include_documents: bool = False) -> dict[str, Any]:
    data = {
        "name": kb.name,
        "title": kb.title,
        "provider": kb.provider,
        "embedding_model": kb.embedding_model,
        "vector_store": kb.vector_store,
        "description": kb.description,
        "chunk_size": kb.chunk_size,
        "chunk_overlap": kb.chunk_overlap,
    }
    if include_documents:
        data["documents"] = [
            {
                "name": row.name,
                "file": row.file or "",
                "text_content": row.text_content or "",
                "is_processed": row.is_processed,
            }
            for row in kb.documents
        ]
    return data


@frappe.whitelist(methods=["POST"])
def create_knowledge_base(
    title: str,
    provider: str,
    embedding_model: str | None = None,
    vector_store: str | None = None,
    description: str = "",
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    embeding_model: str | None = None,
) -> dict[str, Any]:
    """Create a Knowledge Base; the misspelled argument remains as a transition alias."""
    frappe.has_permission("Knowledge Base", "create", throw=True)
    embedding_model = embedding_model or embeding_model
    if not embedding_model:
        frappe.throw(_("Embedding model is required."))

    kb = frappe.get_doc(
        {
            "doctype": "Knowledge Base",
            "title": title,
            "provider": provider,
            "embedding_model": embedding_model,
            "vector_store": vector_store,
            "description": description,
            "chunk_size": cint(chunk_size),
            "chunk_overlap": cint(chunk_overlap),
        }
    ).insert()
    return {
        "success": True,
        "message": _("Knowledge Base created successfully"),
        "data": _serialize(kb),
    }


@frappe.whitelist(methods=["GET"])
def get_knowledge_base(name: str) -> dict[str, Any]:
    return {"success": True, "data": _serialize(_get_knowledge_base(name), include_documents=True)}


@frappe.whitelist(methods=["GET"])
def list_knowledge_bases(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    page = max(cint(page), 1)
    page_size = min(max(cint(page_size), 1), 100)
    rows = frappe.get_list(
        "Knowledge Base",
        fields=[
            "name",
            "title",
            "provider",
            "embedding_model",
            "vector_store",
            "description",
            "chunk_size",
            "chunk_overlap",
            "creation",
            "modified",
        ],
        start=(page - 1) * page_size,
        page_length=page_size,
        order_by="modified desc",
    )
    total_count = len(frappe.get_list("Knowledge Base", pluck="name", limit_page_length=0))
    for row in rows:
        row["document_count"] = frappe.db.count("Knowledge Document", {"parent": row.name})
        row["processed_count"] = frappe.db.count(
            "Knowledge Document", {"parent": row.name, "is_processed": 1}
        )
    return {
        "success": True,
        "data": rows,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size,
        },
    }


@frappe.whitelist(methods=["POST"])
def update_knowledge_base(name: str, **values) -> dict[str, Any]:
    kb = _get_knowledge_base(name, WRITE)
    if "embeding_model" in values and "embedding_model" not in values:
        values["embedding_model"] = values.pop("embeding_model")
    allowed_fields = {
        "title",
        "provider",
        "embedding_model",
        "vector_store",
        "description",
        "chunk_size",
        "chunk_overlap",
    }
    for fieldname in allowed_fields & values.keys():
        kb.set(fieldname, values[fieldname])
    kb.save()
    return {
        "success": True,
        "message": _("Knowledge Base updated successfully"),
        "data": _serialize(kb),
    }


@frappe.whitelist(methods=["POST"])
def delete_knowledge_base(name: str) -> dict[str, Any]:
    kb = _get_knowledge_base(name, "delete")
    frappe.delete_doc("Knowledge Base", kb.name)
    return {"success": True, "message": _("Knowledge Base deleted successfully")}


@frappe.whitelist(methods=["POST"])
def upload_file_to_knowledge_base(kb_name: str, text_content: str = "") -> dict[str, Any]:
    from frappe.utils.file_manager import save_file

    kb = _get_knowledge_base(kb_name, WRITE)
    upload = frappe.request.files.get("file")
    if not upload:
        frappe.throw(_("No file uploaded."))
    file_doc = save_file(
        fname=upload.filename,
        content=upload.read(),
        dt=kb.doctype,
        dn=kb.name,
        is_private=1,
    )
    kb.append(
        "documents",
        {"file": file_doc.file_url, "text_content": text_content, "is_processed": False},
    )
    kb.save()
    return {
        "success": True,
        "message": _("File uploaded successfully"),
        "data": {
            "file_url": file_doc.file_url,
            "file_name": file_doc.file_name,
            "text_content": text_content,
        },
    }


@frappe.whitelist(methods=["POST"])
def add_text_document(kb_name: str, text_content: str) -> dict[str, Any]:
    kb = _get_knowledge_base(kb_name, WRITE)
    if not (text_content or "").strip():
        frappe.throw(_("Text content is required."))
    kb.append("documents", {"text_content": text_content, "is_processed": False})
    kb.save()
    return {
        "success": True,
        "message": _("Text document added successfully"),
        "data": {"text_content": text_content},
    }


@frappe.whitelist(methods=["POST"])
def delete_document(
    kb_name: str, row_name: str, delete_from_vector_store: bool = True
) -> dict[str, Any]:
    kb = _get_knowledge_base(kb_name, WRITE)
    row = next((item for item in kb.documents if item.name == row_name), None)
    if not row:
        frappe.throw(_("Document {0} was not found in this Knowledge Base.").format(row_name))

    file_name = row.file.rsplit("/", 1)[-1] if row.file else _("Text Document")
    source_id = (
        make_source_id(kb.name, "documents", row.name)
        if cint(delete_from_vector_store)
        else None
    )
    file_doc_name = frappe.db.get_value("File", {"file_url": row.file}, "name") if row.file else None
    kb.remove(row)
    kb.save()
    if file_doc_name or source_id:
        frappe.enqueue(
            "finbyzai.ai.api.knowledge_base_api.cleanup_detached_assets",
            enqueue_after_commit=True,
            kb_name=kb.name,
            source_id=source_id,
            file_name=file_doc_name,
        )
    return {
        "success": True,
        "message": _("Document deleted successfully"),
        "data": {
            "row_name": row_name,
            "file_name": file_name,
            "file_deletion_queued": bool(file_doc_name),
            "chunks_deleted_from_vector_store": 0,
            "vector_cleanup_queued": bool(source_id),
            "remaining_documents": len(kb.documents),
        },
    }


def cleanup_detached_assets(
    kb_name: str, source_id: str | None = None, file_name: str | None = None
) -> None:
    """Remove external assets only after the parent transaction commits."""
    if source_id:
        try:
            store = frappe.get_doc("Knowledge Base", kb_name).get_vector_store()
            if store and hasattr(store, "delete"):
                store.delete({"source_id": source_id})
        except Exception:
            frappe.log_error("Could not delete Knowledge Base vector chunks", frappe.get_traceback())
    if file_name and frappe.db.exists("File", file_name):
        frappe.delete_doc("File", file_name, ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def extract_pdfs(kb_name: str) -> dict[str, Any]:
    result = _get_knowledge_base(kb_name, WRITE).extract_all_files()
    return {
        "success": True,
        "message": _("Extracted {0} files with {1} errors").format(
            result["extracted"], result["errors"]
        ),
        "data": result,
    }


@frappe.whitelist(methods=["POST"])
def upsert_to_vector_store(
    kb_name: str, chunk_size: int | None = None, overlap: int | None = None
) -> dict[str, Any]:
    kb = _get_knowledge_base(kb_name, WRITE)
    chunk_size = cint(chunk_size) if chunk_size is not None else kb.chunk_size or 1000
    overlap = cint(overlap) if overlap is not None else kb.chunk_overlap or 200
    count = kb.upsert(chunk_size=chunk_size, overlap=overlap)
    return {
        "success": True,
        "message": _("Successfully indexed {0} chunks").format(count),
        "data": {"num_chunks": count},
    }


@frappe.whitelist(methods=["GET"])
def search_knowledge_base(kb_name: str, query: str, limit: int = 5) -> dict[str, Any]:
    kb = _get_knowledge_base(kb_name)
    query = (query or "").strip()
    if not query:
        frappe.throw(_("Search query is required."))
    raw_results = kb.get_vector_store().search(query=query, k=min(max(cint(limit), 1), 20))
    results = [_search_result(item, query) for item in raw_results]
    return {
        "success": True,
        "data": {
            "query": query,
            "kb_name": kb.name,
            "total_results": len(results),
            "results": results,
        },
    }


def _search_result(result: dict[str, Any], query: str) -> dict[str, Any]:
    metadata = result.get("metadata") or {}
    content = result.get("content") or ""
    file_path = metadata.get("file") or metadata.get("url") or metadata.get("note_id") or ""
    lower_content = content.lower()
    position = next(
        (lower_content.find(word) for word in query.lower().split() if word in lower_content),
        -1,
    )
    if position >= 0:
        start, end = max(0, position - 150), min(len(content), position + 350)
        snippet = ("..." if start else "") + content[start:end]
        snippet += "..." if end < len(content) else ""
    else:
        snippet = content[:500] + ("..." if len(content) > 500 else "")
    score = float(result.get("score", 1.0))
    return {
        "row_name": metadata.get("row_name"),
        "score": round(score, 4),
        "relevance": "High" if score < 0.8 else "Medium" if score < 1.2 else "Low",
        "snippet": snippet.strip(),
        "source": str(file_path).rsplit("/", 1)[-1] if file_path else _("Knowledge source"),
        "has_file": bool(file_path),
        "content_length": len(content),
        "metadata": metadata,
    }


@frappe.whitelist(methods=["POST"])
def reprocess_documents(kb_name: str) -> dict[str, Any]:
    _get_knowledge_base(kb_name, WRITE).reprocess_all_documents()
    return {"success": True, "message": _("All documents marked for reprocessing")}


@frappe.whitelist(methods=["GET"])
def get_providers_and_models() -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "providers": frappe.get_list("LLM Provider", fields=["name"], order_by="name"),
            "models": frappe.get_list("LLM", fields=["name", "provider"], order_by="name"),
            "vector_stores": ["Pinecone", "Qdrant", "Supabase", "ChromaDB"],
        },
    }
