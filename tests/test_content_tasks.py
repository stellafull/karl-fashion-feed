"""Tests for Celery content tasks and LLM rate-limit integration."""

from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.config.celery_config import build_celery_broker_url
from backend.app.core.database import Base
from backend.app.models import Article
from backend.app.service.article_contracts import MarkdownBlock, ParsedArticle
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService
from backend.app.service.llm_rate_limiter import LlmRateLimiter


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


class _FakeRedisClient:
    def __init__(self, *, block_first_n: int) -> None:
        self._remaining_blocks = block_first_n
        self.set_call_count = 0
        self.eval_call_count = 0
        self.last_eval_args: tuple[object, ...] | None = None

    def set(self, key: str, token: str, *, nx: bool, ex: int) -> bool | None:
        del key, token, nx, ex
        self.set_call_count += 1
        if self._remaining_blocks > 0:
            self._remaining_blocks -= 1
            return None
        return True

    def eval(self, script: str, keys_count: int, key: str, token: str) -> int:
        self.eval_call_count += 1
        self.last_eval_args = (script, keys_count, key, token)
        return 1


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
        celery_app = self._load_fresh_celery_app()
        self.assertNotIn("backend.app.tasks.content_tasks", sys.modules)

        celery_app.loader.import_default_modules()

        self.assertIn("backend.app.tasks.content_tasks", sys.modules)
        self.assertEqual(
            sorted(name for name in celery_app.tasks if name.startswith("content.")),
            [
                "content.collect_source",
                "content.extract_event_frames",
                "content.parse_article",
            ],
        )

    def test_build_celery_broker_url_encodes_reserved_password_characters(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "REDIS_HOST": "redis.internal",
                "REDIS_PORT": "6380",
                "REDIS_PASSWORD": "pa/ss?#word",
            },
            clear=False,
        ):
            broker_url = build_celery_broker_url()

        self.assertEqual(broker_url, "redis://:pa%2Fss%3F%23word@redis.internal:6380/0")

    def test_parse_task_marks_article_done_in_eager_mode(self) -> None:
        self._insert_article(article_id="article-1")
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()

        parsed = ParsedArticle(
            title="Parsed title",
            summary="Parsed summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Body text"),),
            images=(),
            published_at=datetime(2026, 3, 27, 9, 0, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "unit-test"},
        )
        original_eager = celery_app.conf.task_always_eager
        parse_task = celery_app.tasks["content.parse_article"]

        async def fake_parse_batches(self, candidates: list[Article]):  # type: ignore[no-untyped-def]
            del self, candidates
            return [("article-1", parsed, None)]

        try:
            celery_app.conf.task_always_eager = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
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
                            parse_task.delay("article-1")
        finally:
            celery_app.conf.task_always_eager = original_eager

        with self.session_factory() as session:
            article = session.get(Article, "article-1")

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "done")

    def test_collect_source_task_runs_in_eager_mode(self) -> None:
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        collect_task = celery_app.tasks["content.collect_source"]
        original_eager = celery_app.conf.task_always_eager
        observed: dict[str, object] = {}

        async def fake_collect_source(self, session, *, run_id: str, source_name: str):  # type: ignore[no-untyped-def]
            del self
            observed["session"] = session
            observed["run_id"] = run_id
            observed["source_name"] = source_name
            return None

        try:
            celery_app.conf.task_always_eager = True
            with patch("backend.app.tasks.content_tasks.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.article_collection_service.ArticleCollectionService.collect_source",
                    new=fake_collect_source,
                ):
                    collect_task.delay("vogue-runway", "run-collect-1")
        finally:
            celery_app.conf.task_always_eager = original_eager

        self.assertEqual(observed["run_id"], "run-collect-1")
        self.assertEqual(observed["source_name"], "vogue-runway")
        self.assertIsNotNone(observed["session"])

    def test_extract_event_frames_task_marks_article_done_in_eager_mode(self) -> None:
        article_id = self._insert_article(
            article_id="article-extract-1",
            markdown_rel_path="2026-03-27/article-extract-1.md",
        )
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        extract_task = celery_app.tasks["content.extract_event_frames"]
        original_eager = celery_app.conf.task_always_eager

        async def fake_extract_frames(self, session, article):  # type: ignore[no-untyped-def]
            del self
            article.event_frame_status = "done"
            article.event_frame_error = None
            article.event_frame_updated_at = datetime(2026, 3, 27, 9, 30, tzinfo=UTC).replace(
                tzinfo=None
            )
            session.flush()
            return ()

        try:
            celery_app.conf.task_always_eager = True
            with patch("backend.app.service.event_frame_extraction_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.event_frame_extraction_service.ensure_article_storage_schema",
                    return_value=None,
                ):
                    with patch(
                        "backend.app.service.event_frame_extraction_service.EventFrameExtractionService.extract_frames",
                        new=fake_extract_frames,
                    ):
                        extract_task.delay(article_id)
        finally:
            celery_app.conf.task_always_eager = original_eager

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertIsNotNone(article)
        self.assertEqual(article.event_frame_status, "done")

    def test_custom_client_path_still_builds_redis_rate_limiter(self) -> None:
        fake_client = _FakeClient('{"frames": []}')
        fake_limiter = object()

        with patch(
            "backend.app.service.event_frame_extraction_service.LlmRateLimiter",
            return_value=fake_limiter,
        ) as limiter_factory:
            service = EventFrameExtractionService(client=fake_client)

        self.assertIs(service._rate_limiter, fake_limiter)
        limiter_factory.assert_called_once_with()

    def test_rate_limit_wait_retries_without_incrementing_attempts(self) -> None:
        article_id = self._insert_article(
            article_id="article-rate-limit",
            markdown_rel_path="2026-03-27/article-rate-limit.md",
        )
        fake_redis = _FakeRedisClient(block_first_n=2)
        limiter = LlmRateLimiter(
            redis_client=fake_redis,
            poll_interval_seconds=0.01,
        )
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

        with patch("backend.app.service.llm_rate_limiter.time.sleep") as sleep_mock:
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
        self.assertEqual(fake_redis.set_call_count, 3)
        self.assertEqual(fake_redis.eval_call_count, 1)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertIsNotNone(fake_redis.last_eval_args)
        self.assertEqual(fake_redis.last_eval_args[1], 1)
        self.assertIn("llm-rate-limit:event_frame_extraction", str(fake_redis.last_eval_args[2]))

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

    def _load_fresh_celery_app(self):
        for module_name in (
            "backend.app.tasks.content_tasks",
            "backend.app.tasks.celery_app",
            "backend.app.tasks",
        ):
            sys.modules.pop(module_name, None)
        return importlib.import_module("backend.app.tasks.celery_app").celery_app


if __name__ == "__main__":
    unittest.main()
