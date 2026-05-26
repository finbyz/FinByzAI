"""
Base classes for AI Agent functionality.
Provides common patterns and abstractions for agent implementations.
"""

import json
import warnings
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union
import frappe
from langchain_litellm.chat_models import ChatLiteLLM
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from json_schema_to_pydantic import create_model
from langgraph.prebuilt import create_react_agent
from langchain_classic.agents import AgentType, initialize_agent
from langchain_classic.memory import (
    ConversationBufferMemory,
    VectorStoreRetrieverMemory,
    ConversationSummaryMemory,
    ConversationBufferWindowMemory
)
from finbyzai.ai.memory.base import FrappeChatMemory


class BaseAgentMemory(ABC):
    """Base class for agent memory management."""
    
    def __init__(self, agent_name: str, memory_type: str, **kwargs):
        self.agent_name = agent_name
        self.memory_type = memory_type
        self.kwargs = kwargs
    
    @abstractmethod
    def get_memory(self) -> Optional[Any]:
        """Get the memory instance."""
        pass
    
    def clear_memory(self) -> bool:
        """Clear the memory if possible."""
        try:
            memory = self.get_memory()
            if memory and hasattr(memory, 'clear'):
                memory.clear()
                return True
            return False
        except Exception as e:
            frappe.log_error(f"Error clearing memory for agent '{self.agent_name}': {e}")
            return False


class FrappeAgentMemory(BaseAgentMemory):
    """Frappe-specific agent memory implementation."""
    
    def get_memory(self) -> Optional[FrappeChatMemory]:
        """Get Frappe chat memory instance."""
        try:
            base_memory = self._create_base_memory()
            if base_memory:
                return FrappeChatMemory(
                    memory=base_memory,
                    agent_name=self.agent_name
                )
            return None
        except Exception as e:
            frappe.log_error(f"Error creating memory for agent '{self.agent_name}': {e}")
            return None
    
    def _create_base_memory(self) -> Optional[Any]:
        """Create the base memory instance based on type."""
        if self.memory_type == "Buffer Memory":
            return ConversationBufferMemory(return_messages=True)
        elif self.memory_type == "Window Memory":
            window_size = self.kwargs.get('window_size', 5)
            return ConversationBufferWindowMemory(k=window_size, return_messages=True)
        elif self.memory_type == "Summary Memory":
            llm = self.kwargs.get('llm')
            if not llm:
                raise ValueError("LLM is required for Summary Memory")
            return ConversationSummaryMemory(llm=llm)
        elif self.memory_type == "Vector Memory":
            retriever = self.kwargs.get('retriever')
            if not retriever:
                raise ValueError("Retriever is required for Vector Memory")
            return VectorStoreRetrieverMemory(retriever=retriever)
        return None


class BaseAgentTools(ABC):
    """Base class for agent tools management."""
    
    def __init__(self, agent_doc):
        self.agent_doc = agent_doc
    
    @abstractmethod
    def get_tools(self) -> List[Any]:
        """Get all tools for the agent."""
        pass
    
    def _get_workflow_tools(self) -> List[Any]:
        """Get tools for workflow execution."""
        workflow_tools = []
        if hasattr(self.agent_doc, 'workflow_steps') and self.agent_doc.workflow_steps:
            for step in self.agent_doc.workflow_steps:
                if hasattr(step, 'tool_name') and step.tool_name:
                    workflow_tool = self._create_workflow_tool(step)
                    if workflow_tool:
                        workflow_tools.append(workflow_tool)
        return workflow_tools
    
    def _get_custom_tools(self) -> List[Any]:
        """Get custom tools based on configuration."""
        custom_tools = []
        if hasattr(self.agent_doc, 'custom_tools_config') and self.agent_doc.custom_tools_config:
            try:
                config = json.loads(self.agent_doc.custom_tools_config)
                for tool_config in config:
                    custom_tool = self._create_custom_tool(tool_config)
                    if custom_tool:
                        custom_tools.append(custom_tool)
            except Exception as e:
                frappe.log_error(f"Error creating custom tools: {str(e)}")
        return custom_tools
    
    def _create_workflow_tool(self, step) -> Optional[Any]:
        """Create a workflow tool from step configuration."""
        # This should be implemented by subclasses
        return None
    
    def _create_custom_tool(self, tool_config: Dict[str, Any]) -> Optional[Any]:
        """Create a custom tool from configuration."""
        # This should be implemented by subclasses
        return None


