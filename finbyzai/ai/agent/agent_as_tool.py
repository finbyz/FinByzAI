from typing import Any
from langchain.tools import BaseTool
from pydantic import Field, create_model
import re
from langchain.prompts import ChatPromptTemplate


def sanitize(name: str) -> str:
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    sanitized = re.sub(r'[-_]+', '_', sanitized)
    if sanitized and not sanitized[0].isalpha():
        sanitized = 't_' + sanitized
    if len(sanitized) > 50:
        sanitized = sanitized[:50]
    return sanitized or 'agent_tool'


class AgentAsTool(BaseTool):
    """Tool that wraps any AI Agent as a reusable tool for other agents."""
    
    agent_service: Any = Field(
        description="Reference to the AgentService instance",
        exclude=True
    )
    
    def get_input_schema(self, config=None):
        """
        Generate input schema dynamically from the agent's chat messages.
        This extracts template variables from the messages to create the schema.
        """
        try:
            # Get the chat messages from the agent service
            messages = self.agent_service.chat_messages
            
            # Create a prompt template to extract input variables
            prompt = ChatPromptTemplate.from_messages(messages)
            
            # Get the input variables from the prompt
            input_variables = prompt.input_variables
            
            # Build a dynamic Pydantic model based on these variables
            if input_variables:
                fields = {
                    var: (str, Field(description=f"Input for {var}"))
                    for var in input_variables
                    if var != "format_instructions"  # Exclude internal variables
                }
                
                if fields:
                    return create_model(
                        f"{sanitize(self.name)}_input",
                        **fields
                    )
            
            return create_model(
                f"{sanitize(self.name)}_input",
                query=(str, Field(description="Query or input for the agent"))
            )
            
        except Exception as e:
            return create_model(
                "AgentInput",
                query=(str, Field(description="Query or input for the agent"))
            )
    
    def _run(self, **kwargs) -> str:
        """
        Execute the agent with the given input.
        
        The kwargs will contain all the input variables defined in the schema.
        """
        try:
            if len(kwargs) == 1 and 'query' in kwargs:
                result = self.agent_service.invoke(query=kwargs['query'])
            else:
                query = kwargs.pop('query', None)
                result = self.agent_service.invoke(query=query, **kwargs)
            
            if isinstance(result, dict):
                if 'output' in result:
                    return str(result['output'])
                elif 'result' in result:
                    return str(result['result'])
                else:
                    return str(result)
            
            return str(result)
            
        except Exception as e:
            return f"Error executing agent tool '{self.name}': {str(e)}"
    
    async def _arun(self, **kwargs) -> str:
        """
        Async execute the agent with the given input.
        
        Note: AgentService doesn't have async invoke yet, so we fall back to sync.
        """
        try:
            # Check if agent_service has async support
            if hasattr(self.agent_service, 'ainvoke'):
                if len(kwargs) == 1 and 'query' in kwargs:
                    result = await self.agent_service.ainvoke(query=kwargs['query'])
                else:
                    query = kwargs.pop('query', None)
                    result = await self.agent_service.ainvoke(query=query, **kwargs)
            else:
                # Fallback to sync invoke
                return self._run(**kwargs)
            
            # Handle different result types
            if isinstance(result, dict):
                if 'output' in result:
                    return str(result['output'])
                elif 'result' in result:
                    return str(result['result'])
                else:
                    return str(result)
            
            return str(result)
            
        except Exception as e:
            return f"Error executing agent tool '{self.name}': {str(e)}"
