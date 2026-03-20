from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Article, PipelineRun, Story, StoryArticle, ensure_article_storage_schema
from backend.app.service.article_cluster_service import EmbeddedArticle
from backend.app.service.article_enrichment_service import EnrichedArticle
from backend.app.service.scheduler_service import BEIJING_TIMEZONE, SchedulerService
from backend.app.service.story_generation_service import StoryDraft


class StubEnrichmentService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def enrich_article(self, session, article: Article) -> bool:
        del session
        self.calls.append(article.article_id)
        article.should_publish = article.article_id != "article-3"
        article.title_zh = f"标题-{article.article_id}"
        article.summary_zh = f"摘要-{article.article_id}"
        article.tags_json = ["时尚"]
        article.brands_json = ["Karl"]
        article.category_candidates_json = [article.category]
        article.reject_reason = "" if article.article_id != "article-3" else "广告"
        article.cluster_text = f"{article.title_zh}\n{article.summary_zh}"
        article.enrichment_status = "done"
        article.enriched_at = datetime(2026, 3, 13, 8, 30, 0)
        article.enrichment_error = None
        return True

    @staticmethod
    def to_record(article: Article) -> EnrichedArticle:
        return EnrichedArticle(
            article_id=article.article_id,
            title_zh=article.title_zh or "",
            summary_zh=article.summary_zh or "",
            tags=tuple(article.tags_json or []),
            brands=tuple(article.brands_json or []),
            category_candidates=tuple(article.category_candidates_json or []),
            cluster_text=article.cluster_text or "",
            published_at=article.published_at,
            ingested_at=article.ingested_at,
            hero_image_url=article.image_url,
            source_name=article.source_name,
        )


class StubEmbeddingService:
    def embed_articles(self, articles: list[EnrichedArticle]) -> list[EmbeddedArticle]:
        vectors = {
            "article-1": (1.0, 0.0),
            "article-2": (0.99, 0.01),
            "article-4": (-1.0, 0.0),
        }
        return [
            EmbeddedArticle(article=article, embedding=vectors[article.article_id])
            for article in articles
        ]


