from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    Article,
    PipelineRun,
    Story,
    StoryArticle,
    StoryFacet,
    ensure_article_storage_schema,
)
from backend.app.prompts.digest_packaging_prompt import build_digest_packaging_prompt
from backend.app.schemas.llm.digest_packaging import DigestPackagingSchema
from backend.app.service.digest_packaging_service import DigestPackagingService


class _FakeAgent:
    def __init__(
        self,
        responses: list[DigestPackagingSchema | dict[str, object]],
        *,
        call_log: list[dict[str, object]],
    ) -> None:
        self._responses = list(responses)
        self._call_log = call_log
        self.invoke_calls = 0

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.invoke_calls += 1
        self._call_log.append(payload)
        if not self._responses:
            raise AssertionError("fake agent exhausted queued responses")
        return {"structured_response": self._responses.pop(0)}


class _FakeChatModel:
    def __init__(self, structured_model: _FakeAgent) -> None:
        self._structured_model = structured_model
        self.structured_output_calls: list[tuple[type[object], str]] = []

    def with_structured_output(self, schema: type[object], *, method: str):
        self.structured_output_calls.append((schema, method))
        return self._structured_model


class _FakeRateLimiter:
    def __init__(self) -> None:
        self.leased_buckets: list[str] = []

    def lease(self, bucket: str):
        self.leased_buckets.append(bucket)
        return nullcontext()


def _build_fake_agent(
    responses: list[DigestPackagingSchema | dict[str, object]],
    *,
    call_log: list[dict[str, object]],
) -> _FakeAgent:
    return _FakeAgent(responses, call_log=call_log)


