"""Focused regression tests for the pipeline refactor in TODO.md."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article, ArticleImage, PipelineRun, Story
from backend.app.service import article_cluster_service as cluster_module
from backend.app.service import article_enrichment_service as enrichment_module
from backend.app.service import article_parse_service as parse_module
from backend.app.service import scheduler_service as scheduler_module
from backend.app.service.RAG import article_rag_service as rag_module
from backend.app.service.RAG import embedding_service as embedding_module
from backend.app.service.article_cluster_service import EmbeddedArticle
from backend.app.service.article_enrichment_service import EnrichedArticle
from backend.app.service.article_parse_service import ArticleMarkdownService, ParseResult
from backend.app.service.story_generation_service import StoryDraft


def build_session_local():
    """Create one shared in-memory SQLite session factory for a test case."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, session_local


def make_article(
    article_id: str,
    *,
    should_publish: bool | None = None,
    enrichment_status: str = "pending",
    parse_status: str = "pending",
    parse_attempts: int = 0,
    markdown_rel_path: str | None = None,
) -> Article:
    """Build a minimal article row for tests."""
    article = Article(
        article_id=article_id,
        source_name="test-source",
        source_type="rss",
        source_lang="en",
        category="fashion",
        canonical_url=f"https://example.com/{article_id}",
        original_url=f"https://example.com/{article_id}",
        title_raw=f"Title {article_id}",
        summary_raw=f"Summary {article_id}",
        parse_status=parse_status,
        parse_attempts=parse_attempts,
        should_publish=should_publish,
        enrichment_status=enrichment_status,
        enrichment_attempts=0,
        markdown_rel_path=markdown_rel_path,
    )
    article.title_zh = f"中文标题 {article_id}"
    article.summary_zh = f"中文摘要 {article_id}"
    article.cluster_text = f"cluster {article_id}"
    article.tags_json = ["tag"]
    article.brands_json = ["brand"]
    article.category_candidates_json = ["fashion"]
    return article


def make_enriched_record(article_id: str, *, hour: int) -> EnrichedArticle:
    """Build one enrichment record for clustering tests."""
    return EnrichedArticle(
        article_id=article_id,
        title_zh=f"标题 {article_id}",
        summary_zh=f"摘要 {article_id}",
        tags=("tag",),
        brands=("brand",),
        category_candidates=("fashion",),
        cluster_text=f"cluster {article_id}",
        published_at=None,
        ingested_at=scheduler_module.datetime(2026, 3, 23, hour, 0, 0),
        hero_image_url=None,
        source_name="test-source",
    )


