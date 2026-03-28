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
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article, PipelineRun, ensure_article_storage_schema
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

    def test_clean_celery_worker_loads_content_and_aggregation_tasks(self) -> None:
        registered_task_names = self._load_task_names_in_clean_python()
        self.assertIn("content.collect_source", registered_task_names)
        self.assertIn("content.parse_article", registered_task_names)
        self.assertIn("content.extract_event_frames", registered_task_names)
        self.assertIn("aggregation.pack_strict_stories_for_day", registered_task_names)
        self.assertIn("aggregation.generate_digests_for_day", registered_task_names)

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

    def _patch_queue_calls(self):
        @contextmanager
        def _manager():
            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.load_source_configs",
                        return_value=[],
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.collect_source.delay",
                        new=self._record_task("content.collect_source"),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.parse_article.delay",
                        new=self._record_task("content.parse_article"),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.extract_event_frames.delay",
                        new=self._record_task("content.extract_event_frames"),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.pack_strict_stories_for_day.delay",
                        new=self._record_task("aggregation.pack_strict_stories_for_day"),
                    )
                )
                stack.enter_context(
                    patch(
                        "backend.app.service.daily_run_coordinator_service.generate_digests_for_day.delay",
                        new=self._record_task("aggregation.generate_digests_for_day"),
                    )
                )
                yield

        return _manager()

    def _record_task(self, task_name: str):
        def _delay(*args: object, **kwargs: object) -> None:
            del args, kwargs
            self.queued_task_names.append(task_name)

        return _delay

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
