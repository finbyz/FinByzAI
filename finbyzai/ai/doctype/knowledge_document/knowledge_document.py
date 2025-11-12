# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class KnowledgeDocument(Document):
	"""Knowledge Document child table row with auto PDF extraction"""
	
	def validate(self):
		"""
		On validate, trigger PDF extraction if the file has changed
		and mark for reprocessing if content is modified.
		"""
		# Only extract if file is new or has been changed
		if self.has_value_changed("file") and self.file:
			self.auto_extract_pdf()
		
		# Mark as not processed if content changed, ensuring it gets re-indexed
		if self.has_value_changed("text_content") or self.has_value_changed("file"):
			self.is_process = False
	
	def auto_extract_pdf(self):
		"""Automatically extract text from an attached PDF on save."""
		if not self.file:
			return
		
		if not self.file.lower().endswith('.pdf'):
			return
		
		# Import the extraction function from the parent doctype's controller
		from finbyzai.ai.doctype.knowledge_base.knowledge_base import extract_pdf_text_for_row
		
		try:
			# Call the extraction logic
			if extract_pdf_text_for_row(self):
				# IMPROVED: Clearer success message
				frappe.msgprint(
					f"Text extracted from: <b>{self.file.split('/')[-1]}</b>",
					alert=True,
					indicator="green",
					title="PDF Processed"
				)
		except Exception as e:
			# On auto-extract, we log the error and show a message but DO NOT block the save.
			# This is better UX than throwing a hard error.
			frappe.log_error(
				f"Auto-extraction failed for PDF: {self.file}\n{e}",
				"PDF Auto-Extraction Error"
			)
			frappe.msgprint(
				f"Could not automatically extract text from '{self.file.split('/')[-1]}'. You can try again using the 'Extract Text from PDFs' button on the Knowledge Base.",
				alert=True,
				indicator="orange",
				title="PDF Extraction Warning"
			)