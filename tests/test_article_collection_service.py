"""Tests for article collection persistence under the digest contract."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from backend.app.core.database import Base
from backend.app.models import Article, ensure_article_storage_schema
from backend.app.service.article_collection_service import ArticleCollectionService
from backend.app.service.article_contracts import CollectedArticle


class ArticleCollectionServiceTest(unittest.TestCase):
    """Verify collection writes only the current Article contract."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

    def test_store_articles_persists_current_article_contract(self) -> None:
        service = ArticleCollectionService()
        collected = CollectedArticle(
            source_name="Vogue Runway",
            source_type="rss",
            lang="en",
            category="fashion",
            url="https://example.com/raw",
            canonical_url="https://example.com/canonical",
            title="Raw title",
            summary="Raw summary",
            published_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC).replace(tzinfo=None),
            metadata={"feed": "runway"},
        )

        with patch("backend.app.service.article_collection_service.SessionLocal", self.session_factory):
            result = service.store_articles([collected])

        with self.session_factory() as session:
            article = session.scalars(select(Article)).one()

        self.assertEqual(result.inserted, 1)
        self.assertEqual(article.canonical_url, collected.canonical_url)
        self.assertEqual(article.parse_status, "pending")
        self.assertEqual(article.parse_attempts, 0)
        self.assertIsNone(article.markdown_rel_path)
        self.assertIsNone(article.body_zh_rel_path)
        self.assertEqual(article.metadata_json["feed"], "runway")
