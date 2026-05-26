from typing import List, Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import Tool
from langgraph.prebuilt import create_react_agent
from langchain_core.tools.base import BaseTool
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel
from json_schema_to_pydantic import create_model
import json


class ReactAgent:
    """Compatibility wrapper to invoke LangGraph ReAct agent like LangChain AgentExecutor.

    Usage:
        exec = ReactAgentExecutor(llm, tools, prompt=optional_prompt)
        result = exec.invoke({"input": "hi", "chat_history": [...], "system": "You are helpful"})
    """

    def __init__(
        self,
        llm,
        tools: List[BaseTool] | List[Tool],
        *,
        prompt: Optional[ChatPromptTemplate] | Optional[str] | None = None,
        name: Optional[str] = None,
        response_format: Optional[Any] = None,
        pre_model_hook: Optional[Any] = None,
        post_model_hook: Optional[Any] = None,
        version: str = "v2",
        output_schema: Optional[str] = None,  # Add output_schema parameter
    ) -> None:
        self._prompt = prompt or ChatPromptTemplate.from_messages([("human", "{input}")])
        
        # Handle output_schema similar to your existing pattern
        self._output_parser = None
        if output_schema:
            dynamic_model = create_model(
                schema=json.loads(output_schema)
            )
            self._output_parser = PydanticOutputParser(pydantic_object=dynamic_model)
            self._pydantic_model = dynamic_model
        elif response_format:
            # If response_format is provided directly, use it
            if isinstance(response_format, dict):
                self._pydantic_model = create_model('ResponseModel', response_format)
                self._output_parser = PydanticOutputParser(pydantic_object=self._pydantic_model)
            elif isinstance(response_format, type) and issubclass(response_format, BaseModel):
                self._pydantic_model = response_format
                self._output_parser = PydanticOutputParser(pydantic_object=response_format)
            else:
                self._pydantic_model = None
                self._output_parser = None
        else:
            self._pydantic_model = None
            self._output_parser = None
        
        self._graph = create_react_agent(
            model=llm,
            tools=tools,
            prompt=None,
            name=name,
            response_format=response_format,
            pre_model_hook=pre_model_hook,
            post_model_hook=post_model_hook,
            version=version,
        )

    def _build_graph_input(self, input: dict, config: RunnableConfig | None = None) -> Dict[str, Any]:
        """Build the proper input format for the ReAct agent graph.
        
        Logic:
        1. Format the prompt template with input variables
        2. Check if there's a human message in the formatted messages
        3. If NO human message exists, add the query (from popup) as a human message
        4. Return properly formatted messages for the graph
        """
        # If input already has messages, use them directly
        if "messages" in input:
            return {"messages": input["messages"]}
        
        # Format using the prompt template (this includes system messages and any human messages defined)
        formatted_prompt = self._prompt.invoke(input, config)
        
        # Get messages from formatted prompt
        messages = formatted_prompt.messages if hasattr(formatted_prompt, 'messages') else []
        
        # Check if there's already a human message in the messages child table
        has_human_message = any(
            msg.type == "human" 
            for msg in messages
        )
        
        # If NO human message is defined in the child table, 
        # then use the query from popup as the human prompt
        if not has_human_message:
            query = input.get("input", "")
            if query:
                messages.append(HumanMessage(content=query))
        
        return {"messages": messages}

    def _parse_output_to_pydantic(self, output_text: str) -> Any:
        """Parse the output text into a Pydantic model using the output parser."""
        if not output_text or not self._output_parser:
            return output_text
        
        try:
            # Use the PydanticOutputParser to parse the output
            return self._output_parser.parse(output_text)
        except Exception as e:
            # Fallback: try manual JSON parsing
            try:
                # Extract JSON from the output text
                json_start = output_text.find('{')
                json_end = output_text.rfind('}') + 1
                
                if json_start != -1 and json_end != 0:
                    json_str = output_text[json_start:json_end]
                    json_data = json.loads(json_str)
                    return self._pydantic_model(**json_data)
                else:
                    # If no clear JSON boundaries, try parsing the whole text
                    json_data = json.loads(output_text)
                    return self._pydantic_model(**json_data)
            except (json.JSONDecodeError, ValueError, KeyError) as json_error:
                raise ValueError(f"Failed to parse output as Pydantic model: {e}. JSON parsing also failed: {json_error}")

    def invoke(self, input: dict, config: RunnableConfig | None = None, **kwargs: Any) -> BaseModel:
        """Invoke the ReAct agent and return Pydantic model directly."""
        if isinstance(input, str):
            input = {"input": input}
        
        # Build proper graph input
        graph_input = self._build_graph_input(input, config)
        
        # Invoke the graph
        result = self._graph.invoke(graph_input, config=config, **kwargs)
        
        # Extract output text
        output_text = result["messages"][-1].content if result.get("messages") else ""
        
        # Parse and return Pydantic model directly
        if self._output_parser and self._pydantic_model:
            return self._parse_output_to_pydantic(output_text)
        else:
            # If no output schema specified, return the raw output
            return output_text

    async def ainvoke(self, input: dict, config: RunnableConfig | None = None, **kwargs: Any) -> BaseModel:
        """Async invoke the ReAct agent and return Pydantic model directly."""
        if isinstance(input, str):
            input = {"input": input}
        
        # Build proper graph input (async version)
        if "messages" in input:
            graph_input = {"messages": input["messages"]}
        else:
            formatted_prompt = await self._prompt.ainvoke(input, config)
            messages = formatted_prompt.messages if hasattr(formatted_prompt, 'messages') else []
            
            # Check if there's a human message
            has_human_message = any(msg.type == "human" for msg in messages)
            
            # If no human message, add the query from popup
            if not has_human_message:
                query = input.get("input", "")
                if query:
                    messages.append(HumanMessage(content=query))
            
            graph_input = {"messages": messages}
        
        # Invoke the graph
        result = await self._graph.ainvoke(graph_input, config=config, **kwargs)
        
        # Extract output text
        output_text = result["messages"][-1].content if result.get("messages") else ""
        
        # Parse and return Pydantic model directly
        if self._output_parser and self._pydantic_model:
            return self._parse_output_to_pydantic(output_text)
        else:
            # If no output schema specified, return the raw output
            return output_text

    def stream(self, inputs: Any, config: RunnableConfig | None = None):
        """Stream the ReAct agent responses."""
        if isinstance(inputs, str):
            inputs = {"input": inputs}
        
        # Build proper graph input
        graph_input = self._build_graph_input(inputs, config)
        
        return self._graph.stream(graph_input, config=config)

    @property
    def output_parser(self):
        """Return the output parser if available, similar to your existing pattern."""
        return self._output_parser