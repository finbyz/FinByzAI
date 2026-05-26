from langchain_classic.memory.chat_memory import BaseChatMemory
from langchain_core.messages import messages_from_dict, messages_to_dict
import frappe

class FrappeChatMemory(BaseChatMemory):
    def __init__(self, memory, conversation_name=None, agent_name=None):
        """
        Wrap a LangChain memory to store/load messages in Frappe AI Conversation.
        Commit automatically when destroyed.
        """
        super().__init__(return_messages=True)
        self.memory = memory
        self.agent_name = agent_name
        self.conversation_name = conversation_name or self._create_new_conversation()
        self._dirty = False

    def _create_new_conversation(self):
        doc = frappe.get_doc({
            "doctype": "AI Conversation",
            "agent": self.agent_name,
            "title": f"Conversation with {self.agent_name}",
            "user": frappe.session.user if frappe.session.user else None
        })
        doc.insert(ignore_permissions=True)
        return doc.name

    def load_memory_variables(self, inputs=None):
        doc = frappe.get_doc("AI Conversation", self.conversation_name)
        msgs = [{"role": m.role, "content": m.content} for m in doc.get("messages")]
        self.chat_memory.messages = messages_from_dict(msgs)
        return {self.memory_key: self.chat_memory.messages}

    def save_context(self, inputs, outputs):
        user_msg = inputs.get("input")
        ai_msg = outputs.get("output")

        if user_msg:
            self.chat_memory.add_user_message(user_msg)
            self._dirty = True
        if ai_msg:
            self.chat_memory.add_ai_message(ai_msg)
            self._dirty = True

    def commit(self):
        """Flush all messages to Frappe DB"""
        if not self._dirty:
            return
        doc = frappe.get_doc("AI Conversation", self.conversation_name)
        doc.set("messages", messages_to_dict(self.chat_memory.messages))
        doc.save(ignore_permissions=True)
        self._dirty = False

    def clear(self):
        self.chat_memory.clear()
        doc = frappe.get_doc("AI Conversation", self.conversation_name)
        doc.set("messages", [])
        doc.save(ignore_permissions=True)
        self._dirty = False

    def __del__(self):
        """Auto-commit when memory object is destroyed"""
        try:
            self.commit()
        except Exception as e:
            frappe.log_error(f"Failed to commit AI Conversation {self.conversation_name}: {e}")

    def __getattr__(self, name):
        return getattr(self.memory, name)
