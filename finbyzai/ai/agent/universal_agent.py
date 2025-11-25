from typing import List, Optional, Union
from finbyzai.ai.agent.agent_service import AgentService
from frappe.model.document import Document
from langchain_core.tools import BaseTool
import re
import frappe


class UniversalReactAgent(AgentService):
    """
    Universal React Agent that can handle any type of sub-agent:
    - React Agents
    - Structured Chat Agents  
    - Conversational Agents
    - Basic Chains
    - LangGraph Agents
    - Image Generation Agents
    - Gemini Cache Agents
    """
    
    def __init__(
        self, 
        agent: Union[str, Document],
        dynamic_tools: Optional[List[BaseTool]] = None,
        sub_agents: Optional[List[str]] = None,
        knowledge_bases: Optional[List[str]] = None
    ):
        """
        Initialize Universal React Agent with any type of sub-agents.
        
        Args:
            agent (str | Document): Base agent name or document
            dynamic_tools (List[BaseTool]): Additional tools to include
            sub_agents (List[str]): List of ANY sub-agent names to use as tools
            knowledge_bases (List[str]): List of knowledge base names to include
        """
        super().__init__(agent)
        self.dynamic_tools = dynamic_tools or []
        self.sub_agents = sub_agents or []
        self.knowledge_bases = knowledge_bases or []
    
    def _sanitize_tool_name(self, name: str) -> str:
        """Sanitize tool name to only contain alphanumeric, underscores, and hyphens."""
        if not name:
            return "unnamed_tool"
            
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        
        sanitized = re.sub(r'_+', '_', sanitized)
        
        sanitized = sanitized.strip('_')
        
        if sanitized and sanitized[0].isdigit():
            sanitized = 'tool_' + sanitized
            
        if not sanitized:
            sanitized = 'agent_tool'
        elif len(sanitized) > 64:
            sanitized = sanitized[:64]
            
        return sanitized
    
    def get_tools(self) -> List[BaseTool]:
        """Extend base tools with sub-agents, knowledge bases, and dynamic tools."""
        base_tools = super().get_tools()
        
        # Convert sub-agents to tools
        sub_agents_tools = []
        for sub_agent_name in self.sub_agents:
            try:
                sub_agent_service = AgentService(sub_agent_name)
                
                # Get the agent document to extract description
                agent_doc = sub_agent_service.agent_doc
                description = (
                    agent_doc.description 
                    if hasattr(agent_doc, 'description') and agent_doc.description
                    else f"Executes the {sub_agent_name} agent"
                )
                
                # Create sanitized name
                sanitized_name = self._sanitize_tool_name(sub_agent_name)
                
                # Convert to tool with proper name and description
                tool = sub_agent_service.as_tool(
                    name=sanitized_name,
                    description=description
                )
                sub_agents_tools.append(tool)
                
            except Exception as e:
                frappe.log_error(
                    title=f"Failed to create tool from sub-agent: {sub_agent_name}",
                    message=str(e)
                )
                continue
        
        # Add knowledge base tools
        kb_tools = []
        for kb_name in self.knowledge_bases:
            try:
                kb = frappe.get_doc("Knowledge Base", kb_name)
                vs = kb.get_vector_store()
                kb_tool = vs.as_tool()
                
                # Sanitize the knowledge base tool name
                if hasattr(kb_tool, 'name'):
                    original_name = kb_tool.name
                    kb_tool.name = self._sanitize_tool_name(original_name)
                else:
                    kb_tool.name = self._sanitize_tool_name(f"{kb_name}_kb")
                
                kb_tools.append(kb_tool)
                
            except Exception as e:
                frappe.log_error(
                    title=f"Failed to create tool from knowledge base: {kb_name}",
                    message=str(e)
                )
                continue
        
        # Sanitize dynamic tool names as well
        sanitized_dynamic_tools = []
        for tool in self.dynamic_tools:
            try:
                if hasattr(tool, 'name'):
                    tool.name = self._sanitize_tool_name(tool.name)
                sanitized_dynamic_tools.append(tool)
            except Exception as e:
                frappe.log_error(
                    title="Failed to sanitize dynamic tool",
                    message=f"Tool: {getattr(tool, 'name', 'unknown')}, Error: {str(e)}"
                )
                continue
        
        # Combine all tools
        all_tools = base_tools + sub_agents_tools + sanitized_dynamic_tools + kb_tools
        
        # Validate for duplicate names
        tool_names = [tool.name for tool in all_tools if hasattr(tool, 'name')]
        if len(tool_names) != len(set(tool_names)):
            frappe.log_error(
                title="Duplicate tool names detected",
                message=f"Tool names: {tool_names}"
            )
            # Deduplicate by keeping the first occurrence
            seen = set()
            unique_tools = []
            for tool in all_tools:
                if hasattr(tool, 'name') and tool.name not in seen:
                    seen.add(tool.name)
                    unique_tools.append(tool)
            return unique_tools
        
        return all_tools
    
    def invoke(self, query=None, **kwargs):
        """
        Override invoke to ensure sub-agents work correctly.
        Sub-agents are already converted to tools, so just call parent invoke.
        """
        return super().invoke(query=query, **kwargs)

