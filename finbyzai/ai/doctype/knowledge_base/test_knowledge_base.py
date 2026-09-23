# Copyright (c) 2025, Finbyz Tech Pvt Ltd and Contributors
# See license.txt

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from finbyzai.ai.api.knowledge_base_api import cleanup_detached_assets
from finbyzai.ai.doctype.knowledge_base.knowledge_base import (
    STATUS_COMPLETED,
    STATUS_QUEUE,
    KnowledgeBase,
    enqueue_knowledge_base,
    make_source_id,
    process_knowledge_base,
)


class TestKnowledgeBase(TestCase):
    def test_make_source_id_matches_index_metadata(self):
        self.assertEqual(
            make_source_id("support", "documents", "row-1"),
            "support_documents_row-1",
        )

    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.frappe.enqueue")
    def test_enqueue_runs_after_commit_and_deduplicates(self, enqueue):
        enqueue_knowledge_base("support")

        enqueue.assert_called_once_with(
            "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
            queue="long",
            timeout=3600,
            enqueue_after_commit=True,
            job_id="knowledge-base-process::support",
            deduplicate=True,
            kb_name="support",
        )

    @patch("finbyzai.ai.api.knowledge_base_api.frappe.get_doc")
    def test_cleanup_uses_vector_metadata_filter(self, get_doc):
        store = MagicMock()
        get_doc.return_value.get_vector_store.return_value = store

        cleanup_detached_assets(
            "support",
            source_id="support_documents_row-1",
        )

        store.delete.assert_called_once_with(
            {"source_id": "support_documents_row-1"}
        )

    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.enqueue_knowledge_base")
    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.frappe")
    def test_reprocess_resets_every_source_type(self, frappe, enqueue):
        set_value = frappe.db.set_value
        kb = SimpleNamespace(name="support", db_set=MagicMock())

        KnowledgeBase.reprocess_all_documents(kb)

        self.assertEqual(set_value.call_count, 3)
        for child_doctype in ("AI Links", "Knowledge Document", "AI Note"):
            set_value.assert_any_call(
                child_doctype,
                {"parent": "support"},
                "is_processed",
                0,
                update_modified=False,
            )
        kb.db_set.assert_called_once_with(
            "status", STATUS_QUEUE, update_modified=False
        )
        enqueue.assert_called_once_with("support")

    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.enqueue_knowledge_base")
    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.frappe")
    def test_process_queues_once_after_status_change(self, frappe, enqueue):
        frappe.db.exists.return_value = True
        kb = SimpleNamespace(
            status=STATUS_COMPLETED,
            check_permission=MagicMock(),
            db_set=MagicMock(),
        )
        frappe.get_doc.return_value = kb

        result = process_knowledge_base.__wrapped__("support")

        self.assertEqual(result["status"], "enqueued")
        kb.check_permission.assert_called_once_with("write")
        kb.db_set.assert_called_once_with(
            "status", STATUS_QUEUE, update_modified=False
        )
        enqueue.assert_called_once_with("support")

    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.enqueue_knowledge_base")
    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.frappe")
    def test_process_does_not_duplicate_queued_job(self, frappe, enqueue):
        frappe.db.exists.return_value = True
        frappe.get_doc.return_value = SimpleNamespace(
            status=STATUS_QUEUE,
            check_permission=MagicMock(),
        )

        result = process_knowledge_base.__wrapped__("support")

        self.assertEqual(result["status"], "already_processing")
        enqueue.assert_not_called()
