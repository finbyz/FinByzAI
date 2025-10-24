from finbyzai.ai.agent.agent_service import AgentService
import frappe
from frappe.model.document import Document
import os



class AIAgent(Document):
    """
    Enhanced AI Agent with capabilities:
    - Multiple agent types with specialized behaviors
    - Advanced memory management
    - Tool integration and workflow capabilities
    - Conversation context and state management
    - Structured output and response formatting
    """
    
    def __init__(self, *args, **kwargs):
        self._agent_service = None
        super().__init__(*args, **kwargs)
    
    def validate(self):
        if self.agent_type == "Gemini Cache Agent":
            if any(map(lambda x:x.type == 'system', self.messages)):
                frappe.throw("You can not set system message for Gemini Cache Agent")
        if not self.output_schema:
            self.output_schema = None

        if self.enable_memory and not self.memory_type:
            frappe.throw("Memory type is required when memory is enabled")
        
    @property
    def agent_service(self):
        if self._agent_service:
            return self._agent_service
        self._agent_service = AgentService(self)
        return self._agent_service
    
    @frappe.whitelist()
    def test_agent(self, **kwargs):
        """
        Test the AI agent with automatic memory and session management.
        
        Args:
            query (str): The test query
            test_type (str): Type of test (simple, workflow)
            
        Returns:
            dict: Test result with response and metadata
        """
        try:
            query = kwargs.get('input', '')
            
            response = self.agent_service.invoke(query, **kwargs)
            
            return {
                "success": True,
                "response": response,
                "agent_type": self.agent_type,
                "llm": self.llm if self.agent_type != "Gemini Cache Agent" else self.gemini_cache,
                "memory_enabled": self.enable_memory,
            }
        except Exception as e:
            frappe.log_error(f"Error in test_agent: {e}")
            return {
                "success": False,
                "error": str(e),
                "query": kwargs.get('query', ''),
                "agent_type": self.agent_type,
                "llm": self.llm if self.agent_type != "Gemini Cache Agent" else self.gemini_cache
            }
