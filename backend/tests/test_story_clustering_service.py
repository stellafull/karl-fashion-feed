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

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models.article import Article, ensure_article_storage_schema
from backend.app.models.event_frame import ArticleEventFrame
from backend.app.models.runtime import PipelineRun
from backend.app.models.story import Story, StoryArticle, StoryFrame
from backend.app.prompts.story_cluster_judgment_prompt import build_story_cluster_judgment_prompt
from backend.app.schemas.llm.story_cluster_judgment import (
    StoryClusterGroup,
    StoryClusterJudgmentSchema,
)
from backend.app.service.story_clustering_service import StoryClusteringService


def build_frame(
    frame_id: str,
    article_id: str,
    *,
    event_type: str,
    brand: str,
    person: str = "",
) -> ArticleEventFrame:
    return ArticleEventFrame(
        event_frame_id=frame_id,
        article_id=article_id,
        business_date=date(2026, 3, 29),
        event_type=event_type,
        subject_json={"brand": brand, "person": person},
        action_text="发布新内容",
        object_text="",
        place_text="Paris",
        collection_text="FW26",
        season_text="FW26",
        show_context_text="",
        evidence_json=[{"quote": "Acme in Paris"}],
        signature_json={"brand": brand},
        extraction_confidence=0.9,
        extraction_status="done",
        extraction_error=None,
    )


def build_story_test_session_with_frames(
    *,
    business_day: date,
    frames: list[ArticleEventFrame],
) -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    article_ids = {frame.article_id for frame in frames}
    session.add(
        PipelineRun(
            run_id="run-1",
            business_date=business_day,
        )
    )
    session.add_all(
        [
            Article(
                article_id=article_id,
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url=f"https://example.com/{article_id}",
                original_url=f"https://example.com/{article_id}",
                title_raw=f"Article {article_id}",
                summary_raw="",
                markdown_rel_path=f"2026/03/29/{article_id}.md",
            )
            for article_id in sorted(article_ids)
        ]
    )
    session.add_all(frames)
    session.commit()
    return session


