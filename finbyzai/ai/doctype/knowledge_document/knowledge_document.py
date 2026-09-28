# Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class KnowledgeDocument(Document):
    """File source processed asynchronously by its parent Knowledge Base."""

    def validate(self):
        if self.has_value_changed("file"):
            self.is_processed = False
