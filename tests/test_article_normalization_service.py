"""Tests for durable article normalization persistence."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article
from backend.app.schemas.llm.article_normalization import ArticleNormalizationSchema
from backend.app.service.article_normalization_service import ArticleNormalizationService
from backend.app.service.article_parse_service import ArticleMarkdownService


def make_article(
    *,
    article_id: str = "article-1",
    parse_status: str,
    markdown_rel_path: str | None,
) -> Article:
    """Build a minimal article row for normalization tests."""
    reference_time = datetime(2026, 3, 26, 7, 0, tzinfo=UTC).replace(tzinfo=None)
    return Article(
        article_id=article_id,
        source_name="Vogue Runway",
        source_type="rss",
        source_lang="en",
        category="fashion",
        canonical_url=f"https://example.com/{article_id}",
        original_url=f"https://example.com/original/{article_id}",
        title_raw="Raw title",
        summary_raw="Raw summary",
        markdown_rel_path=markdown_rel_path,
        discovered_at=reference_time,
        ingested_at=reference_time,
        metadata_json={},
        parse_status=parse_status,
        parse_updated_at=reference_time,
        normalization_status="pending",
        normalization_attempts=0,
        normalization_updated_at=reference_time,
    )


class ArticleNormalizationServiceTest(unittest.TestCase):
    """Verify normalization persists durable Chinese materials and failure state."""

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

    def test_normalize_article_persists_durable_chinese_materials(self) -> None:
        markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))
        markdown_service.write_markdown(
            relative_path="2026-03-26/a.md",
            content="# Raw title\n\nRaw body.\n",
        )
        service = ArticleNormalizationService(markdown_service=markdown_service)
        service._infer_normalized_material = AsyncMock(  # type: ignore[method-assign]
            return_value=ArticleNormalizationSchema(
                title_zh=" 中文标题 ",
                summary_zh=" 中文摘要 ",
                body_zh="第一段\n\n第二段",
            )
        )

        with self.session_factory() as session:
            article = make_article(parse_status="done", markdown_rel_path="2026-03-26/a.md")
            session.add(article)
            session.flush()

            result = asyncio.run(service.normalize_article(session, article))

            self.assertTrue(result)
            self.assertEqual(article.normalization_status, "done")
            self.assertEqual(article.title_zh, "中文标题")
            self.assertEqual(article.summary_zh, "中文摘要")
            self.assertEqual(article.body_zh_rel_path, "normalized/2026-03-26/article-1.md")
            self.assertTrue((Path(self.temp_dir.name) / article.body_zh_rel_path).exists())

    def test_normalization_abandons_after_third_failure(self) -> None:
        markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))
        service = ArticleNormalizationService(markdown_service=markdown_service)

        with self.session_factory() as session:
            article = make_article(parse_status="done", markdown_rel_path="2026-03-26/a.md")
            article.normalization_attempts = 2
            session.add(article)
            session.flush()

            result = asyncio.run(service.normalize_article(session, article))

            self.assertFalse(result)
            self.assertEqual(article.normalization_status, "abandoned")
