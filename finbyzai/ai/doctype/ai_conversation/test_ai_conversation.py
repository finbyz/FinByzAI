# Copyright (c) 2025, AI CRM and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now

from finbyzai.ai.doctype.ai_conversation.ai_conversation import AIConversation


class TestAIConversation(FrappeTestCase):
    def setUp(self):
        """Set up test data"""
        frappe.set_user("Administrator")
        # Create a test user if not exists
        if not frappe.db.exists("User", "test@example.com"):
            user = frappe.new_doc("User")
            user.email = "test@example.com"
            user.first_name = "Test"
            user.last_name = "User"
            user.insert()
        
        # Create a test AI Agent if not exists
        if not frappe.db.exists("AI Agent", "Test Agent"):
            agent = frappe.new_doc("AI Agent")
            agent.title = "Test Agent"
            agent.agent_type = "LangChain Chain"
            agent.insert()

        self.agent_name = frappe.db.get_value(
            "AI Agent", {"title": "Test Agent"}, "name"
        )
        frappe.set_user("test@example.com")
    
    def test_create_conversation(self):
        """Test creating a new conversation"""
        conversation = frappe.new_doc("AI Conversation")
        conversation.title = "Test Conversation"
        conversation.user = "test@example.com"
        conversation.agent = self.agent_name
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.insert()
        
        self.assertEqual(conversation.title, "Test Conversation")
        self.assertEqual(conversation.user, "test@example.com")
        self.assertEqual(conversation.agent, self.agent_name)
        self.assertEqual(conversation.status, "Active")
        self.assertEqual(conversation.message_count, 0)
    
    def test_add_message(self):
        """Test adding messages to conversation"""
        conversation = frappe.new_doc("AI Conversation")
        conversation.title = "Test Conversation with Messages"
        conversation.user = "test@example.com"
        conversation.agent = self.agent_name
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.insert()
        
        # Add a human message
        conversation.add_message("human", "Hello, how are you?")
        
        # Add an assistant message
        conversation.add_message("assistant", "I'm doing well, thank you!")
        
        # Reload to get updated data
        conversation.reload()
        
        self.assertEqual(conversation.message_count, 2)
        self.assertEqual(len(conversation.messages), 2)
        self.assertEqual(conversation.messages[0].message_type, "human")
        self.assertEqual(conversation.messages[0].content, "Hello, how are you?")
        self.assertEqual(conversation.messages[1].message_type, "assistant")
        self.assertEqual(conversation.messages[1].content, "I'm doing well, thank you!")
    
    def test_conversation_summary(self):
        """Test getting conversation summary"""
        conversation = frappe.new_doc("AI Conversation")
        conversation.title = "Test Conversation Summary"
        conversation.user = "test@example.com"
        conversation.agent = self.agent_name
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.insert()
        
        # Add various message types
        conversation.add_message("human", "Hello")
        conversation.add_message("assistant", "Hi there!")
        conversation.add_message("system", "System message")
        conversation.add_message("human", "How are you?")
        conversation.add_message("assistant", "I'm fine, thanks!")
        
        conversation.reload()
        summary = conversation.get_conversation_summary()
        
        self.assertEqual(summary["total_messages"], 5)
        self.assertEqual(summary["human_messages"], 2)
        self.assertEqual(summary["assistant_messages"], 2)
        self.assertEqual(summary["system_messages"], 1)
        self.assertGreaterEqual(summary["duration_minutes"], 0)
    
    def test_conversation_status_changes(self):
        """Test changing conversation status"""
        conversation = frappe.new_doc("AI Conversation")
        conversation.title = "Test Status Changes"
        conversation.user = "test@example.com"
        conversation.agent = self.agent_name
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.insert()
        
        # Test pause
        conversation.pause_conversation()
        self.assertEqual(conversation.status, "Paused")
        
        # Test resume
        conversation.resume_conversation()
        self.assertEqual(conversation.status, "Active")
        
        # Test complete
        conversation.complete_conversation()
        self.assertEqual(conversation.status, "Completed")
        self.assertIsNotNone(conversation.end_time)
        
        # Test archive
        conversation.archive_conversation()
        self.assertEqual(conversation.status, "Archived")
    
    def test_message_search(self):
        """Test searching messages"""
        conversation = frappe.new_doc("AI Conversation")
        conversation.title = "Test Message Search"
        conversation.user = "test@example.com"
        conversation.agent = self.agent_name
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.insert()
        
        # Add messages
        conversation.add_message("human", "Hello world")
        conversation.add_message("assistant", "Hello there!")
        conversation.add_message("human", "How is the weather?")
        conversation.add_message("assistant", "The weather is nice today")
        
        conversation.reload()
        
        # Search for "hello"
        results = conversation.search_messages("hello")
        self.assertEqual(len(results), 2)
        
        # Search for "weather"
        results = conversation.search_messages("weather")
        self.assertEqual(len(results), 2)
        
        # Search by message type
        results = conversation.search_messages("hello", "human")
        self.assertEqual(len(results), 1)
    
    def test_static_methods(self):
        """Test static methods for creating and retrieving conversations"""
        # Test create_conversation
        conversation = AIConversation.create_conversation(
            user="test@example.com",
            agent=self.agent_name,
            title="Static Test Conversation"
        )
        
        self.assertEqual(conversation.title, "Static Test Conversation")
        self.assertEqual(conversation.user, "test@example.com")
        self.assertEqual(conversation.agent, self.agent_name)
        self.assertEqual(conversation.status, "Active")
        
        # Test get_user_conversations
        conversations = AIConversation.get_user_conversations("test@example.com")
        self.assertGreaterEqual(len(conversations), 1)
        
        # Test get_agent_conversations
        conversations = AIConversation.get_agent_conversations(self.agent_name)
        self.assertGreaterEqual(len(conversations), 1)
    
    def tearDown(self):
        """Clean up test data"""
        frappe.set_user("Administrator")
        frappe.db.delete("AI Conversation", {"user": "test@example.com"})
        frappe.db.commit()
        
