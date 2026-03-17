from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Article, PipelineRun, Story, StoryArticle, ensure_article_storage_schema
from backend.app.service.daily_pipeline_service import DailyPipelineService
from backend.app.service.story_pipeline_contracts import EmbeddedArticle, EnrichedArticleRecord, StoryDraft


class StubEnrichmentService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @staticmethod
    def is_complete(article: Article) -> bool:
        return article.enrichment_status == "done" and bool(article.cluster_text)

    def enrich_article(self, session, article: Article) -> bool:
        if self.is_complete(article):
            return False
        self.calls.append(article.article_id)
        self.apply_result(
            article,
            {
                "should_publish": article.article_id != "article-3",
                "title_zh": f"标题-{article.article_id}",
                "summary_zh": f"摘要-{article.article_id}",
                "tags": ["时尚"],
                "brands": ["Karl"],
                "category_candidates": [article.category],
                "reject_reason": "" if article.article_id != "article-3" else "广告",
            },
        )
        session.flush()
        return True

    @staticmethod
    def apply_result(article: Article, result: dict[str, object]) -> None:
        article.should_publish = bool(result["should_publish"])
        article.title_zh = str(result["title_zh"])
        article.summary_zh = str(result["summary_zh"])
        article.tags_json = list(result["tags"])
        article.brands_json = list(result["brands"])
        article.category_candidates_json = list(result["category_candidates"])
        article.reject_reason = str(result["reject_reason"])
        article.cluster_text = f"{article.title_zh}\n{article.summary_zh}"
        article.enrichment_status = "done"
        article.enriched_at = datetime(2026, 3, 13, 8, 30, 0)
        article.enrichment_error = None

    @staticmethod
    def apply_failure(article: Article, error: Exception) -> None:
        article.enrichment_status = "failed"
        article.enriched_at = datetime(2026, 3, 13, 8, 30, 0)
        article.enrichment_error = f"{error.__class__.__name__}: {error}"

    @staticmethod
    def to_record(article: Article) -> EnrichedArticleRecord:
        return EnrichedArticleRecord(
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
    def embed_articles(self, articles: list[EnrichedArticleRecord]) -> list[EmbeddedArticle]:
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
    def cluster_articles(self, articles: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
        return [articles] if articles else []


class StubStoryGenerationService:
    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error

    def generate_story(self, cluster: list[EmbeddedArticle]) -> StoryDraft:
        if self.raise_error:
            raise RuntimeError("story generation failed")
        return StoryDraft(
            title_zh="聚合话题",
            summary_zh="两篇报道聚合成一个话题。",
            key_points=("要点一", "要点二"),
            tags=("时尚",),
            category="高端时装",
            article_ids=tuple(item.article.article_id for item in cluster),
            hero_image_url=cluster[0].article.hero_image_url,
            source_article_count=len(cluster),
        )


class DailyPipelineServiceTest(unittest.TestCase):
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
                    ),
                ]
            )
            session.commit()

    def test_run_creates_story_and_advances_watermark(self) -> None:
        self._seed_articles()
        service = DailyPipelineService(
            session_factory=self.session_factory,
            enrichment_service=StubEnrichmentService(),
            embedding_service=StubEmbeddingService(),
            cluster_service=StubClusterService(),
            story_generation_service=StubStoryGenerationService(),
        )

        result = service.run(skip_ingest=True)

        self.assertEqual(result.candidates, 3)
        self.assertEqual(result.enriched, 3)
        self.assertEqual(result.published, 2)
        self.assertEqual(result.stories_created, 1)
        self.assertEqual(result.watermark_ingested_at, datetime(2026, 3, 13, 8, 7, 0))
        self.assertEqual(result.story_grouping_mode, "incremental_ingested_at")
        self.assertIsNone(result.story_date)
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
        self.assertEqual(result.stages_skipped, ("ingestion",))

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
        self.assertEqual(runs[-1].metadata_json["story_grouping_mode"], "incremental_ingested_at")
        self.assertEqual(runs[-1].metadata_json["stages_skipped"], ["ingestion"])

        second_result = service.run(skip_ingest=True)
        self.assertEqual(second_result.candidates, 0)
        self.assertEqual(second_result.stories_created, 0)
        self.assertEqual(second_result.stages_completed, ())
        self.assertEqual(
            second_result.stages_skipped,
            (
                "ingestion",
                "enrichment",
                "story_embedding",
                "semantic_cluster",
                "cluster_review",
                "story_generation",
                "story_persist",
            ),
        )

    def test_run_marks_failed_without_persisting_story(self) -> None:
        self._seed_articles()
        service = DailyPipelineService(
            session_factory=self.session_factory,
            enrichment_service=StubEnrichmentService(),
            embedding_service=StubEmbeddingService(),
            cluster_service=StubClusterService(),
            story_generation_service=StubStoryGenerationService(raise_error=True),
        )

        with self.assertRaises(RuntimeError):
            service.run(skip_ingest=True)

        with self.session_factory() as session:
            stories = session.scalars(select(Story)).all()
            run = session.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc())).first()

        self.assertEqual(stories, [])
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "failed")

if __name__ == "__main__":
    unittest.main()
