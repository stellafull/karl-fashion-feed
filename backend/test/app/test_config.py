import os
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.models import Document
from backend.app.config.embedding import get_embedding_models_config
from backend.app.config.milvus import get_milvus_settings
from backend.app.config.storage import DEFAULT_DOCUMENT_MARKDOWN_ROOT, get_document_markdown_root
from backend.app.core.database import (
    DatabaseSettings,
    build_database_url,
    create_all_tables,
    create_engine_from_url,
    get_engine,
    get_session_factory,
    require_database_url,
    reset_database_caches,
)


class EmbeddingConfigTests(unittest.TestCase):
    def test_embedding_defaults_are_stable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = get_embedding_models_config()

        self.assertEqual(config.sparse_embedding.embedding_model, "text-embedding-v4")
        self.assertEqual(config.sparse_embedding.embedding_dimension, 1024)
        self.assertEqual(config.dense_embedding.embedding_model, "qwen3-vl-embedding")
        self.assertEqual(config.dense_embedding.embedding_dimension, 2560)

    def test_dense_embedding_can_be_overridden_without_affecting_sparse(self):
        with mock.patch.dict(
            os.environ,
            {
                "MODALITY_EMBEDDING_MODEL": "custom-dense-model",
                "EMBEDDING_DIMENSION": "2048",
            },
            clear=True,
        ):
            config = get_embedding_models_config()

        self.assertEqual(config.sparse_embedding.embedding_model, "text-embedding-v4")
        self.assertEqual(config.sparse_embedding.embedding_dimension, 1024)
        self.assertEqual(config.dense_embedding.embedding_model, "custom-dense-model")
        self.assertEqual(config.dense_embedding.embedding_dimension, 2048)


class DatabaseConfigTests(unittest.TestCase):
    def setUp(self):
        reset_database_caches()

    def tearDown(self):
        reset_database_caches()

    def test_build_database_url_from_postgres_settings(self):
        url = build_database_url(
            DatabaseSettings(
                host="db.internal",
                port=5432,
                user="fashion",
                password="secret",
                name="feed",
            )
        )

        self.assertEqual(url, "postgresql://fashion:secret@db.internal:5432/feed")

    def test_require_database_url_uses_postgres_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "POSTGRES_HOST": "db.internal",
                "POSTGRES_PORT": "5432",
                "POSTGRES_USER": "fashion",
                "POSTGRES_PASSWORD": "secret",
                "POSTGRES_DB": "feed",
            },
            clear=True,
        ):
            self.assertEqual(
                require_database_url(),
                "postgresql://fashion:secret@db.internal:5432/feed",
            )

    def test_require_database_url_rejects_partial_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "POSTGRES_HOST": "db.internal",
                "POSTGRES_PORT": "5432",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "POSTGRES_USER"):
                require_database_url()

    def test_session_helpers_do_not_depend_on_legacy_config_module(self):
        engine = create_engine_from_url("sqlite+pysqlite:///:memory:")

        self.assertEqual(engine.dialect.name, "sqlite")

    def test_get_engine_and_session_factory_support_explicit_sqlite_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            database_url = "sqlite+pysqlite:///:memory:"
            engine = get_engine(database_url)
            create_all_tables(engine)
            session_factory = get_session_factory(database_url)

        self.assertEqual(engine.dialect.name, "sqlite")
        with session_factory() as session:
            session.add(
                Document(
                    article_id="article_001",
                    source_id="source-001",
                    canonical_url="https://example.com/story-001",
                    title="Story 001",
                    source_payload={},
                )
            )
            session.commit()
            self.assertIs(session.get_bind(), engine)
            self.assertEqual(session.execute(text("select count(*) from document")).scalar_one(), 1)

    def test_create_all_tables_accepts_explicit_engine_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            engine = get_engine("sqlite+pysqlite:///:memory:")
            create_all_tables(engine)

        inspector = inspect(engine)
        self.assertIn("document", inspector.get_table_names())
        self.assertIn("story", inspector.get_table_names())

    def test_default_engine_and_session_factory_follow_changed_postgres_env(self):
        first_env = {
            "POSTGRES_HOST": "first-host.internal",
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "fashion",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_DB": "feed_a",
        }
        second_env = {
            "POSTGRES_HOST": "second-host.internal",
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "fashion",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_DB": "feed_b",
        }

        with mock.patch.dict(os.environ, first_env, clear=True):
            first_engine = get_engine()
            first_session_factory = get_session_factory()

        with mock.patch.dict(os.environ, second_env, clear=True):
            second_engine = get_engine()
            second_session_factory = get_session_factory()

        self.assertNotEqual(str(first_engine.url), str(second_engine.url))
        with first_session_factory() as first_session:
            self.assertIs(first_session.get_bind(), first_engine)
        with second_session_factory() as second_session:
            self.assertIs(second_session.get_bind(), second_engine)


class StorageConfigTests(unittest.TestCase):
    def test_document_markdown_root_defaults_to_runtime_directory(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_document_markdown_root(), DEFAULT_DOCUMENT_MARKDOWN_ROOT)

    def test_document_markdown_root_honors_env_override(self):
        with mock.patch.dict(os.environ, {"DOCUMENT_MARKDOWN_ROOT": "/tmp/fashion-docs"}, clear=True):
            self.assertEqual(get_document_markdown_root(), Path("/tmp/fashion-docs"))


class MilvusConfigTests(unittest.TestCase):
    def test_get_milvus_settings_normalizes_bare_host_port_uri(self):
        with mock.patch.dict(os.environ, {"MILVUS_URI": "localhost:19530"}, clear=True):
            settings = get_milvus_settings()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.uri, "http://localhost:19530")

    def test_get_milvus_settings_keeps_explicit_http_uri(self):
        with mock.patch.dict(os.environ, {"MILVUS_URI": "http://localhost:19530"}, clear=True):
            settings = get_milvus_settings()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.uri, "http://localhost:19530")

    def test_get_milvus_settings_keeps_explicit_https_uri(self):
        with mock.patch.dict(os.environ, {"MILVUS_URI": "https://milvus.internal:19530"}, clear=True):
            settings = get_milvus_settings()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.uri, "https://milvus.internal:19530")


class MilvusServiceImportTests(unittest.TestCase):
    def test_milvus_service_import_is_lazy_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            module = import_module("backend.app.service.milvus_service")

        self.assertTrue(hasattr(module, "get_milvus_client"))
