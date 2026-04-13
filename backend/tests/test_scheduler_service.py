from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import Article, PipelineRun, ensure_article_storage_schema
from backend.app.service.scheduler_service import (
    ASIA_SHANGHAI,
    PIPELINE_START_HOUR_SHANGHAI,
    SchedulerService,
)

BUSINESS_DAY = date(2026, 4, 7)


def _shanghai_datetime(hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime that corresponds to the given Shanghai local hour on BUSINESS_DAY."""
    local = datetime.combine(BUSINESS_DAY, time(hour, minute), tzinfo=ASIA_SHANGHAI)
    return local.astimezone(UTC)


class SchedulerServiceTest(unittest.TestCase):
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

    def _patch_session_local(self):
        return patch(
            "backend.app.service.scheduler_service.SessionLocal",
            self.session_factory,
        )

    def _insert_pipeline_run(self, *, status: str = "running", metadata: dict | None = None) -> str:
        with self.session_factory() as session:
            run = PipelineRun(
                run_id="run-sched-test",
                business_date=BUSINESS_DAY,
                run_type="digest_daily",
                status=status,
                story_status="done" if status == "done" else "pending",
                digest_status="done" if status == "done" else "pending",
                metadata_json=metadata or {},
            )
            session.add(run)
            session.commit()
            return run.run_id

    def _insert_article(self, *, article_id: str = "art-1", event_frame_status: str = "done") -> None:
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id=article_id,
                    source_name="test-source",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url=f"https://example.com/{article_id}",
                    original_url=f"https://example.com/{article_id}",
                    title_raw="Test Article",
                    summary_raw="summary",
                    ingested_at=_shanghai_datetime(10),
                    parse_status="done",
                    event_frame_status=event_frame_status,
                )
            )
            session.commit()

    @patch("backend.app.service.scheduler_service.datetime")
    def test_skips_before_7am_shanghai_when_no_existing_run(self, mock_dt):
        mock_dt.now.return_value = _shanghai_datetime(6, 30)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        with self._patch_session_local():
            service = SchedulerService()
            result = service.tick()

        self.assertIsNone(result)

    @patch("backend.app.service.scheduler_service.DailyRunCoordinatorService")
    @patch("backend.app.service.scheduler_service.datetime")
    def test_starts_pipeline_at_7am_shanghai(self, mock_dt, mock_coordinator_cls):
        mock_dt.now.return_value = _shanghai_datetime(PIPELINE_START_HOUR_SHANGHAI, 0)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        mock_coordinator = MagicMock()
        mock_coordinator.tick.return_value = "run-new"
        mock_coordinator_cls.return_value = mock_coordinator

        with self._patch_session_local():
            service = SchedulerService()
            result = service.tick()

        self.assertEqual(result, "run-new")
        mock_coordinator.tick.assert_called_once()

    @patch("backend.app.service.scheduler_service.DailyRunCoordinatorService")
    @patch("backend.app.service.scheduler_service.datetime")
    def test_drives_existing_running_pipeline_even_before_7am(self, mock_dt, mock_coordinator_cls):
        """If a pipeline is already running, tick drives it regardless of time."""
        mock_dt.now.return_value = _shanghai_datetime(5, 0)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        mock_coordinator = MagicMock()
        mock_coordinator.tick.return_value = "run-sched-test"
        mock_coordinator_cls.return_value = mock_coordinator

        with self._patch_session_local():
            self._insert_pipeline_run(status="running")
            service = SchedulerService()
            result = service.tick()

        self.assertEqual(result, "run-sched-test")
        mock_coordinator.tick.assert_called_once()

    @patch("backend.app.service.scheduler_service.ArticleRagService")
    @patch("backend.app.service.scheduler_service.datetime")
    def test_upserts_rag_when_pipeline_done(self, mock_dt, mock_rag_cls):
        mock_dt.now.return_value = _shanghai_datetime(12, 0)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        mock_rag = MagicMock()
        mock_rag.upsert_articles.return_value = MagicMock(
            indexed_articles=1,
            text_units=5,
            image_units=2,
            upserted_units=7,
        )
        mock_rag_cls.return_value = mock_rag

        with self._patch_session_local():
            self._insert_pipeline_run(status="done")
            self._insert_article(article_id="art-rag-1", event_frame_status="done")
            service = SchedulerService()
            result = service.tick()

        self.assertEqual(result, "run-sched-test")
        mock_rag.upsert_articles.assert_called_once()
        call_args = mock_rag.upsert_articles.call_args[0][0]
        self.assertIn("art-rag-1", call_args)

        # Verify metadata updated
        with self.session_factory() as session:
            run = session.get(PipelineRun, "run-sched-test")
            self.assertTrue(run.metadata_json.get("rag_upserted"))

    @patch("backend.app.service.scheduler_service.ArticleRagService")
    @patch("backend.app.service.scheduler_service.datetime")
    def test_skips_rag_when_already_upserted(self, mock_dt, mock_rag_cls):
        mock_dt.now.return_value = _shanghai_datetime(12, 0)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        mock_rag = MagicMock()
        mock_rag_cls.return_value = mock_rag

        with self._patch_session_local():
            self._insert_pipeline_run(status="done", metadata={"rag_upserted": True})
            service = SchedulerService()
            result = service.tick()

        self.assertEqual(result, "run-sched-test")
        mock_rag.upsert_articles.assert_not_called()

    @patch("backend.app.service.scheduler_service.ArticleRagService")
    @patch("backend.app.service.scheduler_service.datetime")
    def test_skips_rag_when_pipeline_failed(self, mock_dt, mock_rag_cls):
        mock_dt.now.return_value = _shanghai_datetime(12, 0)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        mock_rag = MagicMock()
        mock_rag_cls.return_value = mock_rag

        with self._patch_session_local():
            self._insert_pipeline_run(status="failed")
            service = SchedulerService()
            result = service.tick()

        self.assertEqual(result, "run-sched-test")
        mock_rag.upsert_articles.assert_not_called()


if __name__ == "__main__":
    unittest.main()
