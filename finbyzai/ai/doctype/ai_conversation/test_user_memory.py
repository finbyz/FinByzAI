import frappe
from frappe.tests.utils import FrappeTestCase
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.messages import AIMessage, HumanMessage

from finbyzai.ai.agent.agent_service import AgentService
from finbyzai.ai.memory.base import (
    FrappeChatMessageHistory,
    FrappeConversationSummaryMemory,
)


USER_A = "secure-memory-a@example.com"
USER_B = "secure-memory-b@example.com"
AGENT_NAME = "Test Secure Memory Agent"


class TestUserSpecificAgentMemory(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        for email, first_name in ((USER_A, "Memory A"), (USER_B, "Memory B")):
            if not frappe.db.exists("User", email):
                frappe.get_doc(
                    {
                        "doctype": "User",
                        "email": email,
                        "first_name": first_name,
                        "enabled": 1,
                        "send_welcome_email": 0,
                    }
                ).insert(ignore_permissions=True)

        if not frappe.db.exists("AI Agent", AGENT_NAME):
            frappe.get_doc(
                {
                    "doctype": "AI Agent",
                    "title": AGENT_NAME,
                    "agent_type": "LangChain Chain",
                }
            ).insert(ignore_permissions=True)

        frappe.db.delete("AI Conversation", {"user": ["in", [USER_A, USER_B]]})
        frappe.db.commit()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.delete("AI Conversation", {"user": ["in", [USER_A, USER_B]]})
        frappe.db.commit()

    def _agent_doc(self, memory_type="Buffer Memory"):
        agent = frappe.get_doc("AI Agent", AGENT_NAME)
        agent.enable_memory = 1
        agent.memory_type = memory_type
        return agent

    def test_history_survives_a_new_service_instance(self):
        frappe.set_user(USER_A)
        first_service = AgentService(self._agent_doc())
        first_memory = first_service.get_memory()
        first_memory.save_context(
            {"input": "My private value is alpha"},
            {"output": "I will remember alpha"},
        )
        conversation_id = first_service.conversation_id

        second_service = AgentService(self._agent_doc())
        second_memory = second_service.get_memory(conversation_id)
        messages = second_memory.load_memory_variables({})["chat_history"]

        self.assertEqual(second_service.conversation_id, conversation_id)
        self.assertEqual(
            [message.content for message in messages],
            ["My private value is alpha", "I will remember alpha"],
        )

    def test_other_user_cannot_read_write_or_clear_conversation(self):
        frappe.set_user(USER_A)
        history = FrappeChatMessageHistory(agent_name=AGENT_NAME)
        history.add_messages(
            [HumanMessage(content="User A secret"), AIMessage(content="Stored")]
        )
        conversation_id = history.conversation_name

        frappe.set_user(USER_B)
        with self.assertRaises(frappe.PermissionError):
            FrappeChatMessageHistory(
                agent_name=AGENT_NAME,
                conversation_name=conversation_id,
            )

        conversation = frappe.get_doc("AI Conversation", conversation_id)
        conversation.messages[0].content = "Tampered"
        with self.assertRaises(frappe.PermissionError):
            conversation.save(ignore_permissions=True)

        frappe.set_user(USER_A)
        reloaded = FrappeChatMessageHistory(
            agent_name=AGENT_NAME,
            conversation_name=conversation_id,
        )
        self.assertEqual(reloaded.messages[0].content, "User A secret")

    def test_window_memory_is_persistent_and_bounded(self):
        frappe.set_user(USER_A)
        service = AgentService(self._agent_doc("Window Memory"))
        memory = service.get_memory()
        for index in range(6):
            memory.save_context(
                {"input": f"question-{index}"},
                {"output": f"answer-{index}"},
            )

        fresh_service = AgentService(self._agent_doc("Window Memory"))
        variables = fresh_service.get_memory(
            service.conversation_id
        ).load_memory_variables({})
        messages = variables["chat_history"]

        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[0].content, "question-1")
        self.assertEqual(messages[-1].content, "answer-5")

    def test_summary_survives_a_new_memory_instance(self):
        frappe.set_user(USER_A)
        history = FrappeChatMessageHistory(agent_name=AGENT_NAME)
        memory = FrappeConversationSummaryMemory(
            chat_memory=history,
            llm=FakeListLLM(responses=["Durable private summary"]),
            memory_key="chat_history",
            return_messages=True,
            conversation_name=history.conversation_name,
        )
        memory.save_context(
            {"input": "Remember this privately"},
            {"output": "Remembered"},
        )

        fresh_history = FrappeChatMessageHistory(
            agent_name=AGENT_NAME,
            conversation_name=history.conversation_name,
        )
        fresh_memory = FrappeConversationSummaryMemory(
            chat_memory=fresh_history,
            llm=FakeListLLM(responses=["unused"]),
            memory_key="chat_history",
            return_messages=True,
            conversation_name=history.conversation_name,
        )
        self.assertEqual(fresh_memory.buffer, "Durable private summary")

    def test_guest_cannot_create_memory(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.AuthenticationError):
            FrappeChatMessageHistory(agent_name=AGENT_NAME)
