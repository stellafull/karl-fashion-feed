"""Tests for daily coordinator control-plane runtime logic."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article, PipelineRun, SourceRunState, ensure_article_storage_schema
from backend.app.models.runtime import DEFAULT_STALE_STATE_TIMEOUT, _utcnow_naive
from backend.app.service.daily_run_coordinator_service import DailyRunCoordinatorService


class DailyRunCoordinatorServiceTest(unittest.TestCase):
    """Verify run bootstrap, stale reclaim, and batch gating."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        ensure_article_storage_schema(self.engine)
        self.fixed_now = datetime(2026, 3, 25, 18, 0, tzinfo=UTC)
        self.queued_task_names: list[str] = []
        self._commit_events: list[str] = []

    def test_tick_creates_or_resumes_the_current_business_day_run(self) -> None:
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)

        with self._patch_queue_calls():
            run_id = coordinator.tick(now=self.fixed_now)
            resumed_run_id = coordinator.tick(now=self.fixed_now)

        self.assertEqual(run_id, resumed_run_id)

        with self.session_factory() as session:
            run = session.get(PipelineRun, run_id)

        self.assertIsNotNone(run)
        self.assertEqual(run.business_date, date(2026, 3, 26))

    def test_tick_requeues_retryable_failed_article_stages(self) -> None:
        article_id = self._insert_article(
            article_id="article-parse-retry",
            ingested_at=datetime(2026, 3, 25, 18, 15, tzinfo=UTC).replace(tzinfo=None),
            parse_status="failed",
            parse_attempts=1,
        )
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)

        with self._patch_queue_calls():
            coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "queued")
        self.assertEqual(self.queued_task_names, ["content.parse_article"])

    def test_tick_publishes_tasks_only_after_runtime_commit(self) -> None:
        article_id = self._insert_article(
            article_id="article-after-commit",
            ingested_at=datetime(2026, 3, 25, 18, 15, tzinfo=UTC).replace(tzinfo=None),
            parse_status="failed",
            parse_attempts=1,
        )
        coordinator = DailyRunCoordinatorService(session_factory=self._session_factory_with_commit_tracking())

        with self._patch_queue_calls(assert_committed=True):
            coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertEqual(self.queued_task_names, ["content.parse_article"])
        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "queued")

    def test_tick_reclaims_stale_running_rows_before_requeue(self) -> None:
        article_id = self._insert_article(
            article_id="article-stale-event-frame",
            ingested_at=datetime(2026, 3, 25, 18, 30, tzinfo=UTC).replace(tzinfo=None),
            parse_status="abandoned",
            parse_attempts=3,
            event_frame_status="running",
            event_frame_updated_at=(
                self.fixed_now - DEFAULT_STALE_STATE_TIMEOUT - timedelta(minutes=1)
            ).replace(tzinfo=None),
        )
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)

        with self._patch_queue_calls():
            coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertIsNotNone(article)
        self.assertEqual(article.event_frame_status, "failed")
        self.assertEqual(article.event_frame_attempts, 1)

    def test_tick_reclaims_stale_queued_article_without_burning_attempts(self) -> None:
        article_id = self._insert_article(
            article_id="article-stale-queued-parse",
            ingested_at=datetime(2026, 3, 25, 18, 20, tzinfo=UTC).replace(tzinfo=None),
            parse_status="queued",
            parse_attempts=1,
            parse_updated_at=(
                self.fixed_now - DEFAULT_STALE_STATE_TIMEOUT - timedelta(minutes=1)
            ).replace(tzinfo=None),
        )
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)

        with self._patch_queue_calls():
            coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "queued")
        self.assertEqual(article.parse_attempts, 1)
        self.assertEqual(self.queued_task_names, ["content.parse_article"])

    def test_tick_reclaims_stale_queued_source_without_burning_attempts(self) -> None:
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)
        run_id = self._insert_pipeline_run()
        stale_updated_at = (
            self.fixed_now - DEFAULT_STALE_STATE_TIMEOUT - timedelta(minutes=1)
        ).replace(tzinfo=None)
        with self.session_factory() as session:
            session.add(
                SourceRunState(
                    run_id=run_id,
                    source_name="Vogue",
                    status="queued",
                    attempts=1,
                    updated_at=stale_updated_at,
                )
            )
            session.commit()

        with self._patch_queue_calls(source_names=["Vogue"]):
            resumed_run_id = coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            state = session.get(SourceRunState, {"run_id": resumed_run_id, "source_name": "Vogue"})

        self.assertEqual(resumed_run_id, run_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "queued")
        self.assertEqual(state.attempts, 1)
        self.assertEqual(self.queued_task_names, ["content.collect_source"])

    def test_tick_reclaims_stale_running_pack_stage_and_requeues_it(self) -> None:
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)
        self._insert_article(
            article_id="article-pack-stale-running",
            ingested_at=datetime(2026, 3, 25, 18, 20, tzinfo=UTC).replace(tzinfo=None),
            parse_status="done",
            event_frame_status="done",
        )
        run_id = self._insert_pipeline_run(
            strict_story_status="running",
            strict_story_updated_at=(
                self.fixed_now - DEFAULT_STALE_STATE_TIMEOUT - timedelta(minutes=1)
            ).replace(tzinfo=None),
        )

        with self._patch_queue_calls():
            resumed_run_id = coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            run = session.get(PipelineRun, resumed_run_id)

        self.assertEqual(resumed_run_id, run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run.strict_story_status, "queued")
        self.assertEqual(run.strict_story_attempts, 1)
        self.assertEqual(self.queued_task_names, ["aggregation.pack_strict_stories_for_day"])

    def test_tick_reclaims_stale_queued_digest_without_burning_attempts(self) -> None:
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)
        self._insert_article(
            article_id="article-digest-stale-queued",
            ingested_at=datetime(2026, 3, 25, 18, 25, tzinfo=UTC).replace(tzinfo=None),
            parse_status="done",
            event_frame_status="done",
        )
        run_id = self._insert_pipeline_run(
            strict_story_status="done",
            digest_status="queued",
            digest_attempts=1,
            digest_updated_at=(
                self.fixed_now - DEFAULT_STALE_STATE_TIMEOUT - timedelta(minutes=1)
            ).replace(tzinfo=None),
        )

        with self._patch_queue_calls():
            resumed_run_id = coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            run = session.get(PipelineRun, resumed_run_id)

        self.assertEqual(resumed_run_id, run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run.digest_status, "queued")
        self.assertEqual(run.digest_attempts, 1)
        self.assertEqual(self.queued_task_names, ["aggregation.generate_digests_for_day"])

    def test_tick_enqueues_pack_then_generate_only_once_drained(self) -> None:
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)
        self._insert_article(
            article_id="article-drained",
            ingested_at=datetime(2026, 3, 25, 18, 20, tzinfo=UTC).replace(tzinfo=None),
            parse_status="done",
            event_frame_status="done",
        )

        with self._patch_queue_calls():
            run_id = coordinator.tick(now=self.fixed_now)

        with self.session_factory() as session:
            run = session.get(PipelineRun, run_id)
            run.strict_story_status = "done"
            run.digest_status = "failed"
            run.strict_story_updated_at = _utcnow_naive()
            run.digest_updated_at = _utcnow_naive()
            session.commit()

        with self._patch_queue_calls():
            coordinator.tick(now=self.fixed_now)

        self.assertEqual(self.queued_task_names[0], "aggregation.pack_strict_stories_for_day")
        self.assertEqual(self.queued_task_names[-1], "aggregation.generate_digests_for_day")

    def test_tick_does_not_enqueue_digest_when_front_stage_reopens(self) -> None:
        coordinator = DailyRunCoordinatorService(session_factory=self.session_factory)
        self._insert_article(
            article_id="article-parse-reopened",
            ingested_at=datetime(2026, 3, 25, 18, 22, tzinfo=UTC).replace(tzinfo=None),
            parse_status="failed",
            parse_attempts=1,
        )
        self._insert_pipeline_run(
            strict_story_status="done",
            digest_status="failed",
        )

        with self._patch_queue_calls():
            coordinator.tick(now=self.fixed_now)

        self.assertEqual(self.queued_task_names, ["content.parse_article"])

    def test_clean_celery_worker_loads_content_and_aggregation_tasks(self) -> None:
        registered_task_names = self._load_task_names_in_clean_python()
        self.assertIn("content.collect_source", registered_task_names)
        self.assertIn("content.parse_article", registered_task_names)
        self.assertIn("content.extract_event_frames", registered_task_names)
        self.assertIn("aggregation.pack_strict_stories_for_day", registered_task_names)
        self.assertIn("aggregation.generate_digests_for_day", registered_task_names)

    def _session_factory_with_commit_tracking(self):
        commit_events: list[str] = []

        def _factory():
            session = self.session_factory()

            @event.listens_for(session, "after_commit")
            def _after_commit(committed_session) -> None:
                del committed_session
                commit_events.append("committed")

            return session

        self._commit_events = commit_events
        return _factory

    def _insert_article(
        self,
        *,
        article_id: str,
        ingested_at: datetime,
        parse_status: str = "pending",
        parse_attempts: int = 0,
        parse_updated_at: datetime | None = None,
        event_frame_status: str = "pending",
        event_frame_attempts: int = 0,
        event_frame_updated_at: datetime | None = None,
    ) -> str:
        timestamp = ingested_at
        parse_updated_at = parse_updated_at or timestamp
        event_frame_updated_at = event_frame_updated_at or timestamp
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id=article_id,
                    source_name="Vogue Runway",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url=f"https://example.com/{article_id}",
                    original_url=f"https://example.com/original/{article_id}",
                    title_raw="Original title",
                    summary_raw="Original summary",
                    markdown_rel_path="2026-03-26/article.md" if parse_status == "done" else None,
                    published_at=timestamp,
                    discovered_at=timestamp,
                    ingested_at=timestamp,
                    metadata_json={},
                    parse_status=parse_status,
                    parse_attempts=parse_attempts,
                    parse_error=None,
                    parse_updated_at=parse_updated_at,
                    event_frame_status=event_frame_status,
                    event_frame_attempts=event_frame_attempts,
                    event_frame_error=None,
                    event_frame_updated_at=event_frame_updated_at,
                )
            )
            session.commit()
        return article_id

    def _patch_queue_calls(
        self,
        *,
        source_names: list[str] | None = None,
        assert_committed: bool = False,
    ):
        @contextmanager
        def _manager():
            configured_source_names = [] if source_names is None else source_names

            def _assert_commit_boundary() -> None:
                if assert_committed:
                    self.assertEqual(self._commit_events, ["committed"])

            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.load_source_configs",
                        return_value=[
                            type("SourceConfig", (), {"name": name})()
                            for name in configured_source_names
                        ],
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.collect_source.delay",
                        new=self._record_task("content.collect_source", before_record=_assert_commit_boundary),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.parse_article.delay",
                        new=self._record_task("content.parse_article", before_record=_assert_commit_boundary),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.extract_event_frames.delay",
                        new=self._record_task(
                            "content.extract_event_frames",
                            before_record=_assert_commit_boundary,
                        ),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.pack_strict_stories_for_day.delay",
                        new=self._record_task(
                            "aggregation.pack_strict_stories_for_day",
                            before_record=_assert_commit_boundary,
                        ),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.generate_digests_for_day.delay",
                        new=self._record_task(
                            "aggregation.generate_digests_for_day",
                            before_record=_assert_commit_boundary,
                        ),
                    )
                )
                yield

        return _manager()

    def _record_task(self, task_name: str, *, before_record=None):
        def _delay(*args: object, **kwargs: object) -> None:
            del args, kwargs
            if before_record is not None:
                before_record()
            self.queued_task_names.append(task_name)

        return _delay

    def _insert_pipeline_run(
        self,
        *,
        strict_story_status: str = "pending",
        strict_story_attempts: int = 0,
        strict_story_error: str | None = None,
        strict_story_updated_at: datetime | None = None,
        digest_status: str = "pending",
        digest_attempts: int = 0,
        digest_error: str | None = None,
        digest_updated_at: datetime | None = None,
    ) -> str:
        run_id = "run-test"
        observed_at = self.fixed_now.replace(tzinfo=None)
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=date(2026, 3, 26),
                    run_type="digest_daily",
                    status="running",
                    strict_story_status=strict_story_status,
                    strict_story_attempts=strict_story_attempts,
                    strict_story_error=strict_story_error,
                    strict_story_updated_at=strict_story_updated_at or observed_at,
                    digest_status=digest_status,
                    digest_attempts=digest_attempts,
                    digest_error=digest_error,
                    digest_updated_at=digest_updated_at or observed_at,
                    started_at=observed_at,
                    metadata_json={},
                )
            )
            session.commit()
        return run_id

    @staticmethod
    def _load_task_names_in_clean_python() -> set[str]:
        command = """
import importlib
import json

celery_app = importlib.import_module("backend.app.tasks.celery_app").celery_app
celery_app.loader.import_default_modules()
task_names = sorted(name for name in celery_app.tasks if "." in name)
print(json.dumps(task_names))
"""
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
        )
        return set(json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
