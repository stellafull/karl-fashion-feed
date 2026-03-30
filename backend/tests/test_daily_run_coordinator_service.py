from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import PipelineRun, Story, ensure_article_storage_schema
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

    def test_tick_enqueues_story_cluster_task_when_front_stages_drained(self) -> None:
        service = coordinator_module.DailyRunCoordinatorService(
            session_factory=self.session_factory,
            source_names=(),
        )
        observed_at = datetime(2026, 3, 30, 0, 5, tzinfo=UTC)
        expected_business_day_iso = date(2026, 3, 30).isoformat()

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
            patch.object(coordinator_module, "load_source_configs", return_value=[]),
        ):
            run_id = service.tick(now=observed_at)

        self.assertEqual([(expected_business_day_iso, run_id, 1)], cluster_calls)
        self.assertEqual([], strict_pack_calls)
        self.assertEqual([], digest_calls)

    def test_drain_until_idle_raises_when_digest_failed_with_empty_final_digest_set(self) -> None:
        business_day = date(2026, 3, 30)
        run_id = "run-task5"
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=business_day,
                    run_type="digest_daily",
                    status="failed",
                    story_status="done",
                    digest_status="failed",
                    metadata_json={},
                )
            )
            session.add(
                Story(
                    story_key="story-task5",
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