def _build_session() -> Session:
    business_day = date(2026, 3, 30)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    session.add(PipelineRun(run_id="run-1", business_date=business_day))
    session.add_all(
        [
            Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-1",
                original_url="https://example.com/article-1",
                title_raw="Article 1",
                summary_raw="Summary 1",
                markdown_rel_path="2026/03/30/article-1.md",
            ),
            Article(
                article_id="article-2",
                source_name="WWD",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-2",
                original_url="https://example.com/article-2",
                title_raw="Article 2",
                summary_raw="Summary 2",
                markdown_rel_path="2026/03/30/article-2.md",
            ),
            Article(
                article_id="article-3",
                source_name="BoF",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-3",
                original_url="https://example.com/article-3",
                title_raw="Article 3",
                summary_raw="Summary 3",
                markdown_rel_path="2026/03/30/article-3.md",
            ),
            Story(
                story_key="story-1",
                business_date=business_day,
                event_type="runway_show",
                synopsis_zh="Acme 巴黎秀场",
                anchor_json={"brand": "Acme"},
                article_membership_json=["article-1", "article-2"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            Story(
                story_key="story-2",
                business_date=business_day,
                event_type="market_update",
                synopsis_zh="Beta 设计总监变动",
                anchor_json={"brand": "Beta"},
                article_membership_json=["article-3"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            StoryArticle(story_key="story-1", article_id="article-1", rank=0),
            StoryArticle(story_key="story-1", article_id="article-2", rank=1),
            StoryArticle(story_key="story-2", article_id="article-3", rank=0),
            StoryFacet(story_key="story-1", facet="runway_series"),
            StoryFacet(story_key="story-1", facet="trend_summary"),
            StoryFacet(story_key="story-2", facet="trend_summary"),
        ]
    )
    session.commit()
    return session


class DigestPackagingServiceTest(unittest.TestCase):
    def test_build_plans_for_day_builds_structured_model_with_required_arguments(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        limiter = _FakeRateLimiter()
        call_log: list[dict[str, object]] = []
        fake_agent = _build_fake_agent(
            [DigestPackagingSchema(), DigestPackagingSchema()],
            call_log=call_log,
        )
        fake_model = _FakeChatModel(fake_agent)
        service = DigestPackagingService(rate_limiter=limiter)

        with patch(
            "backend.app.service.digest_packaging_service.build_story_model",
            return_value=fake_model,
        ) as build_story_model_mock:
            payload = asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual([], payload)
        self.assertEqual(["digest_packaging", "digest_packaging"], limiter.leased_buckets)
        build_story_model_mock.assert_called_once_with(service._configuration)
        self.assertEqual([(DigestPackagingSchema, "json_schema")], fake_model.structured_output_calls)

    def test_build_plans_for_day_derives_article_ids_and_source_names_locally(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        call_log: list[dict[str, object]] = []
        limiter = _FakeRateLimiter()
        service = DigestPackagingService(
            agent=_build_fake_agent(
                [
                    DigestPackagingSchema.model_validate(
                        {
                            "digests": [
                                {
                                    "story_keys": ["story-1"],
                                    "editorial_angle": "秀场造型作为独立看点",
                                }
                            ]
                        }
                    ),
                    DigestPackagingSchema.model_validate(
                        {
                            "digests": [
                                {
                                    "story_keys": ["story-1", "story-2"],
                                    "editorial_angle": "设计语言与组织动作共同指向新趋势",
                                }
                            ]
                        }
                    ),
                ],
                call_log=call_log,
            ),
            rate_limiter=limiter,
        )

        plans = asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(2, len(plans))
        self.assertEqual(["digest_packaging", "digest_packaging"], limiter.leased_buckets)
        self.assertEqual(date(2026, 3, 30), plans[0].business_date)
        self.assertEqual(date(2026, 3, 30), plans[1].business_date)
        self.assertEqual(("story-1",), plans[0].story_keys)
        self.assertEqual(("story-1", "story-2"), plans[1].story_keys)
        self.assertEqual(("article-1", "article-2"), plans[0].article_ids)
        self.assertEqual(("article-1", "article-2", "article-3"), plans[1].article_ids)
        self.assertEqual(("Vogue", "WWD"), plans[0].source_names)
        self.assertEqual(("BoF", "Vogue", "WWD"), plans[1].source_names)
        self.assertEqual(2, len(call_log))
        story_keys_by_call = []
        for call in call_log:
            payload = json.loads(call["messages"][0]["content"])
            story_keys_by_call.append(tuple(story["story_key"] for story in payload["stories"]))
        self.assertEqual(
            [("story-1",), ("story-1", "story-2")],
            story_keys_by_call,
        )

    def test_build_plans_for_day_fails_when_selected_story_group_resolves_zero_articles(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        session.query(StoryArticle).delete()
        session.commit()
        service = DigestPackagingService(
            agent=_build_fake_agent(
                [
                    DigestPackagingSchema.model_validate(
                        {
                            "digests": [
                                {
                                    "story_keys": ["story-1"],
                                    "editorial_angle": "没有文章的非法组合",
                                }
                            ]
                        }
                    )
                ],
                call_log=[],
            ),
            rate_limiter=_FakeRateLimiter(),
        )

        with self.assertRaisesRegex(ValueError, "resolved zero article_ids"):
            asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))

    def test_build_plans_for_day_returns_empty_when_no_faceted_stories(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        session.query(StoryFacet).delete()
        session.commit()
        call_log: list[dict[str, object]] = []
        service = DigestPackagingService(
            agent=_build_fake_agent(
                [DigestPackagingSchema()],
                call_log=call_log,
            ),
            rate_limiter=_FakeRateLimiter(),
        )

        plans = asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))
        self.assertEqual([], plans)
        self.assertEqual([], call_log)

    def test_build_plans_for_day_raises_on_unsupported_runtime_facet(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        session.query(StoryFacet).delete()
        session.add(StoryFacet(story_key="story-1", facet="trend_watch"))
        session.commit()

        call_log: list[dict[str, object]] = []
        service = DigestPackagingService(
            agent=_build_fake_agent(
                [DigestPackagingSchema()],
                call_log=call_log,
            ),
            rate_limiter=_FakeRateLimiter(),
        )

        with self.assertRaisesRegex(ValueError, "unsupported runtime facet"):
            asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))
        self.assertEqual([], call_log)

    def test_build_plans_for_day_records_llm_debug_artifacts_with_system_prompt(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"KARL_LLM_DEBUG_ARTIFACT_DIR": tmpdir}):
                service = DigestPackagingService(
                    agent=_build_fake_agent(
                        [DigestPackagingSchema(), DigestPackagingSchema()],
                        call_log=[],
                    ),
                    rate_limiter=_FakeRateLimiter(),
                )
                plans = asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))

            self.assertEqual([], plans)
            prompt_path = Path(tmpdir) / "run-1" / "digest_packaging" / "facet-runway_series" / "prompt.json"
            response_path = Path(tmpdir) / "run-1" / "digest_packaging" / "facet-runway_series" / "response.json"
            self.assertTrue(prompt_path.exists())
            self.assertTrue(response_path.exists())
            prompt_payload = json.loads(prompt_path.read_text(encoding="utf-8"))
            self.assertEqual(build_digest_packaging_prompt(), prompt_payload["system_prompt"])
            invoke_payload = prompt_payload["invoke_payload"]
            user_payload = json.loads(invoke_payload["messages"][0]["content"])
            self.assertEqual("runway_series", user_payload["facet"])
