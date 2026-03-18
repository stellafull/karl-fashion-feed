from __future__ import annotations

import asyncio
from argparse import Namespace
import os
import unittest
from unittest.mock import patch

os.environ["MILVUS_URI"] = "http://localhost:19530"

from backend.app.scripts.dev_ingest_story_rag_today import ImageAnalysisRunResult, run
from backend.app.service.RAG.article_rag_service import RagInsertResult
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

        async def fake_analyze_new_images(article_ids: list[str]) -> ImageAnalysisRunResult:
            self.assertEqual(article_ids, ["article-1"])
            events.append("image")
            return ImageAnalysisRunResult(candidates=1, analyzed=1)

        async def fake_insert_rag_after_image_analysis(article_ids, image_analysis_task, rag_service):
            del rag_service
            self.assertEqual(article_ids, ["article-1"])
            await image_analysis_task
            events.append("rag")
            return RagInsertResult(
                publishable_articles=1,
                text_units=1,
                image_units=1,
                inserted_units=2,
            )

        args = Namespace(
            sources=None,
            limit_sources=None,
            max_articles_per_source=None,
            max_pages_per_source=None,
            request_timeout_seconds=12,
            source_concurrency=4,
            http_concurrency=16,
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
            "backend.app.scripts.dev_ingest_story_rag_today._analyze_new_images",
            fake_analyze_new_images,
        ), patch(
            "backend.app.scripts.dev_ingest_story_rag_today._insert_rag_after_image_analysis",
            fake_insert_rag_after_image_analysis,
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
