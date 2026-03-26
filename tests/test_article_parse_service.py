"""Tests for article parse stage persistence under the digest contract."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from backend.app.core.database import Base
from backend.app.models import Article, ArticleImage
from backend.app.service.article_contracts import CollectedImage, MarkdownBlock, ParsedArticle
from backend.app.service.article_parse_service import ArticleMarkdownService, ArticleParseService


class ArticleParseServiceTest(unittest.TestCase):
    """Verify parse writes only current parse/image contract state."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _insert_article(
        self,
        *,
        article_id: str | None = None,
        parse_attempts: int = 0,
        parse_status: str = "pending",
    ) -> tuple[str, datetime, datetime]:
        article_id = article_id or str(uuid4())
        ingested_at = datetime(2026, 3, 26, 7, 0, tzinfo=UTC).replace(tzinfo=None)
        parse_updated_at = datetime(2026, 3, 26, 6, 0, tzinfo=UTC).replace(tzinfo=None)
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id=article_id,
                    source_name="Vogue Runway",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url=f"https://example.com/{article_id}",
                    original_url=f"https://example.com/original/{article_id}",
                    title_raw="Original title",
                    summary_raw="Original summary",
                    published_at=None,
                    discovered_at=ingested_at,
                    ingested_at=ingested_at,
                    metadata_json={},
                    parse_status=parse_status,
                    parse_attempts=parse_attempts,
                    parse_error=None,
                    parse_updated_at=parse_updated_at,
                )
            )
            session.commit()
        return article_id, ingested_at, parse_updated_at

    def test_persist_outcomes_marks_parse_done_without_rewriting_ingested_at(self) -> None:
        article_id, ingested_at, old_parse_updated_at = self._insert_article()
        service = ArticleParseService()
        service._markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))
        parsed = ParsedArticle(
            title="Normalized title",
            summary="Normalized summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Body text"),),
            images=(
                CollectedImage(
                    source_url="https://example.com/image.jpg",
                    normalized_url="https://example.com/image.jpg",
                    role="hero",
                    metadata={"image_hash": "abcd1234"},
                ),
            ),
            published_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "unit-test"},
        )

        with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
            result = service._persist_outcomes([(article_id, parsed, None)])

        with self.session_factory() as session:
            stored = session.get(Article, article_id)
            image = session.scalars(select(ArticleImage).where(ArticleImage.article_id == article_id)).one()

        self.assertEqual(result.parsed, 1)
        self.assertEqual(stored.parse_status, "done")
        self.assertIsNone(stored.parse_error)
        self.assertGreater(stored.parse_updated_at, old_parse_updated_at)
        self.assertEqual(stored.ingested_at, ingested_at)
        self.assertIsNotNone(stored.markdown_rel_path)
        self.assertEqual(image.image_hash, "abcd1234")

    def test_persist_outcomes_marks_parse_failure_with_updated_timestamp(self) -> None:
        article_id, _ingested_at, old_parse_updated_at = self._insert_article()
        service = ArticleParseService()
        service._markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))

        with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
            result = service._persist_outcomes([(article_id, None, RuntimeError("boom"))])

        with self.session_factory() as session:
            stored = session.get(Article, article_id)

        self.assertEqual(result.failed, 1)
        self.assertEqual(stored.parse_status, "failed")
        self.assertEqual(stored.parse_attempts, 1)
        self.assertEqual(stored.parse_error, "RuntimeError: boom")
        self.assertGreater(stored.parse_updated_at, old_parse_updated_at)

    def test_parse_article_updates_parse_updated_at_instead_of_story_era_fields(self) -> None:
        article_id, ingested_at, old_parse_updated_at = self._insert_article(article_id="article-1")
        service = ArticleParseService()
        service._markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))

        parsed = ParsedArticle(
            title="Parsed title",
            summary="Parsed summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Body text"),),
            images=(),
            published_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "unit-test"},
        )

        async def _fake_parse(_candidates: list[Article]):
            return [(article_id, parsed, None)]

        with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
            with patch.object(service, "_parse_batches_with_http_session", side_effect=_fake_parse):
                result = asyncio.run(service.parse_articles(article_ids=[article_id]))

        self.assertEqual(result.parsed, 1)

        with self.session_factory() as session:
            stored = session.get(Article, article_id)

        self.assertEqual(stored.parse_status, "done")
        self.assertGreater(stored.parse_updated_at, old_parse_updated_at)

        # Parse stage should persist truth-source article detail extracted from the page.
        self.assertEqual(stored.title_raw, "Parsed title")
        self.assertEqual(stored.summary_raw, "Parsed summary")
        self.assertEqual(stored.published_at, parsed.published_at)
        self.assertEqual(stored.metadata_json, {"parser": "unit-test"})
        self.assertEqual(stored.character_count, len("Parsed title\nBody text"))

        # Parse stage must not rewrite collection timestamps.
        self.assertEqual(stored.ingested_at, ingested_at)

    def test_parse_failure_abandons_after_third_attempt(self) -> None:
        article_id, _ingested_at, _old_parse_updated_at = self._insert_article(
            article_id="article-1",
            parse_status="failed",
            parse_attempts=2,
        )
        service = ArticleParseService()
        service._markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))

        with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
            result = service._persist_outcomes([(article_id, None, RuntimeError("parse boom"))])

        self.assertEqual(result.failed, 1)

        with self.session_factory() as session:
            stored = session.get(Article, article_id)

        self.assertEqual(stored.parse_status, "abandoned")
        self.assertEqual(stored.parse_attempts, 3)