class StubClusterService:
    async def cluster_articles(self, articles: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
        return [articles] if articles else []


class StubStoryGenerationService:
    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error

    async def generate_stories(self, clusters: list[list[EmbeddedArticle]]) -> list[StoryDraft]:
        if self.raise_error:
            raise RuntimeError("story generation failed")
        return [
            StoryDraft(
                title_zh="聚合话题",
                summary_zh="两篇报道聚合成一个话题。",
                key_points=("要点一", "要点二"),
                tags=("时尚",),
                category="高端时装",
                article_ids=tuple(item.article.article_id for item in cluster),
                hero_image_url=cluster[0].article.hero_image_url,
                source_article_count=len(cluster),
            )
            for cluster in clusters
        ]


class SchedulerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        ensure_article_storage_schema(self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def _seed_articles(self) -> None:
        with self.session_factory() as session:
            session.add_all(
                [
                    Article(
                        article_id="article-1",
                        source_name="Vogue",
                        source_type="rss",
                        source_lang="en",
                        category="高端时装",
                        canonical_url="https://example.com/1",
                        original_url="https://example.com/1",
                        title_raw="Story 1",
                        summary_raw="Summary 1",
                        image_url="https://example.com/1.jpg",
                        published_at=datetime(2026, 3, 13, 8, 0, 0),
                        ingested_at=datetime(2026, 3, 13, 8, 5, 0),
                        parse_status="done",
                    ),
                    Article(
                        article_id="article-2",
                        source_name="WWD",
                        source_type="rss",
                        source_lang="en",
                        category="高端时装",
                        canonical_url="https://example.com/2",
                        original_url="https://example.com/2",
                        title_raw="Story 2",
                        summary_raw="Summary 2",
                        image_url="https://example.com/2.jpg",
                        published_at=datetime(2026, 3, 13, 7, 0, 0),
                        ingested_at=datetime(2026, 3, 13, 8, 6, 0),
                        parse_status="done",
                    ),
                    Article(
                        article_id="article-3",
                        source_name="Ads",
                        source_type="rss",
                        source_lang="en",
                        category="行业动态",
                        canonical_url="https://example.com/3",
                        original_url="https://example.com/3",
                        title_raw="Story 3",
                        summary_raw="Summary 3",
                        published_at=datetime(2026, 3, 13, 6, 0, 0),
                        ingested_at=datetime(2026, 3, 13, 8, 7, 0),
                        parse_status="done",
                    ),
                ]
            )
            session.commit()

    def test_next_run_at_returns_same_day_target_before_beijing_8am(self) -> None:
        service = SchedulerService(
            session_factory=self.session_factory,
            now_factory=lambda: datetime(2026, 3, 20, 23, 30, tzinfo=UTC),
        )

        next_run = service.next_run_at()

        self.assertEqual(next_run, datetime(2026, 3, 21, 8, 0, 0, tzinfo=BEIJING_TIMEZONE))

    def test_next_run_at_rolls_to_next_day_after_beijing_8am(self) -> None:
        service = SchedulerService(
            session_factory=self.session_factory,
            now_factory=lambda: datetime(2026, 3, 21, 1, 5, tzinfo=UTC),
        )

        next_run = service.next_run_at()

        self.assertEqual(next_run, datetime(2026, 3, 22, 8, 0, 0, tzinfo=BEIJING_TIMEZONE))

    def test_seconds_until_next_run_uses_beijing_clock(self) -> None:
        service = SchedulerService(
            session_factory=self.session_factory,
            now_factory=lambda: datetime(2026, 3, 20, 23, 30, tzinfo=UTC),
        )

        self.assertEqual(service.seconds_until_next_run(), 30 * 60)

    def test_run_pipeline_once_creates_story_and_advances_watermark(self) -> None:
        self._seed_articles()
        service = SchedulerService(
            session_factory=self.session_factory,
            enrichment_service=StubEnrichmentService(),
            embedding_service=StubEmbeddingService(),
            cluster_service=StubClusterService(),
            story_generation_service=StubStoryGenerationService(),
        )

        with patch("backend.app.service.scheduler_service.ensure_article_storage_schema"), patch(
            "backend.app.service.scheduler_service.Base.metadata.create_all"
        ):
            result = asyncio.run(service.run_pipeline_once(skip_ingest=True))

        self.assertEqual(result.candidates, 3)
        self.assertEqual(result.enriched, 3)
        self.assertEqual(result.published, 2)
        self.assertEqual(result.stories_created, 1)
        self.assertEqual(result.watermark_ingested_at, datetime(2026, 3, 13, 8, 7, 0))
        self.assertEqual(result.story_grouping_mode, "incremental_ingested_at")
        self.assertEqual(
            result.stages_completed,
            (
                "enrichment",
                "story_embedding",
                "semantic_cluster",
                "cluster_review",
                "story_generation",
                "story_persist",
            ),
        )
        self.assertEqual(result.stages_skipped, ("collection", "parse"))

        with self.session_factory() as session:
            stories = session.scalars(select(Story)).all()
            story_articles = session.scalars(select(StoryArticle)).all()
            runs = session.scalars(select(PipelineRun)).all()

        self.assertEqual(len(stories), 1)
        self.assertEqual(len(story_articles), 2)
        self.assertEqual(runs[-1].status, "success")
        self.assertEqual(
            runs[-1].metadata_json["stages_completed"],
            [
                "enrichment",
                "story_embedding",
                "semantic_cluster",
                "cluster_review",
                "story_generation",
                "story_persist",
            ],
        )

    def test_run_pipeline_once_marks_failed_without_persisting_story(self) -> None:
        self._seed_articles()
        service = SchedulerService(
            session_factory=self.session_factory,
            enrichment_service=StubEnrichmentService(),
            embedding_service=StubEmbeddingService(),
            cluster_service=StubClusterService(),
            story_generation_service=StubStoryGenerationService(raise_error=True),
        )

        with patch("backend.app.service.scheduler_service.ensure_article_storage_schema"), patch(
            "backend.app.service.scheduler_service.Base.metadata.create_all"
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(service.run_pipeline_once(skip_ingest=True))

        with self.session_factory() as session:
            stories = session.scalars(select(Story)).all()
            run = session.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc())).first()

        self.assertEqual(stories, [])
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "failed")

    def test_run_forever_waits_and_runs_one_cycle(self) -> None:
        service = SchedulerService(
            session_factory=self.session_factory,
            enrichment_service=StubEnrichmentService(),
            embedding_service=StubEmbeddingService(),
            cluster_service=StubClusterService(),
            story_generation_service=StubStoryGenerationService(),
            now_factory=lambda: datetime(2026, 3, 20, 23, 30, tzinfo=UTC),
            sleep_func=self._build_sleep_recorder(),
        )

        with patch("backend.app.service.scheduler_service.ensure_article_storage_schema"), patch(
            "backend.app.service.scheduler_service.Base.metadata.create_all"
        ), patch.object(service, "run_pipeline_once", return_value=None) as run_mock:
            asyncio.run(
                service.run_forever(
                    skip_ingest=False,
                    source_names=["Vogue"],
                    limit_sources=1,
                    max_cycles=1,
                )
            )

        self.assertEqual(self.sleep_calls, [30 * 60])
        run_mock.assert_awaited_once_with(
            skip_ingest=False,
            source_names=["Vogue"],
            limit_sources=1,
        )

    def _build_sleep_recorder(self):
        self.sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            self.sleep_calls.append(seconds)

        return fake_sleep


if __name__ == "__main__":
    unittest.main()