class FrappeAgentTools(BaseAgentTools):
    """Frappe-specific agent tools implementation."""
    
    def get_tools(self) -> List[Any]:
        """Get all tools for the agent."""
        tools_list = []
        
        # Get linked tools
        if hasattr(self.agent_doc, 'tools') and self.agent_doc.tools:
            for ai_agent_tool in self.agent_doc.tools:
                tool = frappe.get_doc("AI Tool", ai_agent_tool.tool)
                tools_list.append(tool.get_tool())
        
        # Get knowledge base tools
        if (hasattr(self.agent_doc, 'agent_type') and 
            self.agent_doc.agent_type == "Knowledge Base Agent" and
            hasattr(self.agent_doc, 'knowledge_base') and self.agent_doc.knowledge_base):
            kb = frappe.get_doc("Knowledge Base", self.agent_doc.knowledge_base)
            vs = kb.get_vector_store()
            tools_list.append(vs.as_tool())
        
        # Get workflow tools
        if (hasattr(self.agent_doc, 'enable_workflow') and 
            self.agent_doc.enable_workflow):
            workflow_tools = self._get_workflow_tools()
            tools_list.extend(workflow_tools)
        
        # Get custom tools
        if (hasattr(self.agent_doc, 'custom_tools') and 
            self.agent_doc.custom_tools):
            custom_tools = self._get_custom_tools()
            tools_list.extend(custom_tools)
        
        return tools_list


class BaseAgentLLM(ABC):
    """Base class for LLM management."""
    
    def __init__(self, agent_doc):
        self.agent_doc = agent_doc
    
    @abstractmethod
    def get_llm(self) -> ChatLiteLLM:
        """Get the LLM instance."""
        pass


class FrappeAgentLLM(BaseAgentLLM):
    """Frappe-specific LLM implementation."""
    
    def get_llm(self) -> ChatLiteLLM:
        """Get the LLM instance for this agent."""
        try:
            if self.agent_doc.agent_type == "Gemini Cache Agent":
                cache_doc = frappe.get_doc("Gemini Cache", self.agent_doc.gemini_cache)
                return cache_doc._llm
            else:
                llm_doc = frappe.get_doc("LLM", self.agent_doc.llm)
                return llm_doc.llm
        except Exception as e:
            frappe.log_error(f"Failed to get LLM for agent {self.agent_doc.name}: {e}")
            raise


class BaseAgentInvoker(ABC):
    """Base class for agent invocation."""
    
    def __init__(self, agent_doc, memory_manager, llm_manager):
        self.agent_doc = agent_doc
        self.memory_manager = memory_manager
        self.llm_manager = llm_manager
    
    @abstractmethod
    def invoke(self, query: str = None, **kwargs) -> Any:
        """Invoke the agent with the given query."""
        pass
    
    def _prepare_memory_context(self, query: str) -> List[tuple]:
        """Prepare memory context for the query."""
        memory_context = []
        memory = self.memory_manager.get_memory()
        
        if memory:
            try:
                memory_vars = memory.load_memory_variables({"input": query})
                memory_context = memory_vars.get("chat_history", [])
            except Exception as e:
                frappe.log_error(f"Error loading memory context: {e}")
        
        return memory_context
    
    def _save_to_memory(self, query: str, response: Any) -> bool:
        """Save query and response to memory."""
        try:
            memory = self.memory_manager.get_memory()
            if memory and query and response:
                memory.save_context({"input": query}, {"output": response})
                return True
        except Exception as e:
            frappe.log_error(f"Error saving to memory: {e}")
        return False


