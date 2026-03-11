import os
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.config.embedding import get_embedding_models_config
from backend.app.core.database import DatabaseSettings, build_database_url, require_database_url
from backend.app.db.session import create_engine_from_url


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


class MilvusServiceImportTests(unittest.TestCase):
    def test_milvus_service_import_is_lazy_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            module = import_module("backend.app.service.milvus_service")

        self.assertTrue(hasattr(module, "get_milvus_client"))
