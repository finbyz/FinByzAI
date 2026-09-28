from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from finbyzai.ai.api.knowledge_base_api import (
    _serialize,
    extract_pdfs,
    update_knowledge_base,
    upsert_to_vector_store,
)
from finbyzai.ai.doctype.knowledge_base.knowledge_base import _get_embeddings
from finbyzai.ai.embeddings.openrouter_embedding import (
    OPENROUTER_API_BASE,
    OpenRouterEmbedding,
)


class TestKnowledgeBaseConfiguration(TestCase):
    def test_serialize_only_uses_existing_knowledge_base_fields(self):
        kb = SimpleNamespace(
            name="support",
            title="Support",
            provider="OpenRouter",
            embeding_model="openrouter/openai/text-embedding-3-small",
            vector_store="ChromaDB",
            description="Support documents",
        )

        data = _serialize(kb)

        self.assertEqual(data["embeding_model"], kb.embeding_model)
        self.assertNotIn("chunk_size", data)
        self.assertNotIn("chunk_overlap", data)

    @patch("finbyzai.ai.api.knowledge_base_api.frappe.throw", side_effect=ValueError)
    @patch("finbyzai.ai.api.knowledge_base_api._get_knowledge_base")
    def test_update_rejects_unknown_fields(self, get_knowledge_base, _throw):
        kb = MagicMock()
        get_knowledge_base.return_value = kb

        with patch("finbyzai.ai.api.knowledge_base_api._", lambda message: message):
            with self.assertRaises(ValueError):
                update_knowledge_base.__wrapped__(
                    "support", embedding_model="obsolete-field"
                )

        kb.save.assert_not_called()

    @patch("finbyzai.ai.api.knowledge_base_api._get_knowledge_base")
    def test_legacy_processing_endpoints_queue_reprocessing(self, get_knowledge_base):
        kb = MagicMock()
        get_knowledge_base.return_value = kb

        with patch("finbyzai.ai.api.knowledge_base_api._", lambda message: message):
            extract_result = extract_pdfs.__wrapped__("support")
            upsert_result = upsert_to_vector_store.__wrapped__("support")

        self.assertEqual(kb.reprocess_all_documents.call_count, 2)
        self.assertEqual(extract_result["data"]["status"], "Queue")
        self.assertEqual(upsert_result["data"]["status"], "Queue")

    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.create_embedding")
    @patch("finbyzai.ai.doctype.knowledge_base.knowledge_base.frappe.get_doc")
    def test_embedding_uses_selected_models_provider_key(self, get_doc, create_embedding):
        llm = SimpleNamespace(
            name="openrouter/openai/text-embedding-3-small",
            provider="OpenRouter",
            is_embedding_model=1,
        )
        provider = MagicMock()
        provider.get_password.return_value = "secret"
        get_doc.side_effect = [llm, provider]
        expected = object()
        create_embedding.return_value = expected
        kb = SimpleNamespace(
            name="support",
            provider="OpenRouter",
            embeding_model=llm.name,
        )

        result = _get_embeddings(kb)

        self.assertIs(result, expected)
        create_embedding.assert_called_once_with(
            "OpenRouter", model=llm.name, api_key="secret"
        )


class TestOpenRouterEmbedding(TestCase):
    @patch("finbyzai.ai.embeddings.openrouter_embedding.OpenAIEmbeddings")
    def test_uses_openrouter_endpoint_and_normalizes_seed_name(self, embeddings):
        adapter = OpenRouterEmbedding(
            model="openrouter/openai/text-embedding-3-small",
            api_key="secret",
        )

        embeddings.assert_called_once_with(
            model="openai/text-embedding-3-small",
            api_key="secret",
            base_url=OPENROUTER_API_BASE,
        )
        self.assertIs(adapter.embedding, embeddings.return_value)


class TestKnowledgeBaseSources(TestCase):
    @patch("finbyzai.ai.api.knowledge_base_api._get_knowledge_base")
    def test_text_document_is_stored_as_note(self, get_knowledge_base):
        from finbyzai.ai.api.knowledge_base_api import add_text_document

        kb = MagicMock()
        get_knowledge_base.return_value = kb
        with patch("finbyzai.ai.api.knowledge_base_api._", lambda message: message):
            add_text_document.__wrapped__("support", "  useful note  ")

        kb.append.assert_called_once_with(
            "notes", {"note": "useful note", "is_processed": False}
        )
        kb.save.assert_called_once_with()

    def test_file_change_marks_document_unprocessed(self):
        from finbyzai.ai.doctype.knowledge_document.knowledge_document import (
            KnowledgeDocument,
        )

        row = SimpleNamespace(has_value_changed=lambda field: field == "file")
        KnowledgeDocument.validate(row)

        self.assertFalse(row.is_processed)


class TestOpenAIEmbedding(TestCase):
    @patch("finbyzai.ai.embeddings.openai_embedding.OpenAIEmbeddings")
    def test_normalizes_seed_name(self, embeddings):
        from finbyzai.ai.embeddings.openai_embedding import OpenAIEmbedding

        OpenAIEmbedding(model="openai/text-embedding-3-small", api_key="secret")

        embeddings.assert_called_once_with(
            model="text-embedding-3-small", api_key="secret"
        )