class LangGraphAgentInvoker(BaseAgentInvoker):
    """LangGraph-based agent invoker."""
    
    def invoke(self, query: str = None, **kwargs) -> Any:
        """Invoke LangGraph-based agent."""
        try:
            llm = self.llm_manager.get_llm()
            memory_context = self._prepare_memory_context(query)
            
            messages = self._prepare_messages(query, memory_context)
            prompt = ChatPromptTemplate.from_messages(messages)
            
            # Handle output schema
            dynamic_model = None
            if hasattr(self.agent_doc, 'output_schema') and self.agent_doc.output_schema:
                dynamic_model = create_model(schema=json.loads(self.agent_doc.output_schema))
            
            format_instructions = ''
            if dynamic_model:
                output_parser = PydanticOutputParser(pydantic_object=dynamic_model)
                format_instructions = output_parser.get_format_instructions()
            else:
                output_parser = StrOutputParser()

            chain = prompt | llm | output_parser
            
            input_vars = {
                "format_instructions": format_instructions,
                "query": query,
                **kwargs
            }
            response = chain.invoke(input_vars)
            
            # Save to memory
            self._save_to_memory(query, response)
            
            return response
            
        except Exception as e:
            frappe.log_error(f"Error in LangGraph agent invocation: {e}")
            raise
    
    def _prepare_messages(self, query: str, memory_context: List) -> List[tuple]:
        """Prepare messages for the agent."""
        messages = []
        
        # Add chat messages from agent configuration
        if hasattr(self.agent_doc, 'chat_messages'):
            messages.extend(self.agent_doc.chat_messages)
        
        # Add memory context
        for msg in memory_context:
            if hasattr(msg, 'content'):
                if hasattr(msg, '__class__') and 'Human' in msg.__class__.__name__:
                    messages.append(("human", msg.content))
                elif hasattr(msg, '__class__') and 'AI' in msg.__class__.__name__:
                    messages.append(("assistant", msg.content))
                elif hasattr(msg, '__class__') and 'System' in msg.__class__.__name__:
                    messages.append(("system", msg.content))
        
        # Add current query
        if query:
            messages.append(("human", "{query}"))
        
        return messages


class AgentExecutorInvoker(BaseAgentInvoker):
    """AgentExecutor-based agent invoker."""
    
    def invoke(self, query: str = None, **kwargs) -> Any:
        """Invoke agent using AgentExecutor."""
        try:
            agent = self._get_agent()
            memory = self.memory_manager.get_memory()
            
            # Prepare input data
            input_data = {"input": query}
            
            # Load memory if available
            if memory:
                memory_vars = memory.load_memory_variables(input_data)
                input_data.update(memory_vars)
            
            input_data.update(kwargs)
            
            # Invoke the agent
            response = agent.invoke(input_data)
            
            # Save to memory
            self._save_to_memory(query, response)
            
            return response
            
        except Exception as e:
            frappe.log_error(f"Error in AgentExecutor invocation: {e}")
            raise
    
    def _get_agent(self):
        """Get the agent instance."""
        # This should be implemented by subclasses
        raise NotImplementedError


class BaseAgentValidator(ABC):
    """Base class for agent validation."""
    
    def __init__(self, agent_doc):
        self.agent_doc = agent_doc
    
    @abstractmethod
    def validate(self) -> bool:
        """Validate the agent configuration."""
        pass
    
    def _validate_gemini_cache_agent(self) -> bool:
        """Validate Gemini Cache Agent specific requirements."""
        if self.agent_doc.agent_type == "Gemini Cache Agent":
            if hasattr(self.agent_doc, 'messages') and self.agent_doc.messages:
                if any(msg.type == 'system' for msg in self.agent_doc.messages):
                    frappe.throw("You can not set system message for Gemini Cache Agent")
        return True
    
    def _validate_memory_configuration(self) -> bool:
        """Validate memory configuration."""
        if (hasattr(self.agent_doc, 'enable_memory') and 
            self.agent_doc.enable_memory and
            (not hasattr(self.agent_doc, 'memory_type') or not self.agent_doc.memory_type)):
            frappe.throw("Memory type is required when memory is enabled")
        return True
    
    def _validate_workflow_configuration(self) -> bool:
        """Validate workflow configuration."""
        if (hasattr(self.agent_doc, 'enable_workflow') and 
            self.agent_doc.enable_workflow and
            (not hasattr(self.agent_doc, 'workflow_steps') or not self.agent_doc.workflow_steps)):
            frappe.throw("Workflow steps are required when workflow is enabled")
        return True


class FrappeAgentValidator(BaseAgentValidator):
    """Frappe-specific agent validator."""
    
    def validate(self) -> bool:
        """Validate the agent configuration."""
        self._validate_gemini_cache_agent()
        self._validate_memory_configuration()
        self._validate_workflow_configuration()
        
        # Validate output schema
        if hasattr(self.agent_doc, 'output_schema') and not self.agent_doc.output_schema:
            self.agent_doc.output_schema = None
        
        return True