class _FakeAgent:
    def __init__(
        self,
        responses: list[StoryClusterJudgmentSchema | dict[str, object]],
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


class StoryClusteringServiceTest(unittest.TestCase):
    def test_run_story_cluster_judgment_builds_agent_via_create_agent_with_required_arguments(self) -> None:
        service = StoryClusteringService(rate_limiter=_FakeRateLimiter())
        fake_model = object()
        fake_agent = _FakeAgent([StoryClusterJudgmentSchema()])
        window = (
            build_frame("f1", "a1", event_type="runway_show", brand="Acme"),
        )
        frame_cards = tuple(
            service._build_frame_card(  # noqa: SLF001
                frame=frame,
                article=Article(
                    article_id=frame.article_id,
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url=f"https://example.com/{frame.article_id}",
                    original_url=f"https://example.com/{frame.article_id}",
                    title_raw=f"Article {frame.article_id}",
                    summary_raw="",
                    markdown_rel_path=f"2026/03/29/{frame.article_id}.md",
                ),
            )
            for frame in window
        )

        with patch(
            "backend.app.service.story_clustering_service.build_story_model",
            return_value=fake_model,
        ) as build_story_model_mock:
            with patch(
                "backend.app.service.story_clustering_service.create_agent",
                return_value=fake_agent,
            ) as create_agent_mock:
                payload = asyncio.run(
                    service._run_story_cluster_judgment(frame_cards, run_id="run-1")  # noqa: SLF001
                )

        self.assertIsInstance(payload, StoryClusterJudgmentSchema)
        self.assertEqual(1, fake_agent.invoke_calls)
        build_story_model_mock.assert_called_once_with(service._configuration)
        create_agent_mock.assert_called_once_with(
            model=fake_model,
            tools=[],
            system_prompt=build_story_cluster_judgment_prompt(),
            response_format=StoryClusterJudgmentSchema,
        )

    def test_cluster_business_day_merges_same_event_even_when_event_types_differ(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[
                build_frame("f1", "a1", event_type="runway_show", brand="Acme", person="Jane"),
                build_frame("f2", "a2", event_type="campaign_launch", brand="Acme", person="Jane"),
            ],
        )
        self.addCleanup(session.close)
        fake_agent = _FakeAgent(
            [
                StoryClusterJudgmentSchema(
                    groups=[
                        StoryClusterGroup(
                            seed_event_frame_id="f1",
                            member_event_frame_ids=["f1", "f2"],
                            synopsis_zh="Acme 巴黎秀场同一主事件",
                            event_type="runway_show",
                            anchor_json={"brand": "Acme", "person": "Jane"},
                        )
                    ]
                )
            ]
        )
        limiter = _FakeRateLimiter()

        stories = asyncio.run(
            StoryClusteringService(
                agent=fake_agent,
                rate_limiter=limiter,
            ).cluster_business_day(
                session,
                business_day=date(2026, 3, 29),
                run_id="run-1",
            )
        )

        self.assertEqual(len(stories), 1)
        self.assertEqual(["story_cluster_judgment"], limiter.leased_buckets)
        self.assertEqual(stories[0].event_type, "runway_show")
        self.assertEqual(stories[0].synopsis_zh, "Acme 巴黎秀场同一主事件")
        self.assertEqual(stories[0].anchor_json, {"brand": "Acme", "person": "Jane"})
        self.assertEqual(stories[0].article_membership_json, ["a1", "a2"])

        persisted_story = session.scalars(select(Story)).one()
        self.assertEqual(persisted_story.article_membership_json, ["a1", "a2"])

        persisted_frames = list(
            session.execute(
                select(StoryFrame.event_frame_id).order_by(StoryFrame.rank.asc(), StoryFrame.event_frame_id.asc())
            ).scalars()
        )
        self.assertEqual(persisted_frames, ["f1", "f2"])

        persisted_articles = list(
            session.execute(
                select(StoryArticle.article_id).order_by(StoryArticle.rank.asc(), StoryArticle.article_id.asc())
            ).scalars()
        )
        self.assertEqual(persisted_articles, ["a1", "a2"])

    def test_cluster_business_day_backfills_singleton_story_when_llm_returns_zero_groups(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[build_frame("f1", "a1", event_type="runway_show", brand="Acme")],
        )
        self.addCleanup(session.close)
        fake_agent = _FakeAgent([StoryClusterJudgmentSchema()])

        stories = asyncio.run(
            StoryClusteringService(
                agent=fake_agent,
                rate_limiter=_FakeRateLimiter(),
            ).cluster_business_day(
                session,
                business_day=date(2026, 3, 29),
                run_id="run-1",
            )
        )

        self.assertEqual(1, len(stories))
        self.assertEqual("runway_show", stories[0].event_type)
        self.assertEqual(["a1"], stories[0].article_membership_json)
        self.assertTrue(stories[0].synopsis_zh)

        persisted_frames = list(
            session.execute(
                select(StoryFrame.event_frame_id).order_by(StoryFrame.rank.asc(), StoryFrame.event_frame_id.asc())
            ).scalars()
        )
        self.assertEqual(["f1"], persisted_frames)

    def test_cluster_business_day_backfills_singleton_when_llm_leaves_frames_unassigned(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[
                build_frame("f1", "a1", event_type="runway_show", brand="Acme", person="Jane"),
                build_frame("f2", "a2", event_type="campaign_launch", brand="Acme", person="Jane"),
            ],
        )
        self.addCleanup(session.close)
        fake_agent = _FakeAgent(
            [
                StoryClusterJudgmentSchema(
                    groups=[
                        StoryClusterGroup(
                            seed_event_frame_id="f1",
                            member_event_frame_ids=["f1"],
                            synopsis_zh="Only one frame covered",
                            event_type="runway_show",
                            anchor_json={"brand": "Acme"},
                        )
                    ]
                )
            ]
        )

        stories = asyncio.run(
            StoryClusteringService(
                agent=fake_agent,
                rate_limiter=_FakeRateLimiter(),
            ).cluster_business_day(
                session,
                business_day=date(2026, 3, 29),
                run_id="run-1",
            )
        )

        self.assertEqual(2, len(stories))
        persisted_story_frames = list(
            session.execute(
                select(Story.story_key, StoryFrame.event_frame_id)
                .join(StoryFrame, StoryFrame.story_key == Story.story_key)
                .order_by(Story.story_key.asc(), StoryFrame.rank.asc(), StoryFrame.event_frame_id.asc())
            ).all()
        )
        frames_by_story: dict[str, list[str]] = {}
        for story_key, event_frame_id in persisted_story_frames:
            frames_by_story.setdefault(story_key, []).append(event_frame_id)
        self.assertEqual([["f1"], ["f2"]], sorted(frames_by_story.values()))

        persisted_synopses = {
            story.synopsis_zh
            for story in session.scalars(select(Story).order_by(Story.story_key.asc())).all()
        }
        self.assertIn("Only one frame covered", persisted_synopses)

    def test_cluster_business_day_continues_when_one_window_judgment_raises(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[
                build_frame("f1", "a1", event_type="runway_show", brand="Acme", person="Jane"),
                build_frame("f2", "a2", event_type="campaign_launch", brand="Acme", person="Jane"),
            ],
        )
        self.addCleanup(session.close)

        class _AlwaysFailingWindowService(StoryClusteringService):
            async def _run_story_cluster_judgment(self, window: tuple[object, ...], *, run_id: str):
                _ = (window, run_id)
                raise ValueError("window judgment failed")

        stories = asyncio.run(
            _AlwaysFailingWindowService(
                rate_limiter=_FakeRateLimiter(),
            ).cluster_business_day(
                session,
                business_day=date(2026, 3, 29),
                run_id="run-1",
            )
        )

        self.assertEqual(2, len(stories))
        persisted_story_frames = list(
            session.execute(
                select(Story.story_key, StoryFrame.event_frame_id)
                .join(StoryFrame, StoryFrame.story_key == Story.story_key)
                .order_by(Story.story_key.asc(), StoryFrame.rank.asc(), StoryFrame.event_frame_id.asc())
            ).all()
        )
        frames_by_story: dict[str, list[str]] = {}
        for story_key, event_frame_id in persisted_story_frames:
            frames_by_story.setdefault(story_key, []).append(event_frame_id)
        self.assertEqual([["f1"], ["f2"]], sorted(frames_by_story.values()))

    def test_cluster_business_day_sends_candidate_frames_in_user_message(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[
                build_frame("f1", "a1", event_type="runway_show", brand="Acme", person="Jane"),
                build_frame("f2", "a2", event_type="campaign_launch", brand="Acme", person="Jane"),
            ],
        )
        self.addCleanup(session.close)
        call_log: list[dict[str, object]] = []
        fake_agent = _FakeAgent(
            [
                StoryClusterJudgmentSchema(
                    groups=[
                        StoryClusterGroup(
                            seed_event_frame_id="f1",
                            member_event_frame_ids=["f1", "f2"],
                            synopsis_zh="Acme 巴黎秀场同一主事件",
                            event_type="runway_show",
                            anchor_json={"brand": "Acme", "person": "Jane"},
                        )
                    ]
                )
            ],
            call_log=call_log,
        )

        asyncio.run(
            StoryClusteringService(
                agent=fake_agent,
                rate_limiter=_FakeRateLimiter(),
            ).cluster_business_day(
                session,
                business_day=date(2026, 3, 29),
                run_id="run-1",
            )
        )

        self.assertEqual(1, len(call_log))
        payload = json.loads(call_log[0]["messages"][0]["content"])
        self.assertEqual(["f1", "f2"], [item["event_frame_id"] for item in payload["candidate_frames"]])

    def test_cluster_business_day_records_llm_debug_artifacts_with_system_prompt(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[build_frame("f1", "a1", event_type="runway_show", brand="Acme", person="Jane")],
        )
        self.addCleanup(session.close)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"KARL_LLM_DEBUG_ARTIFACT_DIR": tmpdir}):
                service = StoryClusteringService(
                    agent=_FakeAgent(
                        [
                            StoryClusterJudgmentSchema(
                                groups=[
                                    StoryClusterGroup(
                                        seed_event_frame_id="f1",
                                        member_event_frame_ids=["f1"],
                                        synopsis_zh="Acme 单篇事件",
                                        event_type="runway_show",
                                        anchor_json={"brand": "Acme"},
                                    )
                                ]
                            )
                        ]
                    ),
                    rate_limiter=_FakeRateLimiter(),
                )
                stories = asyncio.run(
                    service.cluster_business_day(
                        session,
                        business_day=date(2026, 3, 29),
                        run_id="run-1",
                    )
                )

            self.assertEqual(1, len(stories))
            prompt_path = Path(tmpdir) / "run-1" / "story_cluster_judgment" / "window-f1" / "prompt.json"
            response_path = Path(tmpdir) / "run-1" / "story_cluster_judgment" / "window-f1" / "response.json"
            self.assertTrue(prompt_path.exists())
            self.assertTrue(response_path.exists())
            prompt_payload = json.loads(prompt_path.read_text(encoding="utf-8"))
            self.assertEqual(build_story_cluster_judgment_prompt(), prompt_payload["system_prompt"])
            invoke_payload = prompt_payload["invoke_payload"]
            user_payload = json.loads(invoke_payload["messages"][0]["content"])
            self.assertEqual(["f1"], [item["event_frame_id"] for item in user_payload["candidate_frames"]])
