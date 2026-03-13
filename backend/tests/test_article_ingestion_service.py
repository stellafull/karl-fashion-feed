from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models.article import Article, ArticleImage
from backend.app.service.article_contracts import CollectedArticle, CollectedImage, MarkdownBlock
from backend.app.service.article_ingestion_service import ArticleIngestionService
from backend.app.service.article_markdown_service import ArticleMarkdownService


class StubCollector:
    def __init__(self, articles):
        self._articles = articles
        self.last_kwargs = None

    async def collect_articles(self, **kwargs):
        self.last_kwargs = kwargs
        return list(self._articles)


class ArticleIngestionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ingest_articles_writes_markdown_and_image_assets(self) -> None:
        service = ArticleIngestionService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
        )
        article = CollectedArticle(
            source_name="Vogue",
            source_type="rss",
            lang="en",
            category="高端时装",
            url="https://example.com/story",
            canonical_url="https://example.com/story",
            title="Runway Story",
            summary="Front-row summary",
            markdown_blocks=(
                MarkdownBlock(kind="image", image_index=0),
                MarkdownBlock(kind="paragraph", text="Front row paragraph."),
            ),
            images=(
                CollectedImage(
                    source_url="https://example.com/hero.jpg",
                    normalized_url="https://example.com/hero.jpg",
                    role="hero",
                    alt_text="Runway look",
                    context_snippet="Front row paragraph.",
                ),
            ),
            published_at=datetime(2026, 3, 13, 8, 0, 0),
        )

        result = service.ingest_articles([article])
        self.assertEqual(result.inserted, 1)

        with self.session_factory() as session:
            stored_article = session.scalars(select(Article)).one()
            stored_images = session.scalars(select(ArticleImage)).all()

        self.assertEqual(stored_article.canonical_url, "https://example.com/story")
        self.assertTrue(stored_article.markdown_rel_path.endswith(".md"))
        self.assertEqual(stored_article.image_url, "https://example.com/hero.jpg")
        self.assertEqual(stored_article.hero_image_id, stored_images[0].image_id)
        self.assertEqual(len(stored_images), 1)
        self.assertEqual(stored_images[0].context_snippet, "Front row paragraph.")

        markdown_path = Path(self.temp_dir.name) / stored_article.markdown_rel_path
        markdown_content = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# Runway Story", markdown_content)
        self.assertIn(f"[image:{stored_images[0].image_id}]", markdown_content)
        self.assertIn("Front row paragraph.", markdown_content)

    def test_ingest_articles_skips_duplicates_in_batch_and_db(self) -> None:
        service = ArticleIngestionService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
        )
        article = CollectedArticle(
            source_name="Vogue",
            source_type="rss",
            lang="en",
            category="高端时装",
            url="https://example.com/story?utm_source=rss",
            canonical_url="https://example.com/story",
            title="Story",
            summary="Summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Body"),),
            images=(),
            published_at=datetime(2026, 3, 13, 8, 0, 0),
        )

        result = service.ingest_articles([article, article])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.skipped_in_batch, 1)

        second_result = service.ingest_articles([article])
        self.assertEqual(second_result.inserted, 0)
        self.assertEqual(second_result.skipped_existing, 1)

    def test_collect_and_ingest_passes_bootstrap_options(self) -> None:
        collector = StubCollector([])
        service = ArticleIngestionService(
            session_factory=self.session_factory,
            collector=collector,
            markdown_service=self.markdown_service,
        )

        cutoff = datetime(2026, 2, 12, 8, 0, 0)
        asyncio.run(
            service.collect_and_ingest(
                source_names=["Vogue"],
                limit_sources=3,
                published_after=cutoff,
                max_articles_per_source=100,
                max_pages_per_source=4,
                include_undated=True,
            )
        )

        self.assertEqual(
            collector.last_kwargs,
            {
                "source_names": ["Vogue"],
                "limit_sources": 3,
                "published_after": cutoff,
                "max_articles_per_source": 100,
                "max_pages_per_source": 4,
                "include_undated": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
