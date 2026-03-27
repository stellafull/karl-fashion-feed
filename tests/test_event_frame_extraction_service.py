"""Tests for sparse per-article event frame extraction."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article, ArticleEventFrame
from backend.app.schemas.llm.event_frame_extraction import (
    EventFrameExtractionSchema,
    ExtractedEventFrame,
)
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService


class StubEventFrameExtractionService(EventFrameExtractionService):
    """Test double that returns a fixed structured extraction payload."""

    def __init__(self, payload: EventFrameExtractionSchema | Exception) -> None:
        self._payload = payload
        super().__init__()

    async def _infer_frames(self, article: Article) -> EventFrameExtractionSchema:
        del article
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class EventFrameExtractionServiceTest(unittest.TestCase):
    """Verify sparse frame extraction status transitions and persistence."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

    def _insert_article(
        self,
        *,
        parse_status: str = "done",
        event_frame_attempts: int = 0,
        event_frame_status: str = "pending",
    ) -> str:
        article_id = str(uuid4())
        now = datetime(2026, 3, 27, 8, 0, tzinfo=UTC).replace(tzinfo=None)
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
                    markdown_rel_path="2026-03-27/article.md",
                    published_at=now,
                    discovered_at=now,
                    ingested_at=now,
                    metadata_json={},
                    parse_status=parse_status,
                    parse_attempts=1,
                    parse_error=None,
                    parse_updated_at=now,
                    event_frame_status=event_frame_status,
                    event_frame_attempts=event_frame_attempts,
                    event_frame_error=None,
                    event_frame_updated_at=now,
                )
            )
            session.commit()
        return article_id

    def test_extract_event_frames_caps_output_at_three(self) -> None:
        payload = EventFrameExtractionSchema(
            frames=[
                ExtractedEventFrame(event_type="brand_appointment", extraction_confidence=0.99),
                ExtractedEventFrame(event_type="runway_show", extraction_confidence=0.95),
                ExtractedEventFrame(event_type="campaign_launch", extraction_confidence=0.91),
                ExtractedEventFrame(event_type="store_opening", extraction_confidence=0.90),
            ]
        )
        service = StubEventFrameExtractionService(payload)
        article_id = self._insert_article()

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            frames = asyncio.run(service.extract_frames(session, article))
            session.commit()

        self.assertEqual(len(frames), 3)
        self.assertTrue(all(isinstance(frame, ArticleEventFrame) for frame in frames))

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            stored_frames = session.scalars(
                select(ArticleEventFrame).where(ArticleEventFrame.article_id == article_id)
            ).all()

        self.assertEqual(len(stored_frames), 3)
        self.assertEqual(article.event_frame_status, "done")

    def test_zero_frames_is_a_valid_done_state(self) -> None:
        service = StubEventFrameExtractionService(EventFrameExtractionSchema(frames=[]))
        article_id = self._insert_article()

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            frames = asyncio.run(service.extract_frames(session, article))
            session.commit()

        self.assertEqual(frames, ())

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            stored_frames = session.scalars(
                select(ArticleEventFrame).where(ArticleEventFrame.article_id == article_id)
            ).all()

        self.assertEqual(stored_frames, [])
        self.assertEqual(article.event_frame_status, "done")

    def test_event_frame_failure_becomes_abandoned_after_three_attempts(self) -> None:
        service = StubEventFrameExtractionService(RuntimeError("boom"))
        article_id = self._insert_article(event_frame_attempts=2, event_frame_status="failed")

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            result = asyncio.run(service.extract_frames(session, article))
            session.commit()

        self.assertEqual(result, ())

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            stored_frames = session.scalars(
                select(ArticleEventFrame).where(ArticleEventFrame.article_id == article_id)
            ).all()

        self.assertEqual(stored_frames, [])
        self.assertEqual(article.event_frame_attempts, 3)
        self.assertEqual(article.event_frame_status, "abandoned")
        self.assertEqual(article.event_frame_error, "RuntimeError: boom")
