"""Tests for Celery content tasks and LLM rate-limit integration."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article
from backend.app.schemas.llm.event_frame_extraction import (
    EventFrameExtractionSchema,
    ExtractedEventFrame,
)
from backend.app.service.article_contracts import MarkdownBlock, ParsedArticle
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService
from backend.app.tasks import content_tasks
from backend.app.tasks.celery_app import celery_app


class _FakeLimiter:
    def __init__(self, *, block_first_n: int = 0) -> None:
        self._remaining_blocks = block_first_n
        self.calls = 0

    @contextmanager
    def lease(self, bucket: str) -> Iterator[None]:
        self.calls += 1
        self.last_bucket = bucket
        while self._remaining_blocks > 0:
            self._remaining_blocks -= 1
        yield


class _FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        message = type("Message", (), {"content": content})
        choice = type("Choice", (), {"message": message()})
        self.choices = [choice()]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    async def create(self, **kwargs: object) -> _FakeCompletionResponse:
        del kwargs
        return _FakeCompletionResponse(self._content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


class ContentTasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_all_content_tasks_are_registered(self) -> None:
        self.assertEqual(
            sorted(name for name in celery_app.tasks if name.startswith("content.")),
            [
                "content.collect_source",
                "content.extract_event_frames",
                "content.parse_article",
            ],
        )

    def test_parse_task_marks_article_done_in_eager_mode(self) -> None:
        self._insert_article(article_id="article-1")
        parsed = ParsedArticle(
            title="Parsed title",
            summary="Parsed summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Body text"),),
            images=(),
            published_at=datetime(2026, 3, 27, 9, 0, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "unit-test"},
        )
        original_eager = celery_app.conf.task_always_eager

        async def fake_parse_batches(self, candidates: list[Article]):  # type: ignore[no-untyped-def]
            del self, candidates
            return [("article-1", parsed, None)]

        try:
            celery_app.conf.task_always_eager = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.tasks.content_tasks.SessionLocal",
                    self.session_factory,
                ):
                    with patch(
                        "backend.app.service.article_parse_service.ARTICLE_MARKDOWN_ROOT",
                        Path(self.temp_dir.name),
                    ):
                        with patch(
                            "backend.app.service.article_parse_service.ArticleMarkdownService.write_markdown",
                            return_value=Path(self.temp_dir.name) / "article-1.md",
                        ):
                            with patch(
                                "backend.app.service.article_parse_service.ArticleParseService._parse_batches_with_http_session",
                                new=fake_parse_batches,
                            ):
                                content_tasks.parse_article.delay("article-1")
        finally:
            celery_app.conf.task_always_eager = original_eager

        with self.session_factory() as session:
            article = session.get(Article, "article-1")

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "done")

    def test_rate_limit_wait_does_not_increment_attempts(self) -> None:
        article_id = self._insert_article(
            article_id="article-rate-limit",
            markdown_rel_path="2026-03-27/article-rate-limit.md",
        )
        limiter = _FakeLimiter(block_first_n=2)
        service = EventFrameExtractionService(
            client=_FakeClient(
                """
                {
                  "frames": [
                    {
                      "event_type": "runway_show",
                      "action_text": "show staged",
                      "extraction_confidence": 0.98
                    }
                  ]
                }
                """
            ),
            rate_limiter=limiter,
        )

        with patch.object(
            service._markdown_service,
            "read_markdown",
            return_value="# Title\n\nBody",
        ):
            with self.session_factory() as session:
                article = session.get(Article, article_id)
                asyncio.run(service.extract_frames(session, article))
                session.commit()

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertEqual(article.event_frame_attempts, 0)
        self.assertEqual(article.event_frame_status, "done")
        self.assertEqual(limiter.calls, 1)
        self.assertEqual(limiter.last_bucket, "event_frame_extraction")

    def _insert_article(
        self,
        *,
        article_id: str,
        markdown_rel_path: str | None = None,
    ) -> str:
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
                    markdown_rel_path=markdown_rel_path,
                    published_at=now,
                    discovered_at=now,
                    ingested_at=now,
                    metadata_json={},
                    parse_status="done" if markdown_rel_path else "pending",
                    parse_attempts=0,
                    parse_error=None,
                    parse_updated_at=now,
                    event_frame_status="pending",
                    event_frame_attempts=0,
                    event_frame_error=None,
                    event_frame_updated_at=now,
                )
            )
            session.commit()
        return article_id


if __name__ == "__main__":
    unittest.main()
