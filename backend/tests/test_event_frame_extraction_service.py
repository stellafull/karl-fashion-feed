from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Article, ArticleEventFrame, ensure_article_storage_schema
from backend.app.schemas.llm.event_frame_extraction import EventFrameExtractionSchema, ExtractedEventFrame
from backend.app.service.article_parse_service import ArticleMarkdownService
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService


class _FakeRateLimiter:
    def __init__(self) -> None:
        self.leased_buckets: list[str] = []

    def lease(self, bucket: str):
        self.leased_buckets.append(bucket)
        return nullcontext()


class _FakeSuccessAgent:
    def __init__(self, payload: EventFrameExtractionSchema) -> None:
        self._payload = payload
        self.invoke_calls = 0

    async def ainvoke(self, _: dict[str, object]) -> dict[str, object]:
        self.invoke_calls += 1
        return {"structured_response": self._payload}


class _FakeFailingAgent:
    def __init__(self, message: str) -> None:
        self._message = message
        self.invoke_calls = 0

    async def ainvoke(self, _: dict[str, object]) -> dict[str, object]:
        self.invoke_calls += 1
        raise RuntimeError(self._message)


def _build_session_with_article(root_path: Path, *, article_id: str) -> tuple[Session, Article]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    article = Article(
        article_id=article_id,
        source_name="Vogue",
        source_type="rss",
        source_lang="en",
        category="fashion",
        canonical_url=f"https://example.com/{article_id}",
        original_url=f"https://example.com/{article_id}",
        title_raw=f"Article {article_id}",
        summary_raw="raw summary",
        markdown_rel_path=f"2026-03-31/{article_id}.md",
        parse_status="done",
        ingested_at=datetime(2026, 3, 31, 1, 0, tzinfo=UTC).replace(tzinfo=None),
    )
    session.add(article)
    (root_path / "2026-03-31").mkdir(parents=True, exist_ok=True)
    (root_path / "2026-03-31" / f"{article_id}.md").write_text("# title\n\nbody\n", encoding="utf-8")
    session.commit()
    return session, article


class EventFrameExtractionServiceTest(unittest.TestCase):
    def test_extract_frames_persists_structured_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            markdown_root = Path(tmp_dir)
            session, article = _build_session_with_article(markdown_root, article_id="article-1")
            self.addCleanup(session.close)
            limiter = _FakeRateLimiter()
            agent = _FakeSuccessAgent(
                EventFrameExtractionSchema(
                    frames=[
                        ExtractedEventFrame(
                            event_type="runway_show",
                            subject_json={"brand": "Acme"},
                            action_text="发布",
                            object_text="新系列",
                            place_text="Paris",
                            collection_text="FW26",
                            season_text="FW26",
                            show_context_text="Paris Fashion Week",
                            evidence_json=[{"quote": "Acme show in Paris"}],
                            signature_json={"brand": "Acme"},
                            extraction_confidence=0.91,
                        )
                    ]
                )
            )
            service = EventFrameExtractionService(
                agent=agent,
                markdown_service=ArticleMarkdownService(root_path=markdown_root),
                rate_limiter=limiter,
            )

            frames = asyncio.run(service.extract_frames(session, article))

            self.assertEqual(1, len(frames))
            self.assertEqual("runway_show", frames[0].event_type)
            self.assertEqual(1, agent.invoke_calls)
            self.assertEqual(["event_frame_extraction"], limiter.leased_buckets)

            persisted_frames = session.scalars(select(ArticleEventFrame)).all()
            self.assertEqual(1, len(persisted_frames))
            persisted_article = session.get(Article, article.article_id)
            self.assertIsNotNone(persisted_article)
            assert persisted_article is not None
            self.assertEqual("done", persisted_article.event_frame_status)
            self.assertEqual(0, persisted_article.event_frame_attempts)
            self.assertIsNone(persisted_article.event_frame_error)

    def test_extract_frames_increments_db_attempt_once_when_inference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            markdown_root = Path(tmp_dir)
            session, article = _build_session_with_article(markdown_root, article_id="article-2")
            self.addCleanup(session.close)
            limiter = _FakeRateLimiter()
            agent = _FakeFailingAgent("agent failed after retries")
            service = EventFrameExtractionService(
                agent=agent,
                markdown_service=ArticleMarkdownService(root_path=markdown_root),
                rate_limiter=limiter,
            )

            frames = asyncio.run(service.extract_frames(session, article))

            self.assertEqual((), frames)
            self.assertEqual(1, agent.invoke_calls)
            self.assertEqual(["event_frame_extraction"], limiter.leased_buckets)
            persisted_article = session.get(Article, article.article_id)
            self.assertIsNotNone(persisted_article)
            assert persisted_article is not None
            self.assertEqual(1, persisted_article.event_frame_attempts)
            self.assertEqual("failed", persisted_article.event_frame_status)
            self.assertIn("RuntimeError: agent failed after retries", persisted_article.event_frame_error or "")
            persisted_frames = session.scalars(select(ArticleEventFrame)).all()
            self.assertEqual([], persisted_frames)
