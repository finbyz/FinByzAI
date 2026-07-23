import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now, time_diff_in_seconds


def _get_authenticated_user(user=None):
    user = user or getattr(frappe.session, "user", None)
    if not user or user == "Guest":
        frappe.throw(
            "Authentication is required to access AI conversations",
            frappe.AuthenticationError,
        )
    return user


class AIConversation(Document):
    """
    AI Conversation DocType to store conversation history between users and AI agents.
    
    Features:
    - Track conversation sessions with start/end times
    - Store message history in a structured format
    - Link conversations to specific users and AI agents
    - Track conversation status and metadata
    - Automatic message count calculation
    """
    
    def before_insert(self):
        """Force new conversations to belong to the authenticated user."""
        self.user = _get_authenticated_user()
        if not self.session_id:
            self.session_id = frappe.generate_hash(length=32)

    def validate(self):
        """Validate the conversation document and immutable ownership."""
        current_user = _get_authenticated_user()
        if self.user != current_user:
            frappe.throw(
                "You are not permitted to access this conversation",
                frappe.PermissionError,
            )
        if not self.is_new():
            stored_user = frappe.db.get_value("AI Conversation", self.name, "user")
            if stored_user and stored_user != self.user:
                frappe.throw(
                    "Conversation ownership cannot be changed",
                    frappe.PermissionError,
                )

        # Set start time if not provided
        if not self.start_time:
            self.start_time = now()
        
        # Update message count
        self.message_count = len(self.messages) if self.messages else 0
        
        # Validate end time
        if self.end_time and self.start_time:
            if get_datetime(self.end_time) < get_datetime(self.start_time):
                frappe.throw("End time cannot be before start time")
        
        # Auto-complete conversation if end time is set
        if self.end_time and self.status == "Active":
            self.status = "Completed"
    
    def _assert_owner(self):
        if self.user != _get_authenticated_user():
            frappe.throw(
                "You are not permitted to access this conversation",
                frappe.PermissionError,
            )

    def add_message(self, message_type, content, metadata=None):
        """Append and immediately persist a message for the owning user."""
        self._assert_owner()
        self.append(
            "messages",
            {
                "message_type": message_type,
                "content": content,
                "timestamp": now(),
                "metadata": frappe.as_json(metadata) if metadata else None,
            },
        )
        self.save(ignore_permissions=True)

    def get_conversation_summary(self):
        """Return owner-scoped conversation statistics."""
        self._assert_owner()
        counts = {"human": 0, "assistant": 0, "system": 0}
        for message in self.messages or []:
            if message.message_type in counts:
                counts[message.message_type] += 1
        end_time = self.end_time or now()
        duration = max(
            time_diff_in_seconds(end_time, self.start_time or end_time) / 60,
            0,
        )
        return {
            "total_messages": len(self.messages or []),
            "human_messages": counts["human"],
            "assistant_messages": counts["assistant"],
            "system_messages": counts["system"],
            "duration_minutes": duration,
        }

    def search_messages(self, search_text, message_type=None):
        """Search only this owning user's in-memory child rows."""
        self._assert_owner()
        needle = (search_text or "").casefold()
        return [
            {
                "message_type": message.message_type,
                "content": message.content,
                "timestamp": message.timestamp,
                "metadata": message.metadata,
            }
            for message in self.messages or []
            if needle in (message.content or "").casefold()
            and (not message_type or message.message_type == message_type)
        ]

    def pause_conversation(self):
        self._set_status("Paused")

    def resume_conversation(self):
        self._set_status("Active", clear_end_time=True)

    def complete_conversation(self):
        self._set_status("Completed", end_time=now())

    def archive_conversation(self):
        self._set_status("Archived")

    def _set_status(self, status, end_time=None, clear_end_time=False):
        self._assert_owner()
        self.status = status
        if clear_end_time:
            self.end_time = None
        elif end_time:
            self.end_time = end_time
        self.save(ignore_permissions=True)

    @staticmethod
    def create_conversation(user=None, agent=None, title=None):
        """Create a conversation owned by the authenticated user."""
        current_user = _get_authenticated_user()
        if user and user != current_user:
            frappe.throw(
                "You cannot create a conversation for another user",
                frappe.PermissionError,
            )
        if not agent:
            frappe.throw("AI Agent is required")
        if not title:
            title = (
                f"Conversation with {agent} - "
                f"{frappe.generate_hash(length=12)}"
            )

        conversation = frappe.new_doc("AI Conversation")
        conversation.title = title
        conversation.user = current_user
        conversation.agent = agent
        conversation.start_time = now()
        conversation.status = "Active"
        conversation.session_id = frappe.generate_hash(length=32)
        conversation.insert(ignore_permissions=True)
        return conversation

    @staticmethod
    def get_user_conversations(user=None):
        current_user = _get_authenticated_user()
        if user and user != current_user:
            frappe.throw(
                "You cannot access another user's conversations",
                frappe.PermissionError,
            )
        return frappe.get_all(
            "AI Conversation",
            filters={"user": current_user},
            fields=["name", "title", "agent", "status", "modified"],
            order_by="modified desc",
            ignore_permissions=True,
        )

    @staticmethod
    def get_agent_conversations(agent):
        return frappe.get_all(
            "AI Conversation",
            filters={"user": _get_authenticated_user(), "agent": agent},
            fields=["name", "title", "agent", "status", "modified"],
            order_by="modified desc",
            ignore_permissions=True,
        )


def get_permission_query_conditions(user=None):
    """Restrict Desk lists and link searches to the current user's records."""
    user = user or getattr(frappe.session, "user", None)
    if not user or user == "Guest":
        return "1=0"
    return f"`tabAI Conversation`.`user` = {frappe.db.escape(user)}"


def has_permission(doc, user=None, permission_type=None):
    """Prevent direct document access to another user's conversation."""
    user = user or getattr(frappe.session, "user", None)
    if not user or user == "Guest":
        return False
    if permission_type == "create" and not getattr(doc, "user", None):
        return True
    return doc.user == user
