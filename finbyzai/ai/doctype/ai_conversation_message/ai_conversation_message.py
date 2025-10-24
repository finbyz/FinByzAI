import frappe
from frappe.model.document import Document


class AIConversationMessage(Document):
    """
    AI Conversation Message child table to store individual messages within a conversation.
    
    This DocType is used as a child table in AI Conversation to store:
    - Message content and type
    - Timestamp information
    - Optional metadata for additional context
    """
    
    def validate(self):
        """Validate the message document"""
        # Set timestamp if not provided
        if not self.timestamp:
            self.timestamp = frappe.utils.now()
        
        # Validate message type
        valid_types = ["human", "assistant", "system"]
        if self.message_type not in valid_types:
            frappe.throw(f"Message type must be one of: {', '.join(valid_types)}")
        
        # Validate content is not empty
        if not self.content or not self.content.strip():
            frappe.throw("Message content cannot be empty")
    
    def before_save(self):
        """Actions to perform before saving"""
        # Ensure timestamp is set
        if not self.timestamp:
            self.timestamp = frappe.utils.now()
    
    def get_formatted_content(self):
        """
        Get formatted content with type prefix
        
        Returns:
            str: Formatted message content
        """
        type_prefix = {
            "human": "👤 User: ",
            "assistant": "🤖 Assistant: ",
            "system": "⚙️ System: "
        }
        
        prefix = type_prefix.get(self.message_type, "📝 Message: ")
        return f"{prefix}{self.content}"
    
    def get_metadata_dict(self):
        """
        Get metadata as a dictionary
        
        Returns:
            dict: Metadata dictionary or empty dict if none
        """
        if self.metadata:
            try:
                return frappe.parse_json(self.metadata)
            except:
                return {}
        return {}
    
    def set_metadata(self, metadata_dict):
        """
        Set metadata from a dictionary
        
        Args:
            metadata_dict (dict): Metadata to store
        """
        if metadata_dict:
            self.metadata = frappe.as_json(metadata_dict)
        else:
            self.metadata = None
