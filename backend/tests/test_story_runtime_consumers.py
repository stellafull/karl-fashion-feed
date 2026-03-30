from __future__ import annotations

import importlib
import unittest
from datetime import date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.models import (
    Article,
    ArticleEventFrame,
    Digest,
    DigestArticle,
    DigestStory,
    PipelineRun,
    SourceRunState,
    Story,
    StoryArticle,
    StoryFrame,
    ensure_article_storage_schema,
)
from backend.app.service.digest_generation_service import DigestGenerationService, _ResolvedPlan
from backend.app.service.strict_story_packing_service import StrictStoryPackingService, _ResolvedStory


class StoryRuntimeConsumerRenameTest(unittest.TestCase):
    def test_renamed_runtime_consumers_are_importable(self) -> None:
        modules = (
            "backend.app.service.digest_generation_service",
            "backend.app.service.strict_story_packing_service",
            "backend.app.service.daily_run_coordinator_service",
            "backend.app.tasks.aggregation_tasks",
        )

        for module_name in modules:
            with self.subTest(module_name=module_name):
                importlib.import_module(module_name)

    def test_aggregation_metadata_uses_story_stage(self) -> None:
        aggregation_tasks = importlib.import_module("backend.app.tasks.aggregation_tasks")
        run = PipelineRun(
            run_id="run-1",
            business_date=date(2026, 3, 30),
            run_type="digest_daily",
            status="running",
            story_status="failed",
            story_attempts=2,
            story_error="story failed",
            story_updated_at=datetime(2026, 3, 30, 8, 0, 0),
            story_token=3,
            digest_status="pending",
            digest_attempts=0,
            digest_error=None,
            digest_updated_at=datetime(2026, 3, 30, 8, 5, 0),
            digest_token=0,
            started_at=datetime(2026, 3, 30, 7, 0, 0),
            metadata_json={},
        )

        aggregation_tasks._merge_batch_metadata(run)

        self.assertEqual({"failed": 1, "pending": 1}, run.metadata_json["batch_status_counts"])
        self.assertIn("story", run.metadata_json["batch_stage_summary"])
        self.assertNotIn("strict_story", run.metadata_json["batch_stage_summary"])
        self.assertEqual("story failed", run.metadata_json["failure_summary"]["story"])

    def test_daily_run_coordinator_summaries_use_story_stage(self) -> None:
        coordinator_module = importlib.import_module("backend.app.service.daily_run_coordinator_service")
        service = coordinator_module.DailyRunCoordinatorService(
            session_factory=lambda: None,  # type: ignore[arg-type]
        )
        run = PipelineRun(
            run_id="run-2",
            business_date=date(2026, 3, 30),
            run_type="digest_daily",
            status="running",
            story_status="abandoned",
            story_attempts=3,
            story_error="story abandoned",
            story_updated_at=datetime(2026, 3, 30, 8, 0, 0),
            story_token=4,
            digest_status="failed",
            digest_attempts=1,
            digest_error="digest failed",
            digest_updated_at=datetime(2026, 3, 30, 8, 5, 0),
            digest_token=1,
            started_at=datetime(2026, 3, 30, 7, 0, 0),
            metadata_json={},
        )
        article = Article(
            article_id="article-1",
            source_name="source-a",
            source_type="rss",
            source_lang="en",
            category="fashion",
            canonical_url="https://example.com/story",
            original_url="https://example.com/story?ref=1",
            title_raw="Example story",
            summary_raw="preview",
            markdown_rel_path="articles/article-1.md",
            discovered_at=datetime(2026, 3, 30, 7, 20, 0),
            ingested_at=datetime(2026, 3, 30, 7, 30, 0),
            parse_status="failed",
            parse_attempts=1,
            parse_error="parse failed",
            parse_updated_at=datetime(2026, 3, 30, 7, 40, 0),
            event_frame_status="abandoned",
            event_frame_attempts=3,
            event_frame_error="frame failed",
            event_frame_updated_at=datetime(2026, 3, 30, 7, 50, 0),
            metadata_json={},
        )
        source_state = SourceRunState(
            run_id="run-2",
            source_name="source-a",
            status="failed",
            attempts=1,
            error="collect failed",
            updated_at=datetime(2026, 3, 30, 7, 10, 0),
            discovered_count=1,
            inserted_count=0,
        )

        self.assertEqual({"abandoned": 1, "failed": 1}, service._batch_status_counts(run))
        self.assertIn("story", service._batch_stage_summary(run))
        self.assertNotIn("strict_story", service._batch_stage_summary(run))
        self.assertEqual(
            "story abandoned",
            service._failure_summary(
                run,
                source_states=[source_state],
                articles=[article],
            )["story"],
        )

    def test_aggregation_metadata_drops_legacy_strict_story_failure_summary(self) -> None:
        aggregation_tasks = importlib.import_module("backend.app.tasks.aggregation_tasks")
        run = PipelineRun(
            run_id="run-legacy",
            business_date=date(2026, 3, 30),
            run_type="digest_daily",
            status="running",
            story_status="failed",
            story_attempts=1,
            story_error="new story error",
            story_updated_at=datetime(2026, 3, 30, 8, 0, 0),
            story_token=1,
            digest_status="failed",
            digest_attempts=1,
            digest_error="digest error",
            digest_updated_at=datetime(2026, 3, 30, 8, 5, 0),
            digest_token=1,
            started_at=datetime(2026, 3, 30, 7, 0, 0),
            metadata_json={
                "failure_summary": {
                    "strict_story": "stale story error",
                    "sources": {"source-a": "collect failed"},
                }
            },
        )

        aggregation_tasks._merge_batch_metadata(run)

        self.assertEqual({"source-a": "collect failed"}, run.metadata_json["failure_summary"]["sources"])
        self.assertEqual("new story error", run.metadata_json["failure_summary"]["story"])
        self.assertNotIn("strict_story", run.metadata_json["failure_summary"])


class StoryRuntimePersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ensure_article_storage_schema(self.engine)
        self.session = Session(self.engine)
        self.business_day = date(2026, 3, 30)
        self.run = PipelineRun(
            run_id="run-1",
            business_date=self.business_day,
            run_type="digest_daily",
            status="running",
            metadata_json={},
        )
        self.session.add(self.run)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _make_article(
        self,
        *,
        article_id: str,
        source_name: str,
        ingested_at: datetime,
    ) -> Article:
        return Article(
            article_id=article_id,
            source_name=source_name,
            source_type="rss",
            source_lang="en",
            category="fashion",
            canonical_url=f"https://example.com/{article_id}",
            original_url=f"https://example.com/{article_id}?ref=1",
            title_raw=f"Title {article_id}",
            summary_raw=f"Summary {article_id}",
            markdown_rel_path=f"articles/{article_id}.md",
            discovered_at=ingested_at,
            ingested_at=ingested_at,
            metadata_json={},
        )

    def test_strict_story_packing_service_persists_and_loads_story_tables(self) -> None:
        article_one = self._make_article(
            article_id="article-1",
            source_name="source-a",
            ingested_at=datetime(2026, 3, 30, 7, 0, 0),
        )
        article_two = self._make_article(
            article_id="article-2",
            source_name="source-b",
            ingested_at=datetime(2026, 3, 30, 7, 10, 0),
        )
        frame_one = ArticleEventFrame(
            event_frame_id="frame-1",
            article_id="article-1",
            business_date=self.business_day,
            event_type="runway_show",
            signature_json={"brand": "Acme"},
            subject_json={},
            evidence_json=[],
        )
        frame_two = ArticleEventFrame(
            event_frame_id="frame-2",
            article_id="article-2",
            business_date=self.business_day,
            event_type="runway_show",
            signature_json={"brand": "Acme"},
            subject_json={},
            evidence_json=[],
        )
        self.session.add_all([article_one, article_two, frame_one, frame_two])
        self.session.commit()

        service = StrictStoryPackingService()
        resolved = [
            _ResolvedStory(
                story_key="story-1",
                signature_json={
                    "event_type": "runway_show",
                    "signature_json": {"brand": "Acme", "season": "FW26"},
                },
                synopsis_zh="品牌 Acme FW26 时装秀",
                frame_ids=("frame-2", "frame-1"),
                article_ids=("article-2", "article-1"),
                signature_token="token-1",
            )
        ]

        persisted = service._replace_day_rows(
            self.session,
            self.business_day,
            run_id=self.run.run_id,
            resolved=resolved,
        )
        self.session.commit()

        self.assertEqual(["story-1"], [item.story_key for item in persisted])
        stored_story = self.session.scalar(select(Story).where(Story.story_key == "story-1"))
        self.assertIsNotNone(stored_story)
        assert stored_story is not None
        self.assertEqual("runway_show", stored_story.event_type)
        self.assertEqual({"brand": "Acme", "season": "FW26"}, stored_story.anchor_json)
        self.assertEqual(["article-2", "article-1"], stored_story.article_membership_json)

        frame_rows = self.session.execute(
            select(StoryFrame.story_key, StoryFrame.event_frame_id, StoryFrame.rank)
            .where(StoryFrame.story_key == "story-1")
            .order_by(StoryFrame.rank.asc())
        ).all()
        self.assertEqual(
            [("story-1", "frame-2", 0), ("story-1", "frame-1", 1)],
            frame_rows,
        )
        article_rows = self.session.execute(
            select(StoryArticle.story_key, StoryArticle.article_id, StoryArticle.rank)
            .where(StoryArticle.story_key == "story-1")
            .order_by(StoryArticle.rank.asc())
        ).all()
        self.assertEqual(
            [("story-1", "article-2", 0), ("story-1", "article-1", 1)],
            article_rows,
        )

        loaded = service._load_existing_stories(self.session, self.business_day)
        self.assertEqual(1, len(loaded))
        self.assertEqual("story-1", loaded[0].story_key)
        self.assertEqual(("frame-2", "frame-1"), loaded[0].frame_ids)
        self.assertEqual(
            {
                "event_type": "runway_show",
                "signature_json": {"brand": "Acme", "season": "FW26"},
            },
            loaded[0].signature_json,
        )

    def test_digest_generation_service_reads_story_rows_and_writes_digest_story_rows(self) -> None:
        article_one = self._make_article(
            article_id="article-1",
            source_name="source-b",
            ingested_at=datetime(2026, 3, 30, 7, 0, 0),
        )
        article_two = self._make_article(
            article_id="article-2",
            source_name="source-a",
            ingested_at=datetime(2026, 3, 30, 7, 5, 0),
        )
        story = Story(
            story_key="story-1",
            business_date=self.business_day,
            event_type="runway_show",
            synopsis_zh="品牌 Acme FW26 时装秀",
            anchor_json={"brand": "Acme"},
            article_membership_json=["article-2", "article-1"],
            created_run_id=self.run.run_id,
            clustering_status="done",
            clustering_error=None,
        )
        story_article_rows = [
            StoryArticle(story_key="story-1", article_id="article-2", rank=0),
            StoryArticle(story_key="story-1", article_id="article-1", rank=1),
        ]
        self.session.add_all([article_one, article_two, story, *story_article_rows])
        self.session.commit()

        service = DigestGenerationService()
        strict_stories = service._load_day_strict_stories(self.session, self.business_day)

        self.assertEqual(1, len(strict_stories))
        self.assertEqual("story-1", strict_stories[0].strict_story_key)
        self.assertEqual(("article-2", "article-1"), strict_stories[0].article_ids)
        self.assertEqual(("source-a", "source-b"), strict_stories[0].source_names)
        self.assertEqual("runway_show", strict_stories[0].event_type)

        persisted = service._replace_day_digests(
            self.session,
            self.business_day,
            run_id=self.run.run_id,
            plans=[
                _ResolvedPlan(
                    facet="runway",
                    strict_story_keys=("story-1",),
                    title_zh="今日秀场",
                    dek_zh="摘要",
                    body_markdown="正文",
                    article_ids=("article-2", "article-1"),
                    source_names=("source-a", "source-b"),
                )
            ],
        )
        self.session.commit()

        self.assertEqual(1, len(persisted))
        digest_rows = self.session.scalars(select(Digest).order_by(Digest.digest_key.asc())).all()
        self.assertEqual(1, len(digest_rows))
        digest_story_rows = self.session.execute(
            select(DigestStory.digest_key, DigestStory.story_key, DigestStory.rank)
            .order_by(DigestStory.rank.asc())
        ).all()
        self.assertEqual([(digest_rows[0].digest_key, "story-1", 0)], digest_story_rows)
        digest_article_rows = self.session.execute(
            select(DigestArticle.digest_key, DigestArticle.article_id, DigestArticle.rank)
            .order_by(DigestArticle.rank.asc())
        ).all()
        self.assertEqual(
            [
                (digest_rows[0].digest_key, "article-2", 0),
                (digest_rows[0].digest_key, "article-1", 1),
            ],
            digest_article_rows,
        )