class IdentityReviewClient:
    """Return the same cluster membership that was sent for review."""

    def __init__(self) -> None:
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        )

    async def parse(self, *, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        parsed = SimpleNamespace(
            groups=[
                SimpleNamespace(
                    article_ids=[item["article_id"] for item in payload],
                )
            ]
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
        )


class TrackingReviewClient(IdentityReviewClient):
    """Track the peak number of concurrent review requests."""

    def __init__(self) -> None:
        super().__init__()
        self.current = 0
        self.max_seen = 0

    async def parse(self, *, messages, **kwargs):
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        try:
            await asyncio.sleep(0.01)
            return await super().parse(messages=messages, **kwargs)
        finally:
            self.current -= 1


class FakeEnrichmentService:
    """Provide to_record while rejecting unexpected enrichment work."""

    @staticmethod
    async def enrich_article(session, article):  # pragma: no cover
        raise AssertionError("enrich_article should not run in this test")

    @staticmethod
    def to_record(article: Article) -> EnrichedArticle:
        return enrichment_module.ArticleEnrichmentService.to_record(article)


class PipelineRefactorTests(unittest.TestCase):
    """Regression tests for the pipeline refactor behavior."""

    def setUp(self) -> None:
        self.engine, self.session_local = build_session_local()

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_parse_retry_limit_marks_article_abandoned(self) -> None:
        """Articles stop retrying after the configured parse attempt ceiling."""
        with self.session_local() as session:
            session.add(
                make_article(
                    "article-1",
                    parse_status="failed",
                    parse_attempts=2,
                )
            )
            session.commit()

        service = parse_module.ArticleParseService.__new__(parse_module.ArticleParseService)
        with (
            patch.object(parse_module, "SessionLocal", self.session_local),
            patch.object(parse_module, "ensure_article_storage_schema", lambda bind: None),
        ):
            result = service._persist_outcomes(
                [("article-1", None, RuntimeError("parse boom"))]
            )
            self.assertEqual(result.failed, 1)
            candidates = service._load_candidates(article_ids=None, limit=None)

        self.assertEqual(candidates, [])
        with self.session_local() as session:
            article = session.get(Article, "article-1")
            self.assertIsNotNone(article)
            assert article is not None
            self.assertEqual(article.parse_attempts, parse_module.MAX_PARSE_ATTEMPTS)
            self.assertEqual(article.parse_status, "abandoned")
            self.assertIn("RuntimeError: parse boom", article.parse_error or "")

    def test_enrichment_retry_limit_marks_article_abandoned(self) -> None:
        """Enrichment failures become terminal after three attempts."""
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_service = ArticleMarkdownService(Path(temp_dir))
            relative_path = "2026-03-23/article-1.md"
            markdown_service.write_markdown(
                relative_path=relative_path,
                content="# Test\n\nBody\n",
            )
            with self.session_local() as session:
                article = make_article(
                    "article-1",
                    parse_status="done",
                    markdown_rel_path=relative_path,
                )
                session.add(article)
                session.commit()

            service = enrichment_module.ArticleEnrichmentService.__new__(
                enrichment_module.ArticleEnrichmentService
            )
            service._markdown_service = markdown_service

            class RejectingClient:
                def __init__(self) -> None:
                    self.beta = SimpleNamespace(
                        chat=SimpleNamespace(
                            completions=SimpleNamespace(parse=self.parse)
                        )
                    )

                async def parse(self, **kwargs):
                    raise RuntimeError("blocked by upstream moderation")

            service._client = RejectingClient()

            for _ in range(enrichment_module.MAX_ENRICHMENT_ATTEMPTS):
                with self.session_local() as session:
                    article = session.get(Article, "article-1")
                    assert article is not None
                    result = asyncio.run(service.enrich_article(session, article))
                    session.commit()
                    self.assertFalse(result)

            with self.session_local() as session:
                article = session.get(Article, "article-1")
                assert article is not None
                self.assertEqual(
                    article.enrichment_attempts,
                    enrichment_module.MAX_ENRICHMENT_ATTEMPTS,
                )
                self.assertEqual(article.enrichment_status, "abandoned")
                self.assertIn(
                    "RuntimeError: blocked by upstream moderation",
                    article.enrichment_error or "",
                )

    def test_enrichment_failure_does_not_stop_other_articles(self) -> None:
        """One failed enrichment no longer aborts the whole batch."""
        with self.session_local() as session:
            session.add(
                make_article(
                    "article-success",
                    parse_status="done",
                )
            )
            session.add(
                make_article(
                    "article-fail",
                    parse_status="done",
                )
            )
            session.commit()

        class MixedEnrichmentService:
            async def enrich_article(self, session, article):
                if article.article_id == "article-fail":
                    article.enrichment_attempts += 1
                    article.enrichment_status = "failed"
                    article.enrichment_error = "RuntimeError: blocked"
                    session.flush()
                    return False
                article.enrichment_status = "done"
                article.title_zh = "ok"
                article.summary_zh = "ok"
                article.cluster_text = "ok"
                article.enrichment_error = None
                session.flush()
                return True

        service = scheduler_module.SchedulerService.__new__(
            scheduler_module.SchedulerService
        )
        service._enrichment_service = MixedEnrichmentService()

        with patch.object(scheduler_module, "SessionLocal", self.session_local):
            enriched_count, skipped_existing = asyncio.run(
                service.enrich_articles(["article-success", "article-fail"])
            )

        self.assertEqual(enriched_count, 1)
        self.assertEqual(skipped_existing, 0)
        with self.session_local() as session:
            success = session.get(Article, "article-success")
            failed = session.get(Article, "article-fail")
            assert success is not None
            assert failed is not None
            self.assertEqual(success.enrichment_status, "done")
            self.assertEqual(failed.enrichment_status, "failed")
            self.assertEqual(failed.enrichment_attempts, 1)

    def test_image_analysis_retry_limit_marks_image_abandoned(self) -> None:
        """Image analysis failures become terminal after three attempts."""
        with self.session_local() as session:
            article = make_article(
                "article-1",
                should_publish=True,
                enrichment_status="done",
                parse_status="done",
            )
            session.add(article)
            session.add(
                ArticleImage(
                    image_id="image-1",
                    article_id=article.article_id,
                    source_url="https://example.com/image-1.jpg",
                    normalized_url="https://example.com/image-1.jpg",
                )
            )
            session.commit()

        service = scheduler_module.ImageAnalysisService.__new__(
            scheduler_module.ImageAnalysisService
        )

        class RejectingClient:
            def __init__(self) -> None:
                self.beta = SimpleNamespace(
                    chat=SimpleNamespace(
                        completions=SimpleNamespace(parse=self.parse)
                    )
                )

            async def parse(self, **kwargs):
                raise RuntimeError("illegal image format")

        service._client = RejectingClient()

        for _ in range(3):
            with self.session_local() as session:
                article = session.get(Article, "article-1")
                image = session.get(ArticleImage, "image-1")
                assert article is not None
                assert image is not None
                result = asyncio.run(
                    service.analyze_image(
                        session,
                        article=article,
                        image=image,
                    )
                )
                session.commit()
                self.assertFalse(result)

        with self.session_local() as session:
            image = session.get(ArticleImage, "image-1")
            assert image is not None
            self.assertEqual(image.visual_attempts, 3)
            self.assertEqual(image.visual_status, "abandoned")
            self.assertIn(
                "RuntimeError: illegal image format",
                image.analysis_metadata_json.get("error", ""),
            )

    def test_pipeline_continues_when_image_analysis_fails(self) -> None:
        """Image analysis failure no longer aborts story persistence."""
        with self.session_local() as session:
            article = make_article(
                "article-1",
                should_publish=True,
                enrichment_status="done",
                parse_status="done",
            )
            session.add(article)
            session.add(
                ArticleImage(
                    image_id="image-1",
                    article_id=article.article_id,
                    source_url="https://example.com/image-1.jpg",
                    normalized_url="https://example.com/image-1.jpg",
                    caption_raw="look 1",
                )
            )
            session.commit()

        class FakeParseService:
            async def parse_articles(self):
                return ParseResult(
                    candidates=0,
                    parsed=0,
                    failed=0,
                    parsed_article_ids=tuple(),
                )

        class FakeEmbeddingService:
            def embed_articles(self, records):
                return [
                    EmbeddedArticle(article=record, embedding=(1.0, 0.0))
                    for record in records
                ]

        class FakeClusterService:
            async def cluster_articles(self, articles):
                return [articles]

        class FakeStoryGenerationService:
            async def generate_stories(self, clusters):
                first_cluster = clusters[0]
                return [
                    StoryDraft(
                        title_zh="story",
                        summary_zh="summary",
                        key_points=("point",),
                        tags=("tag",),
                        category="fashion",
                        article_ids=tuple(
                            item.article.article_id for item in first_cluster
                        ),
                        hero_image_url=None,
                        source_article_count=len(first_cluster),
                    )
                ]

        class FailingImageAnalysisService:
            async def analyze_image(self, session, *, article, image):
                image.visual_attempts += 1
                image.visual_status = "failed"
                image.analysis_metadata_json = {"error": "RuntimeError: image analysis boom"}
                session.flush()
                return False

        class FakeRagService:
            def __init__(self):
                self.called = False

            def upsert_articles(self, article_ids):
                self.called = True
                return rag_module.RagInsertResult(
                    publishable_articles=0,
                    text_units=0,
                    image_units=0,
                    upserted_units=0,
                )

        service = scheduler_module.SchedulerService.__new__(
            scheduler_module.SchedulerService
        )
        service._collection_service = SimpleNamespace()
        service._parse_service = FakeParseService()
        service._enrichment_service = FakeEnrichmentService()
        service._embedding_service = FakeEmbeddingService()
        service._cluster_service = FakeClusterService()
        service._story_generation_service = FakeStoryGenerationService()
        service._image_analysis_service = FailingImageAnalysisService()
        service._article_rag_service = FakeRagService()

        with (
            patch.object(scheduler_module, "SessionLocal", self.session_local),
            patch.object(scheduler_module, "engine", self.engine),
            patch.object(
                scheduler_module,
                "ensure_article_storage_schema",
                lambda bind: None,
            ),
        ):
            result = asyncio.run(service.run_pipeline_once(skip_ingest=True))

        with self.session_local() as session:
            runs = session.scalars(select(PipelineRun)).all()
            stories = session.scalars(select(Story)).all()

        self.assertEqual(result["stories_created"], 1)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, "success")
        self.assertIsNotNone(runs[0].watermark_ingested_at)
        self.assertEqual(len(stories), 1)
        self.assertTrue(service._article_rag_service.called)

    def test_story_persists_before_rag_failure(self) -> None:
        """Story rows persist even if the post-story RAG stage fails."""
        with self.session_local() as session:
            article = make_article(
                "article-1",
                should_publish=True,
                enrichment_status="done",
                parse_status="done",
            )
            session.add(article)
            session.commit()

        class FakeParseService:
            async def parse_articles(self):
                return ParseResult(
                    candidates=0,
                    parsed=0,
                    failed=0,
                    parsed_article_ids=tuple(),
                )

        class FakeEmbeddingService:
            def embed_articles(self, records):
                return [
                    EmbeddedArticle(article=record, embedding=(1.0, 0.0))
                    for record in records
                ]

        class FakeClusterService:
            async def cluster_articles(self, articles):
                return [articles]

        class FakeStoryGenerationService:
            async def generate_stories(self, clusters):
                first_cluster = clusters[0]
                return [
                    StoryDraft(
                        title_zh="story",
                        summary_zh="summary",
                        key_points=("point",),
                        tags=("tag",),
                        category="fashion",
                        article_ids=tuple(
                            item.article.article_id for item in first_cluster
                        ),
                        hero_image_url=None,
                        source_article_count=len(first_cluster),
                    )
                ]

        class FakeImageAnalysisService:
            async def analyze_image(self, session, *, article, image):
                return False

        class FailingRagService:
            def upsert_articles(self, article_ids):
                raise RuntimeError("rag ingest boom")

        service = scheduler_module.SchedulerService.__new__(
            scheduler_module.SchedulerService
        )
        service._collection_service = SimpleNamespace()
        service._parse_service = FakeParseService()
        service._enrichment_service = FakeEnrichmentService()
        service._embedding_service = FakeEmbeddingService()
        service._cluster_service = FakeClusterService()
        service._story_generation_service = FakeStoryGenerationService()
        service._image_analysis_service = FakeImageAnalysisService()
        service._article_rag_service = FailingRagService()

        with (
            patch.object(scheduler_module, "SessionLocal", self.session_local),
            patch.object(scheduler_module, "engine", self.engine),
            patch.object(
                scheduler_module,
                "ensure_article_storage_schema",
                lambda bind: None,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "rag ingest boom"):
                asyncio.run(service.run_pipeline_once(skip_ingest=True))

        with self.session_local() as session:
            runs = session.scalars(select(PipelineRun)).all()
            stories = session.scalars(select(Story)).all()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, "failed")
        self.assertEqual(len(stories), 1)

    def test_analyze_article_images_only_targets_publishable_articles(self) -> None:
        """Image analysis stage ignores non-publishable article images."""
        with self.session_local() as session:
            session.add(
                make_article(
                    "publishable",
                    should_publish=True,
                    enrichment_status="done",
                    parse_status="done",
                )
            )
            session.add(
                make_article(
                    "filtered-out",
                    should_publish=False,
                    enrichment_status="done",
                    parse_status="done",
                )
            )
            session.add(
                ArticleImage(
                    image_id="image-publishable",
                    article_id="publishable",
                    source_url="https://example.com/publishable.jpg",
                    normalized_url="https://example.com/publishable.jpg",
                    caption_raw="publishable image",
                )
            )
            session.add(
                ArticleImage(
                    image_id="image-filtered",
                    article_id="filtered-out",
                    source_url="https://example.com/filtered.jpg",
                    normalized_url="https://example.com/filtered.jpg",
                    caption_raw="filtered image",
                )
            )
            session.commit()

        class RecordingImageAnalysisService:
            def __init__(self):
                self.image_ids: list[str] = []

            async def analyze_image(self, session, *, article, image):
                self.image_ids.append(image.image_id)
                return True

        image_service = RecordingImageAnalysisService()
        service = scheduler_module.SchedulerService.__new__(
            scheduler_module.SchedulerService
        )
        service._image_analysis_service = image_service

        with patch.object(scheduler_module, "SessionLocal", self.session_local):
            analyzed, skipped = asyncio.run(
                service.analyze_article_images(["publishable", "filtered-out"])
            )

        self.assertEqual(analyzed, 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(image_service.image_ids, ["image-publishable"])

    def test_dense_embedding_batches_requests(self) -> None:
        """Dense embedding respects the provider's total-item batch limit."""
        calls: list[int] = []

        def fake_call(**kwargs):
            batch = kwargs["input"]
            calls.append(len(batch))
            return SimpleNamespace(
                output={
                    "embeddings": [
                        {"embedding": [float(index), float(index + 1)]}
                        for index, _ in enumerate(batch)
                    ]
                }
            )

        texts = [f"text-{index}" for index in range(26)]
        with (
            patch.object(
                embedding_module,
                "DENSE_EMBEDDING_CONFIG",
                replace(embedding_module.DENSE_EMBEDDING_CONFIG, batch_size=25),
            ),
            patch.object(embedding_module.MultiModalEmbedding, "call", fake_call),
        ):
            result = embedding_module.generate_dense_embedding(texts)

        self.assertEqual(calls, [20, 6])
        self.assertEqual(len(result), 26)

    def test_dense_embedding_limits_image_items_per_batch(self) -> None:
        """Dense embedding splits mixed batches so each request has at most five images."""
        image_counts: list[int] = []
        batch_sizes: list[int] = []

        def fake_call(**kwargs):
            batch = kwargs["input"]
            batch_sizes.append(len(batch))
            image_counts.append(
                sum(1 for item in batch if isinstance(item, dict) and "image" in item)
            )
            return SimpleNamespace(
                output={
                    "embeddings": [
                        {"embedding": [1.0, 0.0]}
                        for _ in batch
                    ]
                }
            )

        texts = [f"item-{index}" for index in range(10)]
        image_urls = [
            f"https://example.com/{index}.jpg" if index < 6 else None
            for index in range(10)
        ]
        with (
            patch.object(
                embedding_module,
                "DENSE_EMBEDDING_CONFIG",
                replace(embedding_module.DENSE_EMBEDDING_CONFIG, batch_size=20),
            ),
            patch.object(embedding_module.MultiModalEmbedding, "call", fake_call),
        ):
            result = embedding_module.generate_dense_embedding(texts, image_urls)

        self.assertEqual(len(result), 10)
        self.assertEqual(batch_sizes, [5, 5])
        self.assertEqual(image_counts, [5, 1])

    def test_summary_embedding_batches_max_ten(self) -> None:
        """Story summary embeddings are batched with a maximum size of ten."""
        calls: list[int] = []

        def fake_call(**kwargs):
            batch = kwargs["input"]
            if not isinstance(batch, list):
                batch = [batch]
            calls.append(len(batch))
            return SimpleNamespace(
                output={
                    "embeddings": [
                        {"embedding": [1.0, 0.0]}
                        for _ in batch
                    ]
                }
            )

        texts = [f"summary-{index}" for index in range(23)]
        with (
            patch.object(
                embedding_module,
                "DENSE_SUMMARIZATION_EMBEDDING_CONFIG",
                replace(embedding_module.DENSE_SUMMARIZATION_EMBEDDING_CONFIG, batch_size=50),
            ),
            patch.object(embedding_module.TextEmbedding, "call", fake_call),
        ):
            result = embedding_module.generate_article_summary_embeddings(texts)

        self.assertEqual(len(result), 23)
        self.assertEqual(calls, [10, 10, 3])

    def test_cluster_threshold_is_configurable(self) -> None:
        """Lower thresholds split more clusters than higher thresholds."""
        articles = [
            EmbeddedArticle(
                article=make_enriched_record("article-1", hour=1),
                embedding=(1.0, 0.0),
            ),
            EmbeddedArticle(
                article=make_enriched_record("article-2", hour=2),
                embedding=(0.9, math.sqrt(1 - 0.9**2)),
            ),
            EmbeddedArticle(
                article=make_enriched_record("article-3", hour=3),
                embedding=(0.0, 1.0),
            ),
        ]

        low_threshold_service = cluster_module.ArticleClusterService.__new__(
            cluster_module.ArticleClusterService
        )
        low_threshold_service._client = IdentityReviewClient()
        low_threshold_service._distance_threshold = 0.05

        high_threshold_service = cluster_module.ArticleClusterService.__new__(
            cluster_module.ArticleClusterService
        )
        high_threshold_service._client = IdentityReviewClient()
        high_threshold_service._distance_threshold = 0.15

        low_clusters = asyncio.run(low_threshold_service.cluster_articles(articles))
        high_clusters = asyncio.run(high_threshold_service.cluster_articles(articles))

        self.assertGreater(len(low_clusters), len(high_clusters))
        self.assertEqual(len(high_clusters), 2)

    def test_cluster_review_concurrency_is_bounded(self) -> None:
        """Cluster review never exceeds the configured semaphore limit."""
        client = TrackingReviewClient()
        service = cluster_module.ArticleClusterService.__new__(
            cluster_module.ArticleClusterService
        )
        service._client = client
        service._distance_threshold = 0.15

        articles: list[EmbeddedArticle] = []
        for pair_index in range(6):
            first_dimension = pair_index * 2
            second_dimension = first_dimension + 1
            first_embedding = [0.0] * 12
            second_embedding = [0.0] * 12
            first_embedding[first_dimension] = 1.0
            second_embedding[first_dimension] = 0.9
            second_embedding[second_dimension] = math.sqrt(1 - 0.9**2)
            articles.append(
                EmbeddedArticle(
                    article=make_enriched_record(
                        f"article-{pair_index}-a",
                        hour=pair_index,
                    ),
                    embedding=tuple(first_embedding),
                )
            )
            articles.append(
                EmbeddedArticle(
                    article=make_enriched_record(
                        f"article-{pair_index}-b",
                        hour=pair_index,
                    ),
                    embedding=tuple(second_embedding),
                )
            )

        clusters = asyncio.run(service.cluster_articles(articles))

        self.assertEqual(len(clusters), 6)
        self.assertLessEqual(
            client.max_seen,
            cluster_module.CLUSTER_REVIEW_CONCURRENCY,
        )
        self.assertGreaterEqual(client.max_seen, 2)

    def test_rag_upsert_is_idempotent_for_same_articles(self) -> None:
        """Repeated upserts for the same article build the same retrieval units."""
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_service = ArticleMarkdownService(Path(temp_dir))
            relative_path = "2026-03-23/article-1.md"
            markdown_service.write_markdown(
                relative_path=relative_path,
                content="# Test\n\nBody\n",
            )

            with self.session_local() as session:
                article = make_article(
                    "article-1",
                    should_publish=True,
                    enrichment_status="done",
                    parse_status="done",
                    markdown_rel_path=relative_path,
                )
                session.add(article)
                session.add(
                    ArticleImage(
                        image_id="image-1",
                        article_id=article.article_id,
                        source_url="https://example.com/image-1.jpg",
                        normalized_url="https://example.com/image-1.jpg",
                        caption_raw="caption",
                        visual_status="done",
                        observed_description="dress",
                    )
                )
                session.commit()

            class FakeQdrantService:
                def __init__(self):
                    self.calls: list[list[str]] = []

                def upsert_data(self, collection_name, records):
                    self.calls.append(
                        [str(record["retrieval_unit_id"]) for record in records]
                    )
                    return len(records)

            qdrant_service = FakeQdrantService()
            service = rag_module.ArticleRagService.__new__(
                rag_module.ArticleRagService
            )
            service._markdown_service = markdown_service
            service._qdrant_service = qdrant_service
            service._collection_name = rag_module.RAG_COLLECTION_NAME

            with (
                patch.object(rag_module, "SessionLocal", self.session_local),
                patch.object(
                    rag_module,
                    "split_markdown_into_text_chunks",
                    lambda markdown, source_id: [
                        {
                            "page_content": "Body",
                            "metadata": {"chunk_index": 0},
                        }
                    ],
                ),
                patch.object(
                    rag_module,
                    "generate_dense_embedding",
                    lambda texts, image_urls=None: [[1.0, 0.0] for _ in texts],
                ),
                patch.object(
                    rag_module,
                    "generate_sparse_embedding",
                    lambda texts: [{0: 1.0} for _ in texts],
                ),
            ):
                first_result = service.upsert_articles(["article-1"])
                second_result = service.upsert_articles(["article-1"])

        self.assertEqual(first_result.upserted_units, second_result.upserted_units)
        self.assertEqual(first_result.text_units, second_result.text_units)
        self.assertEqual(first_result.image_units, second_result.image_units)
        self.assertEqual(len(qdrant_service.calls), 2)
        self.assertEqual(qdrant_service.calls[0], qdrant_service.calls[1])


if __name__ == "__main__":
    unittest.main()
