"""Tests for source-level collection backed by runtime state."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article, PipelineRun, SourceRunState, ensure_article_storage_schema
from backend.app.service.article_collection_service import ArticleCollectionService
from backend.app.service.article_contracts import CollectedArticle


class SourceCollectionServiceTest(unittest.TestCase):
    """Verify one-source collection updates SourceRunState."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        ensure_article_storage_schema(self.engine)

    def test_collect_source_persists_articles_and_marks_source_done(self) -> None:
        service = ArticleCollectionService()
        service._collector = SimpleNamespace(
            collect_articles=AsyncMock(
                return_value=[
                    self._build_article("https://example.com/a"),
                    self._build_article("https://example.com/b"),
                ]
            )
        )

        with self.session_factory() as session:
            self._add_pipeline_run(session)

            result = asyncio.run(
                service.collect_source(
                    session,
                    run_id="run-1",
                    source_name="Vogue",
                )
            )
            session.rollback()

        with self.session_factory() as verification_session:
            state = verification_session.get(
                SourceRunState,
                {"run_id": "run-1", "source_name": "Vogue"},
            )
            inserted_articles = verification_session.scalar(select(func.count()).select_from(Article))

        self.assertEqual(result.inserted, 2)
        self.assertEqual(inserted_articles, 2)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "done")
        self.assertEqual(state.inserted_count, 2)

    def test_collect_source_dedupes_by_canonical_url_inside_one_run(self) -> None:
        service = ArticleCollectionService()
        service._collector = SimpleNamespace(
            collect_articles=AsyncMock(
                return_value=[
                    self._build_article("https://example.com/a"),
                    self._build_article("https://example.com/a"),
                    self._build_article("https://example.com/b"),
                ]
            )
        )

        with self.session_factory() as session:
            self._add_pipeline_run(session)

            result = asyncio.run(
                service.collect_source(
                    session,
                    run_id="run-1",
                    source_name="Vogue",
                )
            )

        self.assertEqual(result.skipped_in_batch, 1)

    def test_collect_source_marks_state_running_before_collection_starts(self) -> None:
        service = ArticleCollectionService()

        async def fake_collect_articles(*, source_names, limit_sources):  # type: ignore[no-untyped-def]
            del source_names, limit_sources
            state = session.get(SourceRunState, {"run_id": "run-1", "source_name": "Vogue"})
            self.assertIsNotNone(state)
            self.assertEqual(state.status, "running")
            return [self._build_article("https://example.com/a")]

        service._collector = SimpleNamespace(collect_articles=fake_collect_articles)

        with self.session_factory() as session:
            self._add_pipeline_run(session)
            result = asyncio.run(
                service.collect_source(
                    session,
                    run_id="run-1",
                    source_name="Vogue",
                )
            )
            session.rollback()

        self.assertEqual(result.inserted, 1)

    def test_collect_source_abandons_after_third_failure(self) -> None:
        service = ArticleCollectionService()
        service._collector = SimpleNamespace(
            collect_articles=AsyncMock(side_effect=RuntimeError("boom"))
        )

        with self.session_factory() as session:
            self._add_pipeline_run(session)
            session.add(
                SourceRunState(
                    run_id="run-1",
                    source_name="Vogue",
                    status="failed",
                    attempts=2,
                )
            )
            session.commit()

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    service.collect_source(
                        session,
                        run_id="run-1",
                        source_name="Vogue",
                    )
                )
            session.rollback()

        with self.session_factory() as verification_session:
            refreshed = verification_session.get(
                SourceRunState,
                {"run_id": "run-1", "source_name": "Vogue"},
            )

        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.status, "abandoned")

    def test_collect_source_rejects_dirty_caller_session(self) -> None:
        service = ArticleCollectionService()
        collector = AsyncMock(return_value=[self._build_article("https://example.com/a")])
        service._collector = SimpleNamespace(collect_articles=collector)

        with self.session_factory() as session:
            self._add_pipeline_run(session)
            session.add(
                PipelineRun(
                    run_id="run-2",
                    business_date=date(2026, 3, 27),
                )
            )

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    service.collect_source(
                        session,
                        run_id="run-1",
                        source_name="Vogue",
                    )
                )
            session.rollback()

        with self.session_factory() as verification_session:
            unrelated_run = verification_session.get(PipelineRun, "run-2")
            state = verification_session.get(
                SourceRunState,
                {"run_id": "run-1", "source_name": "Vogue"},
            )
            inserted_articles = verification_session.scalar(select(func.count()).select_from(Article))

        self.assertEqual(collector.await_count, 0)
        self.assertIsNone(unrelated_run)
        self.assertIsNone(state)
        self.assertEqual(inserted_articles, 0)

    def test_collect_source_rejects_source_already_marked_done(self) -> None:
        service = ArticleCollectionService()
        collector = AsyncMock(return_value=[self._build_article("https://example.com/a")])
        service._collector = SimpleNamespace(collect_articles=collector)

        with self.session_factory() as session:
            self._add_pipeline_run(session)
            session.add(
                SourceRunState(
                    run_id="run-1",
                    source_name="Vogue",
                    status="done",
                    discovered_count=5,
                    inserted_count=2,
                )
            )
            session.commit()

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    service.collect_source(
                        session,
                        run_id="run-1",
                        source_name="Vogue",
                    )
                )
            session.rollback()

        with self.session_factory() as verification_session:
            state = verification_session.get(
                SourceRunState,
                {"run_id": "run-1", "source_name": "Vogue"},
            )

        self.assertEqual(collector.await_count, 0)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "done")
        self.assertEqual(state.discovered_count, 5)
        self.assertEqual(state.inserted_count, 2)

    def test_collect_source_rejects_open_transaction_with_raw_sql_write(self) -> None:
        service = ArticleCollectionService()
        collector = AsyncMock(return_value=[self._build_article("https://example.com/a")])
        service._collector = SimpleNamespace(collect_articles=collector)

        with self.session_factory() as session:
            self._add_pipeline_run(session)
            session.execute(
                text(
                    """
                    INSERT INTO pipeline_run (
                        run_id,
                        business_date,
                        run_type,
                        status,
                        strict_story_status,
                        strict_story_attempts,
                        strict_story_error,
                        strict_story_updated_at,
                        digest_status,
                        digest_attempts,
                        digest_error,
                        digest_updated_at,
                        started_at,
                        finished_at,
                        metadata_json
                    ) VALUES (
                        'run-2',
                        '2026-03-27',
                        'digest_daily',
                        'pending',
                        'pending',
                        0,
                        NULL,
                        '2026-03-27 08:00:00',
                        'pending',
                        0,
                        NULL,
                        '2026-03-27 08:00:00',
                        '2026-03-27 08:00:00',
                        NULL,
                        '{}'
                    )
                    """
                )
            )

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    service.collect_source(
                        session,
                        run_id="run-1",
                        source_name="Vogue",
                    )
                )
            session.rollback()

        with self.session_factory() as verification_session:
            unrelated_run = verification_session.get(PipelineRun, "run-2")
            state = verification_session.get(
                SourceRunState,
                {"run_id": "run-1", "source_name": "Vogue"},
            )
            inserted_articles = verification_session.scalar(select(func.count()).select_from(Article))

        self.assertEqual(collector.await_count, 0)
        self.assertIsNone(unrelated_run)
        self.assertIsNone(state)
        self.assertEqual(inserted_articles, 0)

    def _add_pipeline_run(self, session) -> None:
        session.add(
            PipelineRun(
                run_id="run-1",
                business_date=date(2026, 3, 26),
            )
        )
        session.commit()

    @staticmethod
    def _build_article(canonical_url: str) -> CollectedArticle:
        return CollectedArticle(
            source_name="Vogue",
            source_type="rss",
            lang="en",
            category="fashion",
            url=f"{canonical_url}?raw=1",
            canonical_url=canonical_url,
            title=f"Title for {canonical_url}",
            summary="Summary",
            published_at=None,
            metadata={"section": "runway"},
        )
