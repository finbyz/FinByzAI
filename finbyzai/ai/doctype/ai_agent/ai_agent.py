from finbyzai.ai.agent.agent_service import AgentService
import frappe
from frappe.model.document import Document
import json


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
            input (str): The test query from the dialog
            **kwargs: Additional variables from the dialog
            
        Returns:
            dict: Test result with response and metadata
        """
        try:
            # Get the query from 'input' field
            query = kwargs.get('input', '').strip()
            
            if not query:
                frappe.throw("Query is required")
            
            # Remove 'input' from kwargs as we're passing it as 'query' parameter
            additional_vars = {k: v for k, v in kwargs.items() if k != 'input'}
            
            # Log for debugging
            frappe.logger().info(f"Testing agent with query: {query}")
            frappe.logger().info(f"Additional variables: {additional_vars}")
            
            # Invoke the agent with query and additional variables
            response = self.agent_service.invoke(query=query, **additional_vars)
            
            # Format response based on type
            formatted_response = self._format_response(response)
            
            return {
                "success": True,
                "response": formatted_response,
                "query": query,
                "variables": additional_vars,
                "agent_type": self.agent_type,
                "llm": self.llm if self.agent_type != "Gemini Cache Agent" else self.gemini_cache,
                "memory_enabled": self.enable_memory,
            }
            
        except Exception as e:
            error_msg = str(e)
            frappe.log_error(
                title=f"Error in test_agent for {self.name}",
                message=f"Query: {kwargs.get('input', '')}\nError: {error_msg}\n\n{frappe.get_traceback()}"
            )
            
            return {
                "success": False,
                "error": error_msg,
                "query": kwargs.get('input', ''),
                "variables": {k: v for k, v in kwargs.items() if k != 'input'},
                "agent_type": self.agent_type,
                "llm": self.llm if self.agent_type != "Gemini Cache Agent" else self.gemini_cache
            }
    
    def _format_response(self, response):
        """Format response based on agent type and response structure"""
        try:
            # Handle different response types
            if isinstance(response, dict):
                # For agent responses with 'output' key
                if 'output' in response:
                    output = response['output']
                    # If output is a Pydantic model, convert to dict
                    if hasattr(output, 'model_dump'):
                        return output.model_dump()
                    elif hasattr(output, 'dict'):
                        return output.dict()
                    return output
                return response
            
            # For Pydantic models
            elif hasattr(response, 'model_dump'):
                return response.model_dump()
            elif hasattr(response, 'dict'):
                return response.dict()
            
            # For string responses
            elif isinstance(response, str):
                return response
            
            # For other objects, try to convert to string
            else:
                return str(response)
                
        except Exception as e:
            frappe.logger().error(f"Error formatting response: {e}")
            return str(response)
    