import json
from collections.abc import Sequence
from typing import Any

import frappe
from frappe.utils import now, now_datetime
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_classic.memory import ConversationSummaryMemory


_MESSAGE_TYPE_TO_CLASS = {
    "human": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
}
_LANGCHAIN_TYPE_TO_MESSAGE_TYPE = {
    "human": "human",
    "ai": "assistant",
    "system": "system",
}


class FrappeChatMessageHistory(BaseChatMessageHistory):
    """Persist one authenticated user's agent conversation in Frappe."""

    def __init__(self, agent_name: str, conversation_name: str | None = None):
        self.agent_name = agent_name
        self.user = self._get_authenticated_user()
        self.conversation_name = (
            self._validate_conversation(conversation_name).name
            if conversation_name
            else self._create_conversation().name
        )

    @staticmethod
    def _get_authenticated_user() -> str:
        user = getattr(frappe.session, "user", None)
        if not user or user == "Guest":
            frappe.throw(
                "Authentication is required to use agent memory",
                frappe.AuthenticationError,
            )
        return user

    def _validate_conversation(self, conversation_name: str):
        if not frappe.db.exists("AI Conversation", conversation_name):
            frappe.throw("Conversation not found", frappe.DoesNotExistError)

        conversation = frappe.get_doc("AI Conversation", conversation_name)
        if conversation.user != self.user:
            frappe.throw(
                "You are not permitted to access this conversation",
                frappe.PermissionError,
            )
        if conversation.agent != self.agent_name:
            frappe.throw(
                "This conversation belongs to a different AI Agent",
                frappe.PermissionError,
            )
        return conversation

    def _get_conversation(self):
        return self._validate_conversation(self.conversation_name)

    def _create_conversation(self):
        unique_suffix = frappe.generate_hash(length=12)
        conversation = frappe.get_doc(
            {
                "doctype": "AI Conversation",
                "title": f"Conversation with {self.agent_name} - {unique_suffix}",
                "user": self.user,
                "agent": self.agent_name,
                "status": "Active",
                "start_time": now(),
                "session_id": frappe.generate_hash(length=32),
            }
        )
        conversation.insert(ignore_permissions=True)
        return conversation

    @property
    def messages(self) -> list[BaseMessage]:
        conversation = self._get_conversation()
        result = []
        for row in conversation.messages or []:
            message_class = _MESSAGE_TYPE_TO_CLASS.get(row.message_type)
            if not message_class:
                continue

            metadata = frappe.parse_json(row.metadata) if row.metadata else {}
            content: Any = row.content
            if metadata.pop("content_is_json", False):
                content = json.loads(content)

            result.append(
                message_class(
                    content=content,
                    additional_kwargs=metadata.get("additional_kwargs") or {},
                    response_metadata=metadata.get("response_metadata") or {},
                    name=metadata.get("name"),
                    id=metadata.get("id"),
                )
            )
        return result

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        if not messages:
            return

        conversation = self._get_conversation()
        for message in messages:
            message_type = _LANGCHAIN_TYPE_TO_MESSAGE_TYPE.get(message.type)
            if not message_type:
                frappe.throw(f"Unsupported memory message type: {message.type}")

            content = message.content
            content_is_json = not isinstance(content, str)
            if content_is_json:
                content = frappe.as_json(content)

            metadata = {
                "content_is_json": content_is_json,
                "additional_kwargs": message.additional_kwargs,
                "response_metadata": message.response_metadata,
                "name": message.name,
                "id": message.id,
            }
            conversation.append(
                "messages",
                {
                    "message_type": message_type,
                    "content": content,
                    "timestamp": now_datetime(),
                    "metadata": frappe.as_json(metadata),
                },
            )

        # Ownership and agent were checked above, so bypassing role permissions here
        # does not allow a caller to write another user's conversation.
        conversation.save(ignore_permissions=True)

    def clear(self) -> None:
        conversation = self._get_conversation()
        conversation.set("messages", [])
        conversation.save(ignore_permissions=True)


class FrappeConversationSummaryMemory(ConversationSummaryMemory):
    """Conversation summary memory whose summary survives process boundaries."""

    conversation_name: str

    def __init__(self, **data):
        conversation_name = data["conversation_name"]
        conversation = data["chat_memory"]._validate_conversation(conversation_name)
        data.setdefault("buffer", conversation.memory_summary or "")
        super().__init__(**data)

    def _save_summary(self) -> None:
        conversation = self.chat_memory._validate_conversation(self.conversation_name)
        conversation.memory_summary = self.buffer
        conversation.save(ignore_permissions=True)

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        super().save_context(inputs, outputs)
        self._save_summary()

    def clear(self) -> None:
        super().clear()
        self._save_summary()
