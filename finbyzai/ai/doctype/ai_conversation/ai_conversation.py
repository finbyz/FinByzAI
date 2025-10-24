import frappe
from frappe.model.document import Document
from frappe.utils import now, get_datetime
from datetime import datetime
from langchain.memory.chat_memory import BaseChatMemory
from langchain_core.messages import BaseMessage, get_buffer_string,HumanMessage,AIMessage
from typing import Any

class AIConversation(Document):
    """
    AI Conversation DocType to store conversation history between users and AI agents.
    
    Features:
    - Track conversation sessions with start/end times
    - Store message history in a structured format
    - Link conversations to specific users and AI agents
    - Track conversation status and metadata
    - Automatic message count calculation
    """
    
    def validate(self):
        """Validate the conversation document"""
        # Set start time if not provided
        if not self.start_time:
            self.start_time = now()
        
        # Update message count
        self.message_count = len(self.messages) if self.messages else 0
        
        # Validate end time
        if self.end_time and self.start_time:
            if get_datetime(self.end_time) < get_datetime(self.start_time):
                frappe.throw("End time cannot be before start time")
        
        # Auto-complete conversation if end time is set
        if self.end_time and self.status == "Active":
            self.status = "Completed"
    
    @staticmethod
    def create_conversation(user, agent, title=None):
        """
        Create a new conversation
        
        Args:
            user (str): User ID
            agent (str): AI Agent name
            title (str, optional): Conversation title
            
        Returns:
            AIConversation: New conversation document
        """
        if not title:
            title = f"Conversation with {agent} - {now()}"
        
        conversation = frappe.new_doc("AI Conversation")
        conversation.title = title
        conversation.user = user
        conversation.agent = agent
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.insert()
        
        return conversation
    