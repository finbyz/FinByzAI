from typing import List, Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain.tools import Tool
from langgraph.prebuilt import create_react_agent
from langchain_core.tools.base import BaseTool
from langchain_core.runnables import RunnableConfig

    
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
    ) -> None:
        self._prompt = prompt or ChatPromptTemplate.from_messages([("human","{input}")])
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


    def invoke(self, input: dict,config:RunnableConfig | None = None, **kwargs: Any) -> Dict[str, Any]:
        if isinstance(input, str):
            input = {"input": input}
        input_prompt = self._prompt.invoke(input,config)
        result = self._graph.invoke(input_prompt.model_dump())
        output_text = result["messages"][-1].content if result.get("messages") else ""
        return {
            "output": output_text,
            "messages": result.get("messages", []),
            "intermediate_steps": result.get("messages", []),
        }

    async def ainvoke(self, input: dict,config:RunnableConfig | None = None, **kwargs: Any) -> Dict[str, Any]:
        if isinstance(input, str):
            input = {"input": input}
        input_prompt = await self._prompt.ainvoke(input,config)
        result = await self._graph.ainvoke(input_prompt.model_dump())
        output_text = result["messages"][-1].content if result.get("messages") else ""
        return {
            "output": output_text,
            "messages": result.get("messages", []),
            "intermediate_steps": result.get("messages", []),
        }


    def stream(self, inputs: Any):
        if isinstance(inputs, str):
            inputs = {"input": inputs}
        payload = self._build_payload(inputs)
        return self._graph.stream(payload)
