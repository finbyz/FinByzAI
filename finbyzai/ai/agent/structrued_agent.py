from collections.abc import Sequence
from langchain_classic.agents import create_structured_chat_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.language_models import BaseLanguageModel
from langchain_core.tools import BaseTool
from langchain_core.tools.render import ToolsRenderer
from langchain_classic.tools.render import render_text_description_and_args


system = """You are a helpful AI assistant that responds with structured JSON output. You have access to the following tools:

{tools}

CRITICAL: You MUST respond with a valid JSON object that has exactly these keys: "action" and "action_input".

Valid "action" values: "Final Answer" or one of: {tool_names}

RESPONSE FORMAT (copy this structure exactly):

For using a tool:
```json
{{
    "action": "EXACT_TOOL_NAME",
    "action_input": {{
        "param1": "value1",
        "param2": "value2"
    }}
}}
```

For final answer:
```json
{{
    "action": "Final Answer",
    "action_input": "Your final response here"
}}
```

EXAMPLES:

Tool usage example:
```json
{{
    "action": "search",
    "action_input": {{
        "query": "python programming",
        "max_results": 5
    }}
}}
```

Final answer example:
```json
{{
    "action": "Final Answer",
    "action_input": "Based on my search, Python is a versatile programming language..."
}}
```

STRICT REQUIREMENTS:
1. Always include both "action" and "action_input" keys
2. Use exact tool names as listed
3. Match parameter names and types exactly
4. No extra text outside the JSON block
5. No markdown formatting around JSON
6. No comments in JSON

Process:
Question: input question to answer
Thought: analyze what needs to be done and which tool to use
Action: (JSON response as specified above)
Observation: result from action
... (repeat Thought/Action/Observation as needed)
Thought: I have sufficient information for final answer
Action: (JSON with "Final Answer")

Begin! Remember: ONLY respond with valid JSON containing "action" and "action_input" keys."""

human = """{input}

{agent_scratchpad}

REMINDER: Respond ONLY with valid JSON format:
{{
    "action": "tool_name_or_Final_Answer",
    "action_input": {{...}} or "string"
}}

{format_instructions}"""


structured_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", human),
    ]
)


def create_structured_agent(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool],
    prompt: ChatPromptTemplate,
    tools_renderer: ToolsRenderer = render_text_description_and_args,
    *,
    stop_sequence: bool | list[str] = True,
    max_iterations: int = 25,
    verbose: bool = False,
) -> AgentExecutor:
    if prompt:
        structured_prompt.messages[2:2] = prompt.messages
    chat_agent = create_structured_chat_agent(
        llm, tools, structured_prompt, tools_renderer, stop_sequence=stop_sequence
    )
    agent_executor = AgentExecutor(
        agent=chat_agent,
        tools=tools,
        max_iterations=max_iterations,
        verbose=verbose,
    )
    return agent_executor
