from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import Article, PipelineRun, SourceRunState, Story, ensure_article_storage_schema
from backend.app.tasks import aggregation_tasks
from backend.app.service import daily_run_coordinator_service as coordinator_module


class _TaskStub:
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self._calls = calls

    def delay(self, *args: object) -> None:
        self._calls.append(args)


class DailyRunCoordinatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ensure_article_storage_schema(self.engine)
        self.session_factory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _insert_front_stages_drained_state(
        self,
        *,
        run_id: str,
        business_day: date,
    ) -> None:
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=business_day,
                    run_type="digest_daily",
                    status="running",
                    story_status="pending",
                    digest_status="pending",
                    metadata_json={},
                )
            )
            session.add(
                SourceRunState(
                    run_id=run_id,
                    source_name="source-a",
                    status="done",
                    attempts=1,
                    error=None,
                    updated_at=datetime(2026, 3, 30, 1, 0, 0),
                    discovered_count=1,
                    inserted_count=1,
                )
            )
            session.add(
                Article(
                    article_id="article-drained-1",
                    source_name="source-a",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url="https://example.com/article-drained-1",
                    original_url="https://example.com/article-drained-1?ref=test",
                    title_raw="Drained Article",
                    summary_raw="ready",
                    markdown_rel_path="articles/article-drained-1.md",
                    discovered_at=datetime(2026, 3, 30, 0, 1, 0),
                    ingested_at=datetime(2026, 3, 30, 0, 2, 0),
                    parse_status="done",
                    parse_attempts=1,
                    parse_error=None,
                    parse_updated_at=datetime(2026, 3, 30, 0, 3, 0),
                    event_frame_status="done",
                    event_frame_attempts=1,
                    event_frame_error=None,
                    event_frame_updated_at=datetime(2026, 3, 30, 0, 4, 0),
                    metadata_json={},
                )
            )
            session.commit()

    def test_tick_enqueues_story_cluster_task_when_front_stages_drained(self) -> None:
        run_id = "run-drained"
        business_day = date(2026, 3, 30)
        self._insert_front_stages_drained_state(run_id=run_id, business_day=business_day)
        service = coordinator_module.DailyRunCoordinatorService(
            session_factory=self.session_factory,
            source_names=("source-a",),
        )
        observed_at = datetime(2026, 3, 30, 0, 5, tzinfo=UTC)
        expected_business_day_iso = business_day.isoformat()

        cluster_calls: list[tuple[object, ...]] = []
        strict_pack_calls: list[tuple[object, ...]] = []
        digest_calls: list[tuple[object, ...]] = []

        with (
            patch.object(
                coordinator_module,
                "cluster_stories_for_day",
                new=_TaskStub(cluster_calls),
                create=True,
            ),
            patch.object(
                coordinator_module,
                "pack_strict_stories_for_day",
                new=_TaskStub(strict_pack_calls),
                create=True,
            ),
            patch.object(
                coordinator_module,
                "generate_digests_for_day",
                new=_TaskStub(digest_calls),
            ),
            patch.object(
                coordinator_module,
                "load_source_configs",
                return_value=[SimpleNamespace(name="source-a")],
            ),
        ):
            observed_run_id = service.tick(now=observed_at)

        self.assertEqual(run_id, observed_run_id)
        self.assertEqual([(expected_business_day_iso, run_id, 1)], cluster_calls)
        self.assertEqual([], strict_pack_calls)
        self.assertEqual([], digest_calls)

    def test_tick_raises_when_terminal_run_has_story_but_no_digest(self) -> None:
        business_day = date(2026, 3, 30)
        run_id = "run-terminal-empty"
        self._insert_front_stages_drained_state(run_id=run_id, business_day=business_day)
        with self.session_factory() as session:
            run = session.get(PipelineRun, run_id)
            assert run is not None
            run.status = "done"
            run.story_status = "done"
            run.digest_status = "done"
            session.add(
                Story(
                    story_key="story-terminal-empty",
                    business_date=business_day,
                    event_type="runway_show",
                    synopsis_zh="故事",
                    anchor_json={},
                    article_membership_json=[],
                    created_run_id=run_id,
                    clustering_status="done",
                    clustering_error=None,
                )
            )
            session.commit()

        service = coordinator_module.DailyRunCoordinatorService(
            session_factory=self.session_factory,
            source_names=("source-a",),
        )
        with patch.object(
            coordinator_module,
            "load_source_configs",
            return_value=[SimpleNamespace(name="source-a")],
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpectedly empty final digest set"):
                service.tick(now=datetime(2026, 3, 30, 0, 6, tzinfo=UTC))

    def test_drain_until_idle_raises_when_digest_abandoned_with_empty_final_digest_set(self) -> None:
        business_day = date(2026, 3, 30)
        run_id = "run-task5-failed"
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=business_day,
                    run_type="digest_daily",
                    status="failed",
                    story_status="done",
                    digest_status="abandoned",
                    metadata_json={},
                )
            )
            session.add(
                Story(
                    story_key="story-task5-failed",
                    business_date=business_day,
                    event_type="runway_show",
                    synopsis_zh="故事",
                    anchor_json={},
                    article_membership_json=[],
                    created_run_id=run_id,
                    clustering_status="done",
                    clustering_error=None,
                )
            )
            session.commit()

        service = coordinator_module.DailyRunCoordinatorService(
            session_factory=self.session_factory,
            source_names=(),
        )
        with patch.object(service, "tick", return_value=run_id):
            with self.assertRaisesRegex(RuntimeError, "unexpectedly empty final digest set"):
                service.drain_until_idle(
                    run_id=run_id,
                    business_day=business_day,
                    max_ticks=1,
                )

    def test_drain_until_idle_raises_when_digest_done_with_empty_final_digest_set(self) -> None:
        business_day = date(2026, 3, 30)
        run_id = "run-task5-done"
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=business_day,
                    run_type="digest_daily",
                    status="done",
                    story_status="done",
                    digest_status="done",
                    metadata_json={},
                )
            )
            session.add(
                Story(
                    story_key="story-task5-done",
                    business_date=business_day,
                    event_type="runway_show",
                    synopsis_zh="故事",
                    anchor_json={},
                    article_membership_json=[],
                    created_run_id=run_id,
                    clustering_status="done",
                    clustering_error=None,
                )
            )
            session.commit()

        service = coordinator_module.DailyRunCoordinatorService(
            session_factory=self.session_factory,
            source_names=(),
        )
        with patch.object(service, "tick", return_value=run_id):
            with self.assertRaisesRegex(RuntimeError, "unexpectedly empty final digest set"):
                service.drain_until_idle(
                    run_id=run_id,
                    business_day=business_day,
                    max_ticks=1,
                )

    def test_digest_finalize_success_rejects_empty_output_when_stories_exist(self) -> None:
        business_day = date(2026, 3, 30)
        run_id = "run-aggregation-guard"
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=business_day,
                    run_type="digest_daily",
                    status="running",
                    story_status="done",
                    story_token=1,
                    digest_status="running",
                    digest_token=1,
                    metadata_json={},
                )
            )
            session.add(
                Story(
                    story_key="story-aggregation-guard",
                    business_date=business_day,
                    event_type="runway_show",
                    synopsis_zh="故事",
                    anchor_json={},
                    article_membership_json=[],
                    created_run_id=run_id,
                    clustering_status="done",
                    clustering_error=None,
                )
            )
            session.commit()

        with self.session_factory() as session:
            with self.assertRaisesRegex(RuntimeError, "unexpectedly empty final digest set"):
                aggregation_tasks._finalize_batch_stage_success(
                    session=session,
                    run_id=run_id,
                    business_day=business_day,
                    stage="digest",
                    ownership_token=1,
                )
