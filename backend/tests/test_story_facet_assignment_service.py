from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Article, PipelineRun, Story, StoryArticle, StoryFacet, ensure_article_storage_schema
from backend.app.prompts.facet_assignment_prompt import build_facet_assignment_prompt
from backend.app.schemas.llm.facet_assignment import FacetAssignmentSchema
from backend.app.service.runtime_facets import RUNTIME_FACETS
from backend.app.service.story_facet_assignment_service import StoryFacetAssignmentService


class _FakeAgent:
    def __init__(
        self,
        responses: list[FacetAssignmentSchema | dict[str, object]],
        *,
        call_log: list[dict[str, object]] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._call_log = call_log if call_log is not None else []
        self.invoke_calls = 0

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.invoke_calls += 1
        self._call_log.append(payload)
        if not self._responses:
            raise AssertionError("fake agent exhausted queued responses")
        return {"structured_response": self._responses.pop(0)}


class _FakeRateLimiter:
    def __init__(self) -> None:
        self.leased_buckets: list[str] = []

    def lease(self, bucket: str):
        self.leased_buckets.append(bucket)
        return nullcontext()


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
            Story(
                story_key="story-1",
                business_date=business_day,
                event_type="runway_show",
                synopsis_zh="Acme 巴黎秀场",
                anchor_json={"brand": "Acme"},
                article_membership_json=["article-1"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            Story(
                story_key="story-2",
                business_date=business_day,
                event_type="campaign_launch",
                synopsis_zh="Beta 发布新广告大片",
                anchor_json={"brand": "Beta"},
                article_membership_json=["article-2"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            StoryArticle(story_key="story-1", article_id="article-1", rank=0),
            StoryArticle(story_key="story-2", article_id="article-2", rank=0),
        ]
    )
    session.commit()
    return session


class StoryFacetAssignmentServiceTest(unittest.TestCase):
    def test_assign_for_day_builds_agent_via_create_agent_with_required_arguments(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        limiter = _FakeRateLimiter()
        fake_model = object()
        fake_agent = _FakeAgent([FacetAssignmentSchema()])
        service = StoryFacetAssignmentService(rate_limiter=limiter)

        with patch(
            "backend.app.service.story_facet_assignment_service.build_story_model",
            return_value=fake_model,
        ) as build_story_model_mock:
            with patch(
                "backend.app.service.story_facet_assignment_service.create_agent",
                return_value=fake_agent,
            ) as create_agent_mock:
                payload = asyncio.run(service.assign_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual([], payload)
        self.assertEqual(["facet_assignment"], limiter.leased_buckets)
        build_story_model_mock.assert_called_once_with(service._configuration)
        create_agent_mock.assert_called_once_with(
            model=fake_model,
            tools=[],
            system_prompt=build_facet_assignment_prompt(),
            response_format=FacetAssignmentSchema,
        )

    def test_prompt_lists_supported_runtime_facets(self) -> None:
        prompt = build_facet_assignment_prompt()

        self.assertIn("只能使用以下 facet", prompt)
        for facet in sorted(RUNTIME_FACETS):
            self.assertIn(facet, prompt)

    def test_assign_for_day_persists_story_facet_rows(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        limiter = _FakeRateLimiter()
        service = StoryFacetAssignmentService(
            agent=_FakeAgent(
                [
                    FacetAssignmentSchema.model_validate(
                        {
                            "stories": [
                                {"story_key": "story-1", "facets": ["runway_series", "trend_summary"]},
                                {"story_key": "story-2", "facets": []},
                            ]
                        }
                    )
                ]
            ),
            rate_limiter=limiter,
        )

        persisted = asyncio.run(service.assign_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(["facet_assignment"], limiter.leased_buckets)
        self.assertEqual(
            [("story-1", "runway_series"), ("story-1", "trend_summary")],
            [(row.story_key, row.facet) for row in persisted],
        )
        stored = session.execute(
            select(StoryFacet.story_key, StoryFacet.facet).order_by(StoryFacet.story_key.asc(), StoryFacet.facet.asc())
        ).all()
        self.assertEqual(
            [("story-1", "runway_series"), ("story-1", "trend_summary")],
            stored,
        )

    def test_assign_for_day_sends_story_batch_in_user_message(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        call_log: list[dict[str, object]] = []
        service = StoryFacetAssignmentService(
            agent=_FakeAgent(
                [
                    FacetAssignmentSchema.model_validate(
                        {
                            "stories": [
                                {"story_key": "story-1", "facets": ["runway_series"]},
                                {"story_key": "story-2", "facets": []},
                            ]
                        }
                    )
                ],
                call_log=call_log,
            ),
            rate_limiter=_FakeRateLimiter(),
        )

        asyncio.run(service.assign_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(1, len(call_log))
        payload = json.loads(call_log[0]["messages"][0]["content"])
        self.assertEqual(["story-1", "story-2"], [story["story_key"] for story in payload["stories"]])

    def test_assign_for_day_batches_large_story_sets_and_merges_rows(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        call_log: list[dict[str, object]] = []
        service = StoryFacetAssignmentService(
            agent=_FakeAgent(
                [
                    FacetAssignmentSchema.model_validate(
                        {"stories": [{"story_key": "story-1", "facets": ["runway_series"]}]}
                    ),
                    FacetAssignmentSchema.model_validate(
                        {"stories": [{"story_key": "story-2", "facets": ["brand_market"]}]}
                    ),
                ],
                call_log=call_log,
            ),
            rate_limiter=_FakeRateLimiter(),
            max_stories_per_request=1,
        )

        persisted = asyncio.run(service.assign_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(
            [("story-1", "runway_series"), ("story-2", "brand_market")],
            [(row.story_key, row.facet) for row in persisted],
        )
        self.assertEqual(2, len(call_log))

        batch_story_keys = []
        for call in call_log:
            payload = json.loads(call["messages"][0]["content"])
            batch_story_keys.append(tuple(story["story_key"] for story in payload["stories"]))
        self.assertEqual([("story-1",), ("story-2",)], batch_story_keys)

    def test_assign_for_day_raises_on_unsupported_runtime_facet(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        service = StoryFacetAssignmentService(
            agent=_FakeAgent(
                [
                    FacetAssignmentSchema.model_validate(
                        {
                            "stories": [
                                {"story_key": "story-1", "facets": ["trend_watch"]},
                                {"story_key": "story-2", "facets": []},
                            ]
                        }
                    )
                ]
            ),
            rate_limiter=_FakeRateLimiter(),
        )

        with self.assertRaisesRegex(ValueError, "unsupported runtime facet"):
            asyncio.run(service.assign_for_day(session, date(2026, 3, 30), run_id="run-1"))

    def test_assign_for_day_records_llm_debug_artifacts_from_env(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"KARL_LLM_DEBUG_ARTIFACT_DIR": tmpdir}):
                service = StoryFacetAssignmentService(
                    agent=_FakeAgent(
                        [
                            FacetAssignmentSchema.model_validate(
                                {
                                    "stories": [
                                        {"story_key": "story-1", "facets": ["runway_series"]},
                                        {"story_key": "story-2", "facets": ["trend_summary"]},
                                    ]
                                }
                            )
                        ]
                    ),
                    rate_limiter=_FakeRateLimiter(),
                )
                persisted = asyncio.run(service.assign_for_day(session, date(2026, 3, 30), run_id="run-1"))

            self.assertEqual(2, len(persisted))
            prompt_path = (
                Path(tmpdir) / "run-1" / "facet_assignment" / "business-day-2026-03-30" / "prompt.json"
            )
            response_path = (
                Path(tmpdir) / "run-1" / "facet_assignment" / "business-day-2026-03-30" / "response.json"
            )
            self.assertTrue(prompt_path.exists())
            self.assertTrue(response_path.exists())
