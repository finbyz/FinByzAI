from enum import Enum
import json
from typing import Union
from finbyzai.ai.agent.react_agent import ReactAgent
from finbyzai.ai.agent.structrued_agent import create_structured_agent
import frappe
from frappe.model.document import Document
from langchain_litellm.chat_models import ChatLiteLLM
from langchain_core.output_parsers import PydanticOutputParser
from langchain.schema import StrOutputParser
from langchain.prompts import ChatPromptTemplate
from json_schema_to_pydantic import create_model
from langgraph.prebuilt import create_react_agent
from langchain.agents import AgentType, initialize_agent
from langchain.memory import (
    ConversationBufferMemory,
    VectorStoreRetrieverMemory,
    ConversationSummaryMemory,
    ConversationBufferWindowMemory,
)
import warnings

import os

os.environ["LANGSMITH_API_KEY"] = "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"
os.environ["LANGSMITH_TRACING"] = "true"


class AgentTypes(Enum):
    LANGCHAIN_CHAIN = "LangChain Chain"
    CONVERSATIONAL_AGENT = "Conversational Agent"
    REACT_AGENT = "ReAct Agent"
    STRUCTURED_CHAT_AGENT = "Structured Chat Agent"
    IMAGE_GENERATION_AGENT = "Image Generation Agent"
    GEMINI_CACHE_AGENT = "Gemini Cache Agent"


