import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.db.base import Base
from backend.app.db.models import Document
from backend.app.db.session import create_engine_from_url
from backend.app.service.document_ingestion_service import (
    DocumentIngestionService,
    map_article_to_document,
)


def build_article(**overrides):
    article = {
        "id": "article_001",
        "source_id": "business-of-fashion",
        "source": "Business of Fashion",
        "source_type": "rss",
        "link": "https://example.com/story?utm_source=newsletter",
        "canonical_url": "https://example.com/story",
        "title": "A fashion story",
        "source_host": "example.com",
        "source_lang": "en",
        "published": "2026-03-11T08:00:00+00:00",
        "content_text": "Long-form article text.",
        "content_snippet": "Long-form article text.",
        "image": "https://example.com/image.jpg",
        "article_summary": "中文摘要",
        "article_tags": ["fashion", "market"],
        "category_hint": "品牌/市场",
        "category_id": "brand-market",
        "content_type": "brand-market",
        "relevance_score": 88,
        "relevance_reason": "高度相关。",
        "is_relevant": True,
        "is_sensitive": False,
        "content_hash": "abc123",
    }
    article.update(overrides)
    return article


class DocumentIngestionServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        self.markdown_dir = tempfile.TemporaryDirectory()
        self.service = DocumentIngestionService(
            session_factory=self.session_factory,
            markdown_storage_root=self.markdown_dir.name,
        )

    def tearDown(self):
        self.markdown_dir.cleanup()

    def test_map_article_to_document_preserves_core_fields(self):
        document = map_article_to_document(build_article(), content_md_path="/tmp/article_001.md")

        self.assertEqual(document.article_id, "article_001")
        self.assertEqual(document.source_id, "business-of-fashion")
        self.assertEqual(document.canonical_url, "https://example.com/story")
        self.assertEqual(document.title, "A fashion story")
        self.assertEqual(document.language, "en")
        self.assertEqual(document.content_md_path, "/tmp/article_001.md")
        self.assertEqual(document.parse_status, "parsed")
        self.assertEqual(document.source_payload["link"], "https://example.com/story?utm_source=newsletter")
        self.assertEqual(document.source_payload["article_tags"], ["fashion", "market"])

    def test_ingest_articles_persists_cleaned_markdown_and_stores_only_path(self):
        self.service.ingest_articles([build_article()])

        with self.session_factory() as session:
            document = session.scalar(select(Document))

        self.assertIsNotNone(document)
        self.assertIsNotNone(document.content_md_path)
        markdown_path = Path(document.content_md_path)
        self.assertTrue(markdown_path.exists())
        self.assertEqual(markdown_path.parent, Path(self.markdown_dir.name))
        markdown_text = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# A fashion story", markdown_text)
        self.assertIn("Long-form article text.", markdown_text)
        self.assertFalse(hasattr(document, "raw_text"))
        self.assertFalse(hasattr(document, "raw_html_path"))

    def test_ingest_articles_inserts_only_new_canonical_urls(self):
        first_run = self.service.ingest_articles([build_article()])
        second_run = self.service.ingest_articles(
            [
                build_article(
                    id="article_002",
                    title="Updated title should still be skipped",
                    canonical_url="https://example.com/story",
                ),
                build_article(
                    id="article_003",
                    canonical_url="https://example.com/story-2",
                    link="https://example.com/story-2",
                    title="Another story",
                ),
            ]
        )

        with self.session_factory() as session:
            count = session.scalar(select(func.count()).select_from(Document))
            inserted_titles = session.scalars(select(Document.title).order_by(Document.canonical_url)).all()

        self.assertEqual(first_run.collected_count, 1)
        self.assertEqual(first_run.existing_count, 0)
        self.assertEqual(first_run.inserted_count, 1)
        self.assertEqual(second_run.collected_count, 2)
        self.assertEqual(second_run.existing_count, 1)
        self.assertEqual(second_run.inserted_count, 1)
        self.assertEqual(count, 2)
        self.assertEqual(inserted_titles, ["A fashion story", "Another story"])

    def test_collect_and_ingest_uses_collection_output(self):
        with mock.patch(
            "backend.app.service.document_ingestion_service.collect_articles",
            return_value=[build_article()],
        ) as collect_articles:
            stats = self.service.collect_and_ingest(sources_file="custom-sources.yaml")

        self.assertEqual(stats.inserted_count, 1)
        collect_articles.assert_called_once_with(sources_file="custom-sources.yaml")
