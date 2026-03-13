from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models.article import Article
from backend.app.service.article_ingestion_service import ArticleIngestionService
from backend.app.service.news_collection_service import CollectedArticle


class StubCollector:
    def __init__(self, articles):
        self._articles = articles
        self.last_kwargs = None

    async def collect_articles(self, **_kwargs):
        self.last_kwargs = _kwargs
        return list(self._articles)


class ArticleIngestionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def test_ingest_articles_skips_duplicates_in_batch_and_db(self) -> None:
        service = ArticleIngestionService(session_factory=self.session_factory)
        article = CollectedArticle(
            source_name="Vogue",
            source_type="rss",
            lang="en",
            category="高端时装",
            url="https://example.com/story?utm_source=rss",
            canonical_url="https://example.com/story",
            title="Story",
            summary="Summary",
            content="Full content",
            image_url=None,
            published_at=datetime(2026, 3, 13, 8, 0, 0),
        )

        result = service.ingest_articles([article, article])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.skipped_in_batch, 1)
        self.assertEqual(result.skipped_existing, 0)

        second_result = service.ingest_articles([article])
        self.assertEqual(second_result.inserted, 0)
        self.assertEqual(second_result.skipped_existing, 1)

        with self.session_factory() as session:
            stored = session.scalars(select(Article)).all()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].canonical_url, "https://example.com/story")

    def test_collect_and_ingest_uses_collector(self) -> None:
        article = CollectedArticle(
            source_name="Vogue",
            source_type="rss",
            lang="en",
            category="高端时装",
            url="https://example.com/story",
            canonical_url="https://example.com/story",
            title="Story",
            summary="Summary",
            content="Full content",
            image_url=None,
            published_at=None,
        )
        service = ArticleIngestionService(
            session_factory=self.session_factory,
            collector=StubCollector([article]),
        )

        result = asyncio.run(service.collect_and_ingest())
        self.assertEqual(result.inserted, 1)

    def test_collect_and_ingest_passes_bootstrap_options(self) -> None:
        collector = StubCollector([])
        service = ArticleIngestionService(
            session_factory=self.session_factory,
            collector=collector,
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
