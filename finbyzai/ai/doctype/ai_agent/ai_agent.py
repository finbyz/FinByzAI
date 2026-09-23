from finbyzai.ai.agent.agent_service import AgentService
from finbyzai.ai.agent.builtin_tools import BuiltinToolCompiler
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
        selected_tools = [row.tool for row in self.tools or [] if row.tool]
        if len(selected_tools) != len(set(selected_tools)):
            frappe.throw("Each AI Tool can only be selected once")
        if self.agent_type == "Gemini Cache Agent":
            if any(map(lambda x:x.type == 'system', self.messages)):
                frappe.throw("You can not set system message for Gemini Cache Agent")
        if self.enable_memory and not self.memory_type:
            frappe.throw("Memory type is required when memory is enabled")
        if not self.max_iterations:
            self.max_iterations = 25
        elif self.max_iterations < 0:
            frappe.throw("Max Iterations must be at least 1")
        if self.temperature is not None:
            if self.temperature < 0:
                frappe.throw("Temperature must be between 0 and 1")
            if self.temperature > 1:
                self.temperature = 1
        if self.max_tokens is not None and self.max_tokens < 0:
            frappe.throw("Max Tokens cannot be negative")
        if self.agent_type != "Gemini Cache Agent" and self.llm:
            llm_doc = frappe.get_doc("LLM", self.llm)
            BuiltinToolCompiler(self, llm_doc).compile()
        
    @property
    def agent_service(self):
        if self._agent_service:
            return self._agent_service
        self._agent_service = AgentService(self)
        return self._agent_service
    
    @staticmethod
    def _format_response(response):
        """Safely format an agent response into a serializable value."""
        if isinstance(response, str):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if isinstance(response, dict):
            return response
        return str(response)
    
    @frappe.whitelist(methods=["POST"])
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
                return {
                    "success": False,
                    "error": "Query is required",
                    "query": "",
                    "variables": {},
                    "agent_type": self.agent_type
                }
            
            conversation_id = kwargs.get("conversation_id")
            additional_vars = {
                key: value
                for key, value in kwargs.items()
                if key not in {"input", "conversation_id"}
            }

            frappe.logger().info(f"Testing agent with query: {query}")
            frappe.logger().info(f"Additional variables: {additional_vars}")
            
            # Invoke the agent with query and additional variables
            response = self.agent_service.invoke(query=query, **additional_vars)

            # Normalise + surface the response
            formatted_response = self._format_response(response)
            frappe.msgprint(
                formatted_response
                if isinstance(formatted_response, str)
                else frappe.as_json(formatted_response)
            )

            return {
                "success": True,
                "response": formatted_response,
                "query": query,
                "variables": additional_vars,
                "agent_type": self.agent_type,
                "llm": self.llm if self.agent_type != "Gemini Cache Agent" else self.gemini_cache,
                "memory_enabled": self.enable_memory,
                "conversation_id": self.agent_service.conversation_id,
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
        """Normalise an agent response into a JSON-serialisable, display-friendly value.

        Agent types return different shapes: a plain string (ReAct / StrOutputParser),
        a LangChain message object (``.content``), a pydantic model (``.model_dump()``
        when an output schema is set), or an AgentExecutor dict (``{"output": ...}``).
        """
        if response is None:
            return ""
        if isinstance(response, str):
            return response
        if hasattr(response, "model_dump"):
            try:
                return response.model_dump()
            except Exception:
                pass
        if hasattr(response, "content"):
            return response.content
        if isinstance(response, dict):
            return response.get("output", response)
        return str(response)
