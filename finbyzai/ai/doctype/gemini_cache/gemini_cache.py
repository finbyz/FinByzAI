# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils.file_manager import get_file_path
from finbyzai.ai.doctype.gemini_cache.cache import create_gemini_cache,delete_cache
from langchain_google_genai.chat_models import ChatGoogleGenerativeAI

class GeminiCache(Document):
    @property 
    def _llm(self): 
        provider = frappe.get_value("LLM", self.llm,'provider') 
        provider = frappe.get_doc("LLM Provider", provider) 
        
        model_name = self.llm.split('/',2)[1]
        return ChatGoogleGenerativeAI(
            model = model_name,
            google_api_key = provider.get_password("api_key"),
            cached_content=self.cache_name
        )
    
    @frappe.whitelist()
    def update_cache(self):
        files = self.get_files()
        model_name = self.llm.split('/',2)[1]
        provider = frappe.get_value("LLM", self.llm,'provider')
        api_key = frappe.get_doc("LLM Provider",provider).get_password("api_key")
        try:
            cache = create_gemini_cache(
                files=files,
                api_key=api_key,
                ttl=f"{self.ttl}s",
                cache_display_name=self.display_name,
                model=model_name,
                information = self.information,
                system_instruction=self.system_instruction
            )
        except Exception as e:
            frappe.log_error("Gemini Cache creation error",frappe.get_traceback())
            self.status = "Failed"
            self.save()
            return {
                "success": False
            }
        if not cache: return
        old_cache_name = self.cache_name
        self.cache_name = cache.name
        self.expiry = cache.expire_time.strftime("%Y-%m-%d %H:%M:%S")
        self.status = "Complete"
        self.save()
        if old_cache_name:
            delete_cache(cache=old_cache_name,api_key=api_key)

    def get_files(self):
        files = frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Gemini Cache", "attached_to_name": self.name},
            fields=["file_url"]
        )
        return [get_file_path(f.file_url) for f in files]
        
    def get_files_text(self):
        text_content = []
        files = self.get_files()

        for file_path in files:
            try:
                if file_path.endswith(".txt"):
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                        text_content.append(file.read())

                elif file_path.endswith(".md"):
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                        text_content.append(file.read())

                elif file_path.endswith(".pdf"):
                    from PyPDF2 import PdfReader
                    reader = PdfReader(file_path)
                    pdf_text = "\n".join([page.extract_text() or "" for page in reader.pages])
                    text_content.append(pdf_text)

                elif file_path.endswith((".docx", ".doc")):
                    import docx
                    doc = docx.Document(file_path)
                    doc_text = "\n".join([p.text for p in doc.paragraphs])
                    text_content.append(doc_text)

                else:
                    with open(file_path, "rb") as file:
                        text_content.append(file.read().decode("utf-8", errors="ignore"))

            except Exception as e:
                frappe.log_error(frappe.get_traceback(), f"AI Agent File Read Error: {file_path}")

        return "\n".join(text_content)

    