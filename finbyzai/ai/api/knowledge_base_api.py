# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

from finbyzai.ai.doctype.knowledge_base.knowledge_base import get_absolute_file_path
import frappe
from frappe import _
import json
import os
from typing import Dict, List, Any


def get_kb_name(name_or_title: str) -> str:
	"""
	Helper function to get actual KB name from either name or title
	
	Args:
		name_or_title: Knowledge Base name or title
		
	Returns:
		Actual Knowledge Base name
	"""
	# Check if exists directly
	if frappe.db.exists("Knowledge Base", name_or_title):
		return name_or_title
	
	# Try to find by title
	kb_list = frappe.get_all("Knowledge Base", filters={"title": name_or_title}, pluck="name")
	if kb_list:
		return kb_list[0]
	
	# Try scrubbed version
	scrubbed = frappe.scrub(name_or_title)
	if frappe.db.exists("Knowledge Base", scrubbed):
		return scrubbed
	
	# Not found
	available = frappe.get_all("Knowledge Base", pluck="name", limit=10)
	frappe.throw(
		_("Knowledge Base '{0}' not found. Available: {1}").format(
			name_or_title, 
			", ".join(available) if available else "None"
		)
	)


@frappe.whitelist(allow_guest=False)
def create_knowledge_base(title: str, provider: str, embeding_model: str, 
                          vector_store: str, description: str = "", 
                          chunk_size: int = 1000, chunk_overlap: int = 200) -> Dict[str, Any]:
	"""
	Create a new Knowledge Base
	
	Args:
		title: Knowledge Base title
		provider: LLM Provider name
		embeding_model: Embedding model name
		vector_store: Vector store type (Pinecone/Qdrant/Supabase/ChromaDB)
		description: Optional description
		chunk_size: Chunk size for text splitting (default: 1000)
		chunk_overlap: Overlap between chunks (default: 200)
	
	Returns:
		Created Knowledge Base document
	"""
	try:
		# Check permissions
		if not frappe.has_permission("Knowledge Base", "create"):
			frappe.throw(_("Not permitted to create Knowledge Base"), frappe.PermissionError)
		
		# Create Knowledge Base
		kb = frappe.get_doc({
			"doctype": "Knowledge Base",
			"title": title,
			"provider": provider,
			"embeding_model": embeding_model,
			"vector_store": vector_store,
			"description": description,
			"chunk_size": chunk_size,
			"chunk_overlap": chunk_overlap
		})
		kb.insert()
		frappe.db.commit()
		
		return {
			"success": True,
			"message": "Knowledge Base created successfully",
			"data": {
				"name": kb.name,
				"title": kb.title,
				"provider": kb.provider,
				"embeding_model": kb.embeding_model,
				"vector_store": kb.vector_store,
				"description": kb.description,
				"chunk_size": kb.chunk_size,
				"chunk_overlap": kb.chunk_overlap
			}
		}
	except Exception as e:
		frappe.log_error(f"Error creating Knowledge Base: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def get_knowledge_base(name: str) -> Dict[str, Any]:
	"""
	Get Knowledge Base details with all documents
	
	Args:
		name: Knowledge Base name or title
	
	Returns:
		Knowledge Base document with documents
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "read"):
			frappe.throw(_("Not permitted to read Knowledge Base"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(name)
		
		kb = frappe.get_doc("Knowledge Base", kb_name)
		
		return {
			"success": True,
			"data": {
				"name": kb.name,
				"title": kb.title,
				"provider": kb.provider,
				"embeding_model": kb.embeding_model,
				"vector_store": kb.vector_store,
				"description": kb.description,
				"chunk_size": kb.chunk_size,
				"chunk_overlap": kb.chunk_overlap,
				"documents": [
					{
						"name": doc.name,
						"file": doc.file or "",
						"text_content": doc.text_content or "",
						"is_process": doc.is_process,
					}
					for doc in kb.documents
				]
			}
		}
	except Exception as e:
		frappe.log_error(f"Error fetching Knowledge Base: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist()
def list_knowledge_bases(page: int = 1, page_size: int = 20) -> Dict[str, Any]:
	"""
	List all Knowledge Bases with pagination
	
	Args:
		page: Page number (default: 1)
		page_size: Items per page (default: 20)
	
	Returns:
		List of Knowledge Bases
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "read"):
			frappe.throw(_("Not permitted to read Knowledge Base"), frappe.PermissionError)
		
		start = (int(page) - 1) * int(page_size)
		
		kb_list = frappe.get_all(
			"Knowledge Base",
			fields=["name", "title", "provider", "embeding_model", "vector_store", 
			        "description", "chunk_size", "chunk_overlap", "creation", "modified"],
			start=start,
			page_length=page_size,
			order_by="modified desc"
		)
		
		# Get document counts for each KB
		for kb in kb_list:
			kb["document_count"] = frappe.db.count("Knowledge Document", 
			                                        filters={"parent": kb["name"]})
			kb["processed_count"] = frappe.db.count("Knowledge Document", 
			                                         filters={"parent": kb["name"], "is_process": 1})
		
		total_count = frappe.db.count("Knowledge Base")
		
		return {
			"success": True,
			"data": kb_list,
			"pagination": {
				"page": int(page),
				"page_size": int(page_size),
				"total_count": total_count,
				"total_pages": (total_count + int(page_size) - 1) // int(page_size)
			}
		}
	except Exception as e:
		frappe.log_error(f"Error listing Knowledge Bases: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def update_knowledge_base(name: str, **kwargs) -> Dict[str, Any]:
	"""
	Update Knowledge Base fields
	
	Args:
		name: Knowledge Base name
		**kwargs: Fields to update (title, provider, embeding_model, vector_store, description, chunk_size, chunk_overlap)
	
	Returns:
		Updated Knowledge Base
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to update Knowledge Base"), frappe.PermissionError)
		
		kb = frappe.get_doc("Knowledge Base", name)
		
		# Update allowed fields
		allowed_fields = ["title", "provider", "embeding_model", "vector_store", "description", "chunk_size", "chunk_overlap"]
		for field in allowed_fields:
			if field in kwargs:
				setattr(kb, field, kwargs[field])
		
		kb.save()
		frappe.db.commit()
		
		return {
			"success": True,
			"message": "Knowledge Base updated successfully",
			"data": {
				"name": kb.name,
				"title": kb.title,
				"provider": kb.provider,
				"embeding_model": kb.embeding_model,
				"vector_store": kb.vector_store,
				"description": kb.description,
				"chunk_size": kb.chunk_size,
				"chunk_overlap": kb.chunk_overlap
			}
		}
	except Exception as e:
		frappe.log_error(f"Error updating Knowledge Base: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def delete_knowledge_base(name: str) -> Dict[str, Any]:
	"""
	Delete a Knowledge Base
	
	Args:
		name: Knowledge Base name
	
	Returns:
		Success status
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "delete"):
			frappe.throw(_("Not permitted to delete Knowledge Base"), frappe.PermissionError)
		
		frappe.delete_doc("Knowledge Base", name)
		frappe.db.commit()
		
		return {
			"success": True,
			"message": "Knowledge Base deleted successfully"
		}
	except Exception as e:
		frappe.log_error(f"Error deleting Knowledge Base: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def upload_file_to_knowledge_base(kb_name: str, text_content: str = "") -> Dict[str, Any]:
	"""
	Upload a file to Knowledge Base (handles multipart/form-data)
	
	Args:
		kb_name: Knowledge Base name or title
		text_content: Optional manual text content
	
	Returns:
		Created document row
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to upload files"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		
		# Get the uploaded file from request
		file = frappe.request.files.get('file')
		
		if not file:
			return {
				"success": False,
				"message": "No file uploaded"
			}
		
		# Save the file
		from frappe.utils.file_manager import save_file
		
		file_doc = save_file(
			fname=file.filename,
			content=file.read(),
			dt="Knowledge Base",
			dn=kb_name,
			is_private=0
		)
		
		# Add document row to Knowledge Base
		kb = frappe.get_doc("Knowledge Base", kb_name)
		
		kb.append("documents", {
			"file": file_doc.file_url,
			"text_content": text_content,
			"is_process": False
		})
		
		kb.save()
		frappe.db.commit()
		
		return {
			"success": True,
			"message": "File uploaded successfully",
			"data": {
				"file_url": file_doc.file_url,
				"file_name": file_doc.file_name,
				"text_content": text_content
			}
		}
	except Exception as e:
		frappe.log_error(f"Error uploading file: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def add_text_document(kb_name: str, text_content: str) -> Dict[str, Any]:
	"""
	Add a text document without file upload
	
	Args:
		kb_name: Knowledge Base name or title
		text_content: Text content to add
	
	Returns:
		Created document row
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to add documents"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		
		kb = frappe.get_doc("Knowledge Base", kb_name)
		
		kb.append("documents", {
			"text_content": text_content,
			"is_process": False
		})
		
		kb.save()
		frappe.db.commit()
		
		return {
			"success": True,
			"message": "Text document added successfully",
			"data": {
				"text_content": text_content
			}
		}
	except Exception as e:
		frappe.log_error(f"Error adding text document: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def delete_document(kb_name: str, row_name: str, delete_from_vector_store: bool = True) -> Dict[str, Any]:
	"""
	Delete a document from Knowledge Base and optionally from vector store
	
	Args:
		kb_name: Knowledge Base name or title
		row_name: Document row name (e.g., "9s9hl54eol")
		delete_from_vector_store: Whether to remove embeddings from vector store (default: True)
	
	Returns:
		Success status with details
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to delete documents"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		kb = frappe.get_doc("Knowledge Base", kb_name)
		
		# Find the document to delete
		doc_to_delete = None
		for doc in kb.documents:
			if doc.name == row_name:
				doc_to_delete = doc
				break
		
		if not doc_to_delete:
			return {
				"success": False,
				"message": f"Document with row_name '{row_name}' not found in Knowledge Base"
			}
		
		# Store info before deletion
		file_name = doc_to_delete.file.split('/')[-1] if doc_to_delete.file else "Text Document"
		chunks_deleted = 0
		
		# Delete from vector store if requested
		if delete_from_vector_store:
			try:
				store = kb.get_vector_store()
				if store:
					# Calculate how many chunks this document has
					if doc_to_delete.text_content:
						text_splitter = kb.get_text_splitter()
						chunks = text_splitter.split_text(doc_to_delete.text_content)
						
						# Delete each chunk from vector store
						chunk_ids = [
							f"{kb_name}-{row_name}-chunk-{i}" 
							for i in range(len(chunks))
						]
						
						# Most vector stores have a delete method
						if hasattr(store, 'delete'):
							store.delete(ids=chunk_ids)
							chunks_deleted = len(chunk_ids)
						else:
							frappe.msgprint(
								"Vector store does not support deletion. Embeddings will remain.",
								indicator="orange"
							)
			except Exception as e:
				frappe.log_error(
					f"Error deleting from vector store: {str(e)}\n{frappe.get_traceback()}", 
					"Vector Store Delete Error"
				)
				frappe.msgprint(
					f"Warning: Could not delete from vector store: {str(e)}",
					indicator="orange"
				)
		
		# Delete the file from file system if it exists
		file_deleted = False
		if doc_to_delete.file:
			try:
				file_path = get_absolute_file_path(doc_to_delete.file)
				if os.path.exists(file_path):
					os.remove(file_path)
					file_deleted = True
				
				# Also delete the File doctype record
				file_doc_name = frappe.db.get_value("File", {"file_url": doc_to_delete.file})
				if file_doc_name:
					frappe.delete_doc("File", file_doc_name, ignore_permissions=True)
			except Exception as e:
				frappe.log_error(
					f"Error deleting file: {str(e)}", 
					"File Delete Error"
				)
				# Continue even if file deletion fails
		
		# Remove from child table
		kb.remove(doc_to_delete)
		kb.save()
		frappe.db.commit()
		
		return {
			"success": True,
			"message": "Document deleted successfully",
			"data": {
				"row_name": row_name,
				"file_name": file_name,
				"file_deleted": file_deleted,
				"chunks_deleted_from_vector_store": chunks_deleted,
				"remaining_documents": len(kb.documents)
			}
		}
	except Exception as e:
		frappe.log_error(f"Error deleting document: {str(e)}\n{frappe.get_traceback()}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def extract_pdfs(kb_name: str) -> Dict[str, Any]:
	"""
	Extract text from all PDFs in Knowledge Base
	
	Args:
		kb_name: Knowledge Base name or title
	
	Returns:
		Extraction results
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to extract PDFs"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		
		kb = frappe.get_doc("Knowledge Base", kb_name)
		# Changed from extract_pdf_from_documents() to extract_all_files()
		result = kb.extract_all_files()
		
		return {
			"success": True,
			"message": f"Extracted {result['extracted']} files with {result['errors']} errors",
			"data": result
		}
	except Exception as e:
		frappe.log_error(f"Error extracting files: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def upsert_to_vector_store(kb_name: str, chunk_size: int = None, overlap: int = None) -> Dict[str, Any]:
	"""
	Index all documents to vector store
	
	Args:
		kb_name: Knowledge Base name or title
		chunk_size: Chunk size for text splitting (optional, uses KB default)
		overlap: Overlap between chunks (optional, uses KB default)
	
	Returns:
		Indexing results
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to index documents"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		
		kb = frappe.get_doc("Knowledge Base", kb_name)
		
		# Use KB's default chunk_size and chunk_overlap if not provided
		if chunk_size is None:
			chunk_size = kb.chunk_size or 1000
		if overlap is None:
			overlap = kb.chunk_overlap or 200
		
		num_indexed = kb.upsert(chunk_size=chunk_size, overlap=overlap)
		
		return {
			"success": True,
			"message": f"Successfully indexed {num_indexed} chunks",
			"data": {
				"num_chunks": num_indexed
			}
		}
	except Exception as e:
		frappe.log_error(f"Error indexing documents: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def search_knowledge_base(kb_name: str, query: str, limit: int = 5) -> Dict[str, Any]:
	"""
	Search in Knowledge Base with enhanced results including text content
	
	Args:
		kb_name: Knowledge Base name or title
		query: Search query
		limit: Number of results
	
	Returns:
		Search results with text snippets
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "read"):
			frappe.throw(_("Not permitted to search Knowledge Base"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		
		kb = frappe.get_doc("Knowledge Base", kb_name)
		raw_results = kb.search(query=query, limit=limit)
		
		# Enhance results with actual text content from database
		enhanced_results = []
		for result in raw_results:
			# Get the row_name from metadata
			row_name = result.get("metadata", {}).get("row_name")
			
			if not row_name:
				continue
			
			# Query the database directly for the document content
			doc_data = frappe.db.get_value(
				"Knowledge Document",
				row_name,
				["text_content", "file", "parent"],
				as_dict=True
			)
			
			if not doc_data:
				continue
			
			text_content = doc_data.get("text_content", "")
			file_path = doc_data.get("file", "")
			file_name = file_path.split('/')[-1] if file_path else "Text Document"
			
			# Extract a relevant snippet
			snippet = ""
			if text_content:
				# Try to find query terms in the text for a better snippet
				query_lower = query.lower()
				text_lower = text_content.lower()
				
				# Find the first occurrence of any query word
				query_words = query_lower.split()
				best_pos = -1
				
				for word in query_words:
					pos = text_lower.find(word)
					if pos != -1:
						best_pos = pos
						break
				
				if best_pos != -1:
					# Extract context around the match
					start = max(0, best_pos - 150)
					end = min(len(text_content), best_pos + 350)
					snippet = text_content[start:end]
					
					if start > 0:
						snippet = "..." + snippet
					if end < len(text_content):
						snippet = snippet + "..."
				else:
					# No match found, just take the beginning
					snippet = text_content[:500] + ("..." if len(text_content) > 500 else "")
			else:
				snippet = "No content available"
			
			# Calculate relevance based on score (lower is better for distance metrics)
			score = result.get("score", 1.0)
			if score < 0.8:
				relevance = "High"
			elif score < 1.2:
				relevance = "Medium"
			else:
				relevance = "Low"
			
			enhanced_results.append({
				"row_name": row_name,
				"score": round(score, 4),
				"relevance": relevance,
				"snippet": snippet.strip(),
				"source": file_name,
				"has_file": bool(file_path),
				"content_length": len(text_content) if text_content else 0,
				"metadata": result.get("metadata", {})
			})
		
		return {
			"success": True,
			"data": {
				"query": query,
				"kb_name": kb_name,
				"total_results": len(enhanced_results),
				"results": enhanced_results
			}
		}
	except Exception as e:
		frappe.log_error(f"Error searching Knowledge Base: {str(e)}\n{frappe.get_traceback()}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def reprocess_documents(kb_name: str) -> Dict[str, Any]:
	"""
	Mark all documents for reprocessing
	
	Args:
		kb_name: Knowledge Base name or title
	
	Returns:
		Success status
	"""
	try:
		if not frappe.has_permission("Knowledge Base", "write"):
			frappe.throw(_("Not permitted to reprocess documents"), frappe.PermissionError)
		
		# Resolve KB name
		kb_name = get_kb_name(kb_name)
		
		kb = frappe.get_doc("Knowledge Base", kb_name)
		kb.reprocess_all_documents()
		
		return {
			"success": True,
			"message": "All documents marked for reprocessing"
		}
	except Exception as e:
		frappe.log_error(f"Error reprocessing documents: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}


@frappe.whitelist(allow_guest=False)
def get_providers_and_models() -> Dict[str, Any]:
	"""
	Get available LLM Providers and Embedding Models
	
	Returns:
		List of providers and models
	"""
	try:
		# Get all fields from LLM Provider to avoid field errors
		providers = frappe.get_all(
			"LLM Provider",
			fields=["name"],
			order_by="name"
		)
		
		# Get all fields from LLM to avoid field errors
		models = frappe.get_all(
			"LLM",
			fields=["name", "provider"],
			order_by="name"
		)
		
		vector_stores = ["Pinecone", "Qdrant", "Supabase", "ChromaDB"]
		
		return {
			"success": True,
			"data": {
				"providers": providers,
				"models": models,
				"vector_stores": vector_stores
			}
		}
	except Exception as e:
		frappe.log_error(f"Error fetching providers: {str(e)}", "KB API Error")
		return {
			"success": False,
			"message": str(e)
		}