from __future__ import annotations

import asyncio
from argparse import Namespace
import os
import unittest
from unittest.mock import patch

os.environ["QDRANT_URL"] = "http://localhost:6333"

from backend.app.scripts.dev_ingest_story_rag_today import ArticleRagRunResult, run
from backend.app.service.article_collection_service import CollectionResult
from backend.app.service.article_parse_service import ParseResult
from backend.app.service.story_pipeline_contracts import StoryDraft
from backend.app.service.story_workflow_service import StoryWorkflowResult


class DevIngestStoryRagTodayTest(unittest.TestCase):
    def test_run_executes_story_and_rag_after_enrichment(self) -> None:
        events: list[str] = []

        class StubNewsCollectionService:
            def __init__(self, **_: object) -> None:
                events.append("collector_init")

        class StubArticleCollectionService:
            def __init__(self, *, collector: object) -> None:
                del collector

            async def collect_articles(self, **_: object) -> CollectionResult:
                events.append("collect")
                return CollectionResult(
                    total_collected=1,
                    unique_candidates=1,
                    inserted=1,
                    skipped_existing=0,
                    skipped_in_batch=0,
                    inserted_article_ids=("article-1",),
                )

        class StubArticleParseService:
            def __init__(self, *, collector: object) -> None:
                del collector

            async def parse_articles(self, *, article_ids: list[str]) -> ParseResult:
                self.assertEqual(article_ids, ["article-1"])
                events.append("parse")
                return ParseResult(
                    candidates=1,
                    parsed=1,
                    failed=0,
                    parsed_article_ids=("article-1",),
                )

            def assertEqual(self, left: object, right: object) -> None:
                if left != right:
                    raise AssertionError(f"{left!r} != {right!r}")

        class StubStoryWorkflowService:
            async def enrich_articles(self, article_ids: list[str]) -> tuple[int, int]:
                self.assertEqual(article_ids, ["article-1"])
                events.append("enrich")
                return 1, 0

            async def build_story_drafts(self, article_ids: list[str]) -> StoryWorkflowResult:
                self.assertEqual(article_ids, ["article-1"])
                events.append("story")
                return StoryWorkflowResult(
                    enriched_count=0,
                    skipped_existing_enrichment=0,
                    publishable_records=tuple(),
                    watermark_ingested_at=None,
                    story_drafts=(
                        StoryDraft(
                            title_zh="标题",
                            summary_zh="摘要",
                            key_points=("要点",),
                            tags=("时尚",),
                            category="高端时装",
                            article_ids=("article-1",),
                            hero_image_url=None,
                            source_article_count=1,
                        ),
                    ),
                    stages_completed=("story_embedding", "semantic_cluster", "cluster_review", "story_generation"),
                    stages_skipped=tuple(),
                )

            def assertEqual(self, left: object, right: object) -> None:
                if left != right:
                    raise AssertionError(f"{left!r} != {right!r}")

        class StubArticleRagService:
            pass

        async def fake_process_articles_for_rag(
            article_ids: list[str],
            *,
            rag_service: object,
            worker_count: int,
            retry_delay_seconds: int,
        ) -> ArticleRagRunResult:
            del rag_service
            self.assertEqual(article_ids, ["article-1"])
            self.assertEqual(worker_count, 2)
            self.assertEqual(retry_delay_seconds, 15)
            events.append("image")
            events.append("rag")
            return ArticleRagRunResult(
                image_candidates=1,
                analyzed_images=1,
                publishable_articles=1,
                text_units=1,
                image_units=1,
                upserted_units=2,
            )

        args = Namespace(
            sources=None,
            limit_sources=None,
            max_articles_per_source=None,
            max_pages_per_source=None,
            request_timeout_seconds=12,
            source_concurrency=4,
            http_concurrency=16,
            image_analysis_concurrency=2,
            image_analysis_retry_delay_seconds=15,
        )

        with patch("backend.app.scripts.dev_ingest_story_rag_today.ensure_article_storage_schema"), patch(
            "backend.app.scripts.dev_ingest_story_rag_today.Base.metadata.create_all"
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today.NewsCollectionService",
            StubNewsCollectionService,
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today.ArticleCollectionService",
            StubArticleCollectionService,
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today.ArticleParseService",
            StubArticleParseService,
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today.StoryWorkflowService",
            StubStoryWorkflowService,
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today.ArticleRagService",
            StubArticleRagService,
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today._process_articles_for_rag",
            fake_process_articles_for_rag,
        ):
            result = asyncio.run(run(args))

        self.assertEqual(result, 0)
        self.assertIn("collect", events)
        self.assertIn("parse", events)
        self.assertIn("enrich", events)
        self.assertIn("image", events)
        self.assertIn("story", events)
        self.assertIn("rag", events)
        self.assertLess(events.index("enrich"), events.index("story"))
        self.assertLess(events.index("image"), events.index("rag"))


if __name__ == "__main__":
    unittest.main()
