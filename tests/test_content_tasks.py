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
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.config.celery_config import build_celery_broker_url, build_celery_settings
from backend.app.core.database import Base
from backend.app.models import (
    Article,
    ArticleEventFrame,
    Digest,
    PipelineRun,
    StrictStory,
    ensure_article_storage_schema,
)
from backend.app.service.article_contracts import MarkdownBlock, ParsedArticle
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService
from backend.app.service.llm_rate_limiter import LlmRateLimiter
from backend.app.schemas.llm.event_frame_extraction import EventFrameExtractionSchema
from backend.app.tasks.aggregation_tasks import generate_digests_for_day, pack_strict_stories_for_day


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
        ensure_article_storage_schema(self.engine)
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

    def test_aggregation_tasks_route_to_aggregation_queue(self) -> None:
        settings = build_celery_settings()
        task_routes = settings["task_routes"]

        self.assertEqual(task_routes["aggregation.pack_strict_stories_for_day"]["queue"], "aggregation")
        self.assertEqual(task_routes["aggregation.generate_digests_for_day"]["queue"], "aggregation")

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

    def test_parse_task_marks_article_running_before_parse_work_starts(self) -> None:
        self._insert_article(article_id="article-running-1", parse_status="queued")
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates
        parse_task = celery_app.tasks["content.parse_article"]
        test_case = self
        parsed = ParsedArticle(
            title="Running parsed title",
            summary="Running parsed summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Running body text"),),
            images=(),
            published_at=datetime(2026, 3, 27, 9, 10, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "running-unit-test"},
        )

        async def fake_parse_batches(self, candidates: list[Article]):  # type: ignore[no-untyped-def]
            del self
            test_case.assertEqual(len(candidates), 1)
            test_case.assertEqual(candidates[0].article_id, "article-running-1")
            test_case.assertEqual(candidates[0].parse_status, "running")
            return [(candidates[0].article_id, parsed, None)]

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.article_parse_service.ARTICLE_MARKDOWN_ROOT",
                    Path(self.temp_dir.name),
                ):
                    with patch(
                        "backend.app.service.article_parse_service.ArticleMarkdownService.write_markdown",
                        return_value=Path(self.temp_dir.name) / "article-running-1.md",
                    ):
                        with patch(
                            "backend.app.service.article_parse_service.ArticleParseService._parse_batches_with_http_session",
                            new=fake_parse_batches,
                        ):
                            parse_task.delay("article-running-1")
        finally:
            celery_app.conf.task_always_eager = original_eager
            celery_app.conf.task_eager_propagates = original_propagates

        with self.session_factory() as session:
            article = session.get(Article, "article-running-1")

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "done")
        self.assertEqual(article.parse_attempts, 0)

    def test_parse_task_accepts_coordinator_queued_article_in_eager_mode(self) -> None:
        self._insert_article(article_id="article-queued-1", parse_status="queued")
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()

        parsed = ParsedArticle(
            title="Queued parsed title",
            summary="Queued parsed summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Queued body text"),),
            images=(),
            published_at=datetime(2026, 3, 27, 9, 5, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "queued-unit-test"},
        )
        original_eager = celery_app.conf.task_always_eager
        parse_task = celery_app.tasks["content.parse_article"]

        async def fake_parse_batches(self, candidates: list[Article]):  # type: ignore[no-untyped-def]
            del self, candidates
            return [("article-queued-1", parsed, None)]

        try:
            celery_app.conf.task_always_eager = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.article_parse_service.ARTICLE_MARKDOWN_ROOT",
                    Path(self.temp_dir.name),
                ):
                    with patch(
                        "backend.app.service.article_parse_service.ArticleMarkdownService.write_markdown",
                        return_value=Path(self.temp_dir.name) / "article-queued-1.md",
                    ):
                        with patch(
                            "backend.app.service.article_parse_service.ArticleParseService._parse_batches_with_http_session",
                            new=fake_parse_batches,
                        ):
                            parse_task.delay("article-queued-1")
        finally:
            celery_app.conf.task_always_eager = original_eager

        with self.session_factory() as session:
            article = session.get(Article, "article-queued-1")

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "done")

    def test_parse_task_rejects_already_running_article_claim(self) -> None:
        self._insert_article(article_id="article-running-claimed", parse_status="running")
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        parse_task = celery_app.tasks["content.parse_article"]
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
                with self.assertRaises(RuntimeError):
                    parse_task.delay("article-running-claimed")
        finally:
            celery_app.conf.task_always_eager = original_eager
            celery_app.conf.task_eager_propagates = original_propagates

    def test_parse_task_rejects_late_completion_after_ownership_moves(self) -> None:
        article_id = self._insert_article(article_id="article-parse-late-owner", parse_status="queued")
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        parse_task = celery_app.tasks["content.parse_article"]
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates
        test_case = self
        claimed_by_retry_at = datetime(2026, 3, 27, 9, 12, tzinfo=UTC).replace(tzinfo=None)
        parsed = ParsedArticle(
            title="Late owner parsed title",
            summary="Late owner parsed summary",
            markdown_blocks=(MarkdownBlock(kind="paragraph", text="Late owner body"),),
            images=(),
            published_at=datetime(2026, 3, 27, 9, 11, tzinfo=UTC).replace(tzinfo=None),
            metadata={"parser": "late-owner"},
        )

        async def fake_parse_batches(self, candidates: list[Article]):  # type: ignore[no-untyped-def]
            del self
            with test_case.session_factory() as competing_session:
                article = competing_session.get(Article, article_id)
                test_case.assertIsNotNone(article)
                article.parse_status = "running"
                article.parse_error = None
                article.parse_updated_at = claimed_by_retry_at
                competing_session.commit()
            return [(candidates[0].article_id, parsed, None)]

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.article_parse_service.ARTICLE_MARKDOWN_ROOT",
                    Path(self.temp_dir.name),
                ):
                    with patch(
                        "backend.app.service.article_parse_service.ArticleParseService._parse_batches_with_http_session",
                        new=fake_parse_batches,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "parse ownership lost before finalize"):
                            parse_task.delay(article_id)
        finally:
            celery_app.conf.task_always_eager = original_eager
            celery_app.conf.task_eager_propagates = original_propagates

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        expected_markdown_path = Path(self.temp_dir.name) / "2026-03-27" / f"{article_id}.md"
        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "running")
        self.assertEqual(article.parse_updated_at, claimed_by_retry_at)
        self.assertIsNone(article.markdown_rel_path)
        self.assertFalse(expected_markdown_path.exists())

    def test_parse_task_rejects_late_failure_after_ownership_moves(self) -> None:
        article_id = self._insert_article(article_id="article-parse-late-failure", parse_status="queued")
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        parse_task = celery_app.tasks["content.parse_article"]
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates
        test_case = self
        claimed_by_retry_at = datetime(2026, 3, 27, 9, 13, tzinfo=UTC).replace(tzinfo=None)

        async def fake_parse_batches(self, candidates: list[Article]):  # type: ignore[no-untyped-def]
            del self
            with test_case.session_factory() as competing_session:
                article = competing_session.get(Article, article_id)
                test_case.assertIsNotNone(article)
                article.parse_status = "running"
                article.parse_error = None
                article.parse_updated_at = claimed_by_retry_at
                competing_session.commit()
            return [(candidates[0].article_id, None, RuntimeError("late parse boom"))]

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
            with patch("backend.app.service.article_parse_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.article_parse_service.ArticleParseService._parse_batches_with_http_session",
                    new=fake_parse_batches,
                ):
                    with self.assertRaisesRegex(RuntimeError, "parse ownership lost before finalize"):
                        parse_task.delay(article_id)
        finally:
            celery_app.conf.task_always_eager = original_eager
            celery_app.conf.task_eager_propagates = original_propagates

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertIsNotNone(article)
        self.assertEqual(article.parse_status, "running")
        self.assertEqual(article.parse_updated_at, claimed_by_retry_at)
        self.assertEqual(article.parse_attempts, 0)
        self.assertIsNone(article.parse_error)

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

        async def fake_extract_frames(
            self,
            session,
            article,
            *,
            claimed_updated_at=None,
        ):  # type: ignore[no-untyped-def]
            del self, claimed_updated_at
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

    def test_extract_event_frames_task_marks_article_running_before_work_starts(self) -> None:
        article_id = self._insert_article(
            article_id="article-extract-running-1",
            markdown_rel_path="2026-03-27/article-extract-running-1.md",
        )
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        extract_task = celery_app.tasks["content.extract_event_frames"]
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates
        test_case = self

        async def fake_extract_frames(
            self,
            session,
            article,
            *,
            claimed_updated_at=None,
        ):  # type: ignore[no-untyped-def]
            del self, session, claimed_updated_at
            test_case.assertEqual(article.event_frame_status, "running")
            article.event_frame_status = "done"
            article.event_frame_updated_at = datetime(2026, 3, 27, 9, 31, tzinfo=UTC).replace(
                tzinfo=None
            )
            return ()

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
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
            celery_app.conf.task_eager_propagates = original_propagates

        with self.session_factory() as session:
            article = session.get(Article, article_id)

        self.assertIsNotNone(article)
        self.assertEqual(article.event_frame_status, "done")

    def test_extract_event_frames_task_rejects_already_running_article_claim(self) -> None:
        article_id = self._insert_article(
            article_id="article-extract-claimed",
            markdown_rel_path="2026-03-27/article-extract-claimed.md",
        )
        with self.session_factory() as session:
            article = session.get(Article, article_id)
            article.event_frame_status = "running"
            article.event_frame_attempts = 1
            session.commit()

        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        extract_task = celery_app.tasks["content.extract_event_frames"]
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
            with patch("backend.app.service.event_frame_extraction_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.event_frame_extraction_service.ensure_article_storage_schema",
                    return_value=None,
                ):
                    with self.assertRaises(RuntimeError):
                        extract_task.delay(article_id)
        finally:
            celery_app.conf.task_always_eager = original_eager
            celery_app.conf.task_eager_propagates = original_propagates

    def test_extract_event_frames_task_rejects_late_completion_after_ownership_moves(self) -> None:
        article_id = self._insert_article(
            article_id="article-extract-late-owner",
            markdown_rel_path="2026-03-27/article-extract-late-owner.md",
        )
        celery_app = self._load_fresh_celery_app()
        celery_app.loader.import_default_modules()
        extract_task = celery_app.tasks["content.extract_event_frames"]
        original_eager = celery_app.conf.task_always_eager
        original_propagates = celery_app.conf.task_eager_propagates
        claimed_by_retry_at = datetime(2026, 3, 27, 9, 32, tzinfo=UTC).replace(tzinfo=None)
        test_case = self

        async def fake_infer_frames(self, article):  # type: ignore[no-untyped-def]
            del self, article
            with test_case.session_factory() as competing_session:
                competing_article = competing_session.get(Article, article_id)
                test_case.assertIsNotNone(competing_article)
                competing_article.event_frame_status = "running"
                competing_article.event_frame_error = None
                competing_article.event_frame_updated_at = claimed_by_retry_at
                competing_session.commit()
            return EventFrameExtractionSchema.model_validate(
                {
                    "frames": [
                        {
                            "event_type": "runway_show",
                            "action_text": "show staged",
                            "extraction_confidence": 0.97,
                        }
                    ]
                }
            )

        try:
            celery_app.conf.task_always_eager = True
            celery_app.conf.task_eager_propagates = True
            with patch("backend.app.service.event_frame_extraction_service.SessionLocal", self.session_factory):
                with patch(
                    "backend.app.service.event_frame_extraction_service.ensure_article_storage_schema",
                    return_value=None,
                ):
                    with patch(
                        "backend.app.service.event_frame_extraction_service.EventFrameExtractionService._infer_frames",
                        new=fake_infer_frames,
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "event frame ownership lost before finalize",
                        ):
                            extract_task.delay(article_id)
        finally:
            celery_app.conf.task_always_eager = original_eager
            celery_app.conf.task_eager_propagates = original_propagates

        with self.session_factory() as session:
            article = session.get(Article, article_id)
            frames = session.scalars(
                select(ArticleEventFrame).where(ArticleEventFrame.article_id == article_id)
            ).all()

        self.assertIsNotNone(article)
        self.assertEqual(article.event_frame_status, "running")
        self.assertEqual(article.event_frame_updated_at, claimed_by_retry_at)
        self.assertEqual(frames, [])

    def test_pack_task_rejects_superseded_token_before_work_starts(self) -> None:
        session_factory = self._build_file_session_factory("pack-superseded-before-start.db")
        self._insert_pipeline_run(
            session_factory,
            run_id="run-pack-before-start",
            business_date_value=datetime(2026, 3, 27, 0, 0, tzinfo=UTC).date(),
            strict_story_status="queued",
            strict_story_token=2,
        )

        with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
            with patch("backend.app.tasks.aggregation_tasks.ensure_article_storage_schema", return_value=None):
                with patch(
                    "backend.app.tasks.aggregation_tasks.StrictStoryPackingService.pack_business_day"
                ) as pack_mock:
                    with self.assertRaises(RuntimeError):
                        pack_strict_stories_for_day("2026-03-27", "run-pack-before-start", 1)

        pack_mock.assert_not_called()

    def test_pack_task_rolls_back_output_after_losing_ownership(self) -> None:
        session_factory = self._build_file_session_factory("pack-ownership-loss.db")
        business_day = datetime(2026, 3, 27, 0, 0, tzinfo=UTC).date()
        self._insert_pipeline_run(
            session_factory,
            run_id="run-pack-ownership-loss",
            business_date_value=business_day,
            strict_story_status="queued",
            strict_story_token=1,
        )

        async def fake_pack(self, session, business_day, *, run_id):  # type: ignore[no-untyped-def]
            del self
            session.add(
                StrictStory(
                    strict_story_key="story-old-owner",
                    business_date=business_day,
                    synopsis_zh="old owner output",
                    signature_json={"event_type": "show"},
                    frame_membership_json=[],
                    created_run_id=run_id,
                    packing_status="done",
                    packing_error=None,
                )
            )
            with session_factory() as competing_session:
                competing_run = competing_session.get(PipelineRun, run_id)
                competing_run.strict_story_status = "queued"
                competing_run.strict_story_token = 2
                competing_run.strict_story_updated_at = datetime(2026, 3, 27, 9, 1, tzinfo=UTC).replace(
                    tzinfo=None
                )
                competing_session.commit()
            return []

        with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
            with patch("backend.app.tasks.aggregation_tasks.ensure_article_storage_schema", return_value=None):
                with patch(
                    "backend.app.tasks.aggregation_tasks.StrictStoryPackingService.pack_business_day",
                    new=fake_pack,
                ):
                    with self.assertRaises(RuntimeError):
                        pack_strict_stories_for_day("2026-03-27", "run-pack-ownership-loss", 1)

        with session_factory() as session:
            run = session.get(PipelineRun, "run-pack-ownership-loss")
            stories = session.query(StrictStory).all()

        self.assertIsNotNone(run)
        self.assertEqual(run.strict_story_status, "queued")
        self.assertEqual(run.strict_story_token, 2)
        self.assertEqual(stories, [])

    def test_pack_task_marks_run_failed_immediately_when_stage_becomes_abandoned(self) -> None:
        session_factory = self._build_file_session_factory("pack-abandoned-terminal.db")
        business_day = datetime(2026, 3, 27, 0, 0, tzinfo=UTC).date()
        self._insert_pipeline_run(
            session_factory,
            run_id="run-pack-abandoned",
            business_date_value=business_day,
            strict_story_status="queued",
            strict_story_token=1,
        )
        with session_factory() as session:
            run = session.get(PipelineRun, "run-pack-abandoned")
            run.strict_story_attempts = 2
            session.commit()

        async def fake_pack(self, session, business_day, *, run_id):  # type: ignore[no-untyped-def]
            del self, session, business_day, run_id
            raise RuntimeError("pack boom")

        with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
            with patch("backend.app.tasks.aggregation_tasks.ensure_article_storage_schema", return_value=None):
                with patch(
                    "backend.app.tasks.aggregation_tasks.StrictStoryPackingService.pack_business_day",
                    new=fake_pack,
                ):
                    with self.assertRaises(RuntimeError):
                        pack_strict_stories_for_day("2026-03-27", "run-pack-abandoned", 1)

        with session_factory() as session:
            run = session.get(PipelineRun, "run-pack-abandoned")

        self.assertIsNotNone(run)
        self.assertEqual(run.strict_story_status, "abandoned")
        self.assertEqual(run.strict_story_attempts, 3)
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(
            run.metadata_json,
            {
                "batch_status_counts": {"abandoned": 1, "pending": 1},
                "batch_stage_summary": {
                    "strict_story": {
                        "status": "abandoned",
                        "attempts": 3,
                        "error": "RuntimeError: pack boom",
                    },
                    "digest": {
                        "status": "pending",
                        "attempts": 0,
                        "error": None,
                    },
                },
                "failure_summary": {
                    "strict_story": "RuntimeError: pack boom",
                    "digest": None,
                },
            },
        )

    def test_digest_task_refreshes_metadata_immediately_on_success(self) -> None:
        session_factory = self._build_file_session_factory("digest-success-metadata.db")
        business_day = datetime(2026, 3, 27, 0, 0, tzinfo=UTC).date()
        self._insert_pipeline_run(
            session_factory,
            run_id="run-digest-success",
            business_date_value=business_day,
            strict_story_status="done",
            strict_story_token=1,
            digest_status="queued",
            digest_token=1,
        )

        async def fake_generate(self, session, business_day, *, run_id):  # type: ignore[no-untyped-def]
            del self, session, business_day, run_id
            return []

        with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
            with patch("backend.app.tasks.aggregation_tasks.ensure_article_storage_schema", return_value=None):
                with patch(
                    "backend.app.tasks.aggregation_tasks.DigestGenerationService.generate_for_day",
                    new=fake_generate,
                ):
                    generate_digests_for_day("2026-03-27", "run-digest-success", 1)

        with session_factory() as session:
            run = session.get(PipelineRun, "run-digest-success")

        self.assertIsNotNone(run)
        self.assertEqual(run.digest_status, "done")
        self.assertEqual(run.status, "done")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(
            run.metadata_json,
            {
                "batch_status_counts": {"done": 2},
                "batch_stage_summary": {
                    "strict_story": {
                        "status": "done",
                        "attempts": 0,
                        "error": None,
                    },
                    "digest": {
                        "status": "done",
                        "attempts": 0,
                        "error": None,
                    },
                },
                "failure_summary": {
                    "strict_story": None,
                    "digest": None,
                },
            },
        )

    def test_digest_task_preserves_existing_front_stage_failure_summary(self) -> None:
        session_factory = self._build_file_session_factory("digest-preserve-front-failures.db")
        business_day = datetime(2026, 3, 27, 0, 0, tzinfo=UTC).date()
        self._insert_pipeline_run(
            session_factory,
            run_id="run-digest-preserve-front-failures",
            business_date_value=business_day,
            strict_story_status="done",
            strict_story_token=1,
            digest_status="queued",
            digest_token=1,
            metadata_json={
                "failure_summary": {
                    "sources": {"Vogue": "RuntimeError: source boom"},
                    "parse": {"article-1": "RuntimeError: parse boom"},
                    "event_frame": {"article-2": "RuntimeError: frame boom"},
                    "strict_story": None,
                    "digest": None,
                }
            },
        )

        async def fake_generate(self, session, business_day, *, run_id):  # type: ignore[no-untyped-def]
            del self, session, business_day, run_id
            return []

        with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
            with patch("backend.app.tasks.aggregation_tasks.ensure_article_storage_schema", return_value=None):
                with patch(
                    "backend.app.tasks.aggregation_tasks.DigestGenerationService.generate_for_day",
                    new=fake_generate,
                ):
                    generate_digests_for_day("2026-03-27", "run-digest-preserve-front-failures", 1)

        with session_factory() as session:
            run = session.get(PipelineRun, "run-digest-preserve-front-failures")

        self.assertIsNotNone(run)
        self.assertEqual(
            run.metadata_json["failure_summary"],
            {
                "sources": {"Vogue": "RuntimeError: source boom"},
                "parse": {"article-1": "RuntimeError: parse boom"},
                "event_frame": {"article-2": "RuntimeError: frame boom"},
                "strict_story": None,
                "digest": None,
            },
        )

    def test_digest_task_rolls_back_output_after_losing_ownership(self) -> None:
        session_factory = self._build_file_session_factory("digest-ownership-loss.db")
        business_day = datetime(2026, 3, 27, 0, 0, tzinfo=UTC).date()
        self._insert_pipeline_run(
            session_factory,
            run_id="run-digest-ownership-loss",
            business_date_value=business_day,
            strict_story_status="done",
            digest_status="queued",
            digest_token=1,
        )

        async def fake_generate(self, session, business_day, *, run_id):  # type: ignore[no-untyped-def]
            del self
            session.add(
                Digest(
                    digest_key="digest-old-owner",
                    business_date=business_day,
                    facet="market",
                    title_zh="old owner digest",
                    dek_zh="dek",
                    body_markdown="body",
                    source_article_count=0,
                    source_names_json=[],
                    created_run_id=run_id,
                    generation_status="done",
                    generation_error=None,
                )
            )
            with session_factory() as competing_session:
                competing_run = competing_session.get(PipelineRun, run_id)
                competing_run.digest_status = "queued"
                competing_run.digest_token = 2
                competing_run.digest_updated_at = datetime(2026, 3, 27, 9, 2, tzinfo=UTC).replace(
                    tzinfo=None
                )
                competing_session.commit()
            return []

        with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
            with patch("backend.app.tasks.aggregation_tasks.ensure_article_storage_schema", return_value=None):
                with patch(
                    "backend.app.tasks.aggregation_tasks.DigestGenerationService.generate_for_day",
                    new=fake_generate,
                ):
                    with self.assertRaises(RuntimeError):
                        generate_digests_for_day("2026-03-27", "run-digest-ownership-loss", 1)

        with session_factory() as session:
            run = session.get(PipelineRun, "run-digest-ownership-loss")
            digests = session.query(Digest).all()

        self.assertIsNotNone(run)
        self.assertEqual(run.digest_status, "queued")
        self.assertEqual(run.digest_token, 2)
        self.assertEqual(digests, [])

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
        parse_status: str | None = None,
    ) -> str:
        now = datetime(2026, 3, 27, 8, 0, tzinfo=UTC).replace(tzinfo=None)
        resolved_parse_status = parse_status or ("done" if markdown_rel_path else "pending")
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
                    parse_status=resolved_parse_status,
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

    def _build_file_session_factory(self, filename: str):
        database_path = Path(self.temp_dir.name) / filename
        engine = create_engine(f"sqlite:///{database_path}")
        Base.metadata.create_all(engine)
        ensure_article_storage_schema(engine)
        return sessionmaker(bind=engine)

    @staticmethod
    def _insert_pipeline_run(
        session_factory,
        *,
        run_id: str,
        business_date_value,
        strict_story_status: str = "pending",
        strict_story_token: int = 0,
        digest_status: str = "pending",
        digest_token: int = 0,
        metadata_json: dict | None = None,
    ) -> None:
        observed_at = datetime(2026, 3, 27, 8, 0, tzinfo=UTC).replace(tzinfo=None)
        with session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=business_date_value,
                    run_type="digest_daily",
                    status="running",
                    strict_story_status=strict_story_status,
                    strict_story_attempts=0,
                    strict_story_error=None,
                    strict_story_updated_at=observed_at,
                    strict_story_token=strict_story_token,
                    digest_status=digest_status,
                    digest_attempts=0,
                    digest_error=None,
                    digest_updated_at=observed_at,
                    digest_token=digest_token,
                    started_at=observed_at,
                    metadata_json={} if metadata_json is None else metadata_json,
                )
            )
            session.commit()


if __name__ == "__main__":
    unittest.main()