class AgentService:
    """
    Enhanced AI Agent with capabilities:
    - Multiple agent types with specialized behaviors
    - Advanced memory management
    - Tool integration and workflow capabilities
    - Conversation context and state management
    - Structured output and response formatting
    """

    def __init__(self, agent: Union[str, Document]) -> None:
        """
        Initialize the service with either an agent name or a loaded AIAgent doc.

        Args:
            agent (str | Document): The AI Agent name or a Frappe Document instance
        """
        self._agent_instance = None
        self._memory = None
        self._is_basic_chain = False
        try:
            # Accept both name and Document
            if isinstance(agent, str):
                self.agent_doc = frappe.get_doc("AI Agent", agent)
            else:
                self.agent_doc = agent
        except Exception as e:
            frappe.log_error(f"Failed to initialize AgentService", e)
            raise

    @property
    def chat_messages(self):
        """Convert stored messages to LangChain message format"""
        messages = []
        for msg in self.agent_doc.messages:
            messages.append((msg.type, msg.content))

        if self.agent_doc.output_schema:
            for i, (mtype, content) in enumerate(messages):
                if mtype == "system":
                    messages[i] = (mtype, content + "\n\n{format_instructions}")
                    break
            else:
                message_type = (
                    "system"
                    if self.agent_doc.agent_type != "Gemini Cache Agent"
                    else "human"
                )
                messages.insert(0, (message_type, "{format_instructions}"))

        return messages

    def get_output_parser(self):
        if self.agent_doc.output_schema:
            dynamic_model = create_model(
                schema=json.loads(self.agent_doc.output_schema)
            )
            output_parser = PydanticOutputParser(pydantic_object=dynamic_model)
            return output_parser
        return None
    
    def get_memory(self):
        """Initialize memory with persistent context in Frappe"""
        if not self.agent_doc.enable_memory:
            return None
        if self._memory:
            return self._memory

        base_memory = None
        if self.agent_doc.memory_type == "Buffer Memory":
            base_memory = ConversationBufferMemory(return_messages=True)
        elif self.agent_doc.memory_type == "Window Memory":
            base_memory = ConversationBufferWindowMemory(
                k=self.agent_doc.window_size or 5, return_messages=True
            )
        elif self.agent_doc.memory_type == "Summary Memory":
            base_memory = ConversationSummaryMemory(llm=self.get_llm())
        elif self.agent_doc.memory_type == "Vector Memory":
            base_memory = VectorStoreRetrieverMemory(
                retriever=self.get_vector_retriever()
            )
        self._memory = base_memory
        return base_memory

    def get_tools(self):
        """
        Return a list of LangChain Tool objects for all tools linked to this agent.
        Enhanced with tool management and workflow capabilities.
        """
        tools_list = []

        if self.agent_doc.tools:
            for ai_agent_tool in self.agent_doc.tools:
                tool = frappe.get_doc("AI Tool", ai_agent_tool.tool)
                tools_list.append(tool.get_tool())
        if self.agent_doc.knowledge_base:
            kb = frappe.get_doc("Knowledge Base", self.agent_doc.knowledge_base)
            vs = kb.get_vector_store()
            tools_list.append(vs.as_tool())

        return tools_list

    @property
    def agent(self):
        """
        Initialize and return an agent using the tools linked to this agent.
        Enhanced with agent types and memory support.
        """
        if self._agent_instance is not None:
            return self._agent_instance
        tools = self.get_tools()
        model = self.get_llm()

        if self.agent_doc.agent_type == AgentTypes.REACT_AGENT.value:
            memory = self.get_memory()
            agent = ReactAgent(
                llm=model,
                tools=tools,
                prompt=ChatPromptTemplate.from_messages(self.chat_messages),
                name=self.agent_doc.name,
                output_schema = self.agent_doc.output_schema
            )
            self._agent_instance = agent
            return agent
        elif self.agent_doc.agent_type == AgentTypes.CONVERSATIONAL_AGENT.value:
            memory = self.get_memory()
            return self._create_conversational_agent(tools, model, memory)
        elif self.agent_doc.agent_type == AgentTypes.STRUCTURED_CHAT_AGENT.value:
            return create_structured_agent(
                llm=model,
                tools=tools,
                prompt=ChatPromptTemplate.from_messages(self.chat_messages),
            )
        else:
            return self._create_basic_chain(model)

    def _create_langgraph_agent(self, tools, model, memory):
        """Create a LangGraph-based agent"""
        dynamic_model = None
        if self.agent_doc.output_schema:
            dynamic_model = create_model(
                schema=json.loads(self.agent_doc.output_schema)
            )

        messages = self.chat_messages
        prompt = ChatPromptTemplate.from_messages(
            [
                *messages,
            ]
        )

        agent = create_react_agent(
            tools=tools,
            prompt=prompt or None,
            model=model,
            name=self.agent_doc.name,
            response_format=dynamic_model,
        )
        self._agent_instance = agent
        return agent

    def _create_conversational_agent(self, tools, model, memory):
        """Create a conversational agent with enhanced memory"""
        if not memory:
            memory = ConversationBufferMemory(
                memory_key="chat_history", return_messages=True, output_key="output"
            )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            agent = initialize_agent(
                tools=tools,
                llm=model,
                agent=AgentType.CONVERSATIONAL_REACT_DESCRIPTION,
                memory=memory,
                verbose=self.agent_doc.verbose_mode,
                max_iterations=self.agent_doc.max_iterations or 25,
            )
        self._agent_instance = agent
        return agent

    def _create_basic_chain(self, model):
        """Create a simple LangChain chain as a safe fallback when agent type is not defined."""
        dynamic_model = None
        if self.agent_doc.output_schema:
            dynamic_model = create_model(
                schema=json.loads(self.agent_doc.output_schema)
            )

        messages = self.chat_messages
        # Ensure the chain consumes the dynamic query input
        messages.append(("human", "{query}"))
        prompt = ChatPromptTemplate.from_messages(
            [
                *messages,
            ]
        )

        if dynamic_model:
            output_parser = PydanticOutputParser(pydantic_object=dynamic_model)
        else:
            output_parser = StrOutputParser()

        chain = prompt | model | output_parser
        self._agent_instance = chain
        self._is_basic_chain = True
        return chain

    def get_llm(self) -> ChatLiteLLM:
        """Get the LLM instance for this agent."""
        try:
            if self.agent_doc.agent_type == AgentTypes.GEMINI_CACHE_AGENT.value:
                cache_doc = frappe.get_doc("Gemini Cache", self.agent_doc.gemini_cache)
                llm = cache_doc._llm
            else:
                llm_doc = frappe.get_doc("LLM", self.agent_doc.llm)
                llm = llm_doc.llm
            return llm
        except Exception as e:
            frappe.log_error(f"Failed to get LLM for agent {self.agent_doc.name}", e)
            raise

    def get_vector_retriever(self):
        """Get vector retriever for vector memory."""
        try:
            if (
                hasattr(self.agent_doc, "knowledge_base")
                and self.agent_doc.knowledge_base
            ):
                kb = frappe.get_doc("Knowledge Base", self.agent_doc.knowledge_base)
                return kb.get_vector_store().as_retriever()
            else:
                frappe.throw("Knowledge base is required for vector memory")
        except Exception as e:
            frappe.log_error(f"Failed to get vector retriever", e)
            raise

    def invoke(self, query=None, **kwargs):
        """
        Invoke the AI agent with automatic memory and session management.

        Args:
            query (str): The user query
            kwargs (dict, optional): Additional context and parameters
        """
        try:
            if self.agent_doc.agent_type == AgentTypes.IMAGE_GENERATION_AGENT.value:
                return self._invoke_image_generation(query, **kwargs)
            elif self.agent_doc.agent_type in [
                AgentTypes.REACT_AGENT.value,
                AgentTypes.CONVERSATIONAL_AGENT.value,
                AgentTypes.STRUCTURED_CHAT_AGENT.value,
            ]:
                return self._invoke_with_agent_executor(query, **kwargs)
            else:
                if getattr(self, "_is_basic_chain", False):
                    return self._invoke_basic_chain(query, **kwargs)
                return self._invoke_langgraph_agent(query, **kwargs)
        except Exception as e:
            frappe.log_error(
                title=f"Error invoking agent {self.agent_doc.name}", message=e
            )
            raise e

    def _invoke_image_generation(self, query, **kwargs):
        """Handle image generation agent"""
        llm = self.get_llm()
        messages = self.chat_messages
        if query:
            messages.append(("human", "{query}"))

        prompt = ChatPromptTemplate.from_messages(
            [
                *messages,
            ]
        )

        input_vars = {"query": query, **kwargs}
        image_generation_prompt = prompt.invoke(input_vars)
        return llm.invoke(image_generation_prompt)

    def _invoke_with_agent_executor(self, query, **kwargs):
        """Invoke agent using AgentExecutor with automatic memory management"""
        try:
            agent = self.agent
            memory = self.get_memory()

            # Prepare input data
            input_data = {"input": query, "format_instructions": ""}
            if output_parser := self.get_output_parser():
                input_data["format_instructions"] = output_parser.get_format_instructions()
            
            if memory:
                memory_vars = memory.load_memory_variables(input_data)
                input_data.update(memory_vars)
            input_data.update(kwargs)

            
            response = agent.invoke(input_data)
            # Auto-save to memory
            if memory and query and response:
                memory.save_context(
                    {"input": query},
                    {
                        "output": (
                            response.get("output", response)
                            if isinstance(response, dict)
                            else response
                        )
                    },
                )

            return response

        except Exception as e:
            frappe.log_error(f"Error in _invoke_with_agent_executor", e)
            raise

    def _invoke_langgraph_agent(self, query, **kwargs):
        """Invoke LangGraph-based agent with automatic memory management"""
        try:
            llm = self.get_llm()
            memory = self.get_memory()

            # Load memory context
            memory_context = []
            if memory:
                memory_vars = memory.load_memory_variables({"input": query})
                memory_context = memory_vars.get("chat_history", [])

            messages = self.chat_messages

            # Add memory context to messages
            if memory_context:
                for msg in memory_context:
                    if hasattr(msg, "content"):
                        if (
                            hasattr(msg, "__class__")
                            and "Human" in msg.__class__.__name__
                        ):
                            messages.append(("human", msg.content))
                        elif (
                            hasattr(msg, "__class__") and "AI" in msg.__class__.__name__
                        ):
                            messages.append(("assistant", msg.content))
                        elif (
                            hasattr(msg, "__class__")
                            and "System" in msg.__class__.__name__
                        ):
                            messages.append(("system", msg.content))

            if query:
                messages.append(("human", "{query}"))

            prompt = ChatPromptTemplate.from_messages([*messages])

            # Handle output schema
            dynamic_model = None
            if self.agent_doc.output_schema:
                dynamic_model = create_model(
                    schema=json.loads(self.agent_doc.output_schema)
                )

            format_instructions = ""
            if dynamic_model:
                output_parser = PydanticOutputParser(pydantic_object=dynamic_model)
                format_instructions = output_parser.get_format_instructions()
            else:
                output_parser = StrOutputParser()

            chain = prompt | llm | output_parser

            input_vars = {
                "format_instructions": format_instructions,
                "query": query,
                **kwargs,
            }
            response = chain.invoke(input_vars)

            # Auto-save to memory
            if memory and query and response:
                memory.save_context({"input": query}, {"output": response})

            return response

        except Exception as e:
            frappe.log_error(f"Error in _invoke_langgraph_agent", e)
            raise

    def _invoke_basic_chain(self, query, **kwargs):
        """Invoke the cached basic chain fallback and handle memory save."""
        try:
            chain = self.agent

            format_instructions = ""
            if self.agent_doc.output_schema:
                dynamic_model = create_model(
                    schema=json.loads(self.agent_doc.output_schema)
                )
                output_parser = PydanticOutputParser(pydantic_object=dynamic_model)
                format_instructions = output_parser.get_format_instructions()

            input_vars = {
                "format_instructions": format_instructions,
                "query": query,
                **kwargs,
            }

            response = chain.invoke(input_vars)

            return response
        except Exception as e:
            frappe.log_error(f"Error in _invoke_basic_chain", e)
            raise
