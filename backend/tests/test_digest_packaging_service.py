from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

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
from backend.app.service.digest_packaging_service import DigestPackagingService


def _build_fake_llm_client(
    raw_contents: list[str],
    *,
    call_log: list[dict[str, object]],
) -> SimpleNamespace:
    queued = list(raw_contents)

    async def create(**kwargs: object) -> SimpleNamespace:
        call_log.append(dict(kwargs))
        if not queued:
            raise AssertionError("fake llm client exhausted queued responses")
        raw_content = queued.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw_content))]
        )

    completions = SimpleNamespace(create=create)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


def _build_fake_rate_limiter() -> SimpleNamespace:
    return SimpleNamespace(lease=lambda *_: nullcontext())


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
    def test_build_plans_for_day_groups_by_facet_and_allows_story_overlap(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        call_log: list[dict[str, object]] = []
        service = DigestPackagingService(
            client=_build_fake_llm_client(
                [
                    (
                        '{"digests":['
                        '{"facet":"runway_series","story_keys":["story-1"],'
                        '"article_ids":["article-1","article-2"],'
                        '"editorial_angle":"秀场造型作为独立看点",'
                        '"title_zh":"Acme 秀场速览","dek_zh":"聚焦造型变化"}'
                        "]}"
                    ),
                    (
                        '{"digests":['
                        '{"facet":"trend_summary","story_keys":["story-1","story-2"],'
                        '"article_ids":["article-2","article-3"],'
                        '"editorial_angle":"设计语言与组织动作共同指向新趋势",'
                        '"title_zh":"本日趋势联动","dek_zh":"从秀场延展到品牌动作"}'
                        "]}"
                    ),
                ],
                call_log=call_log,
            ),
            rate_limiter=_build_fake_rate_limiter(),
        )

        plans = asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(2, len(plans))
        self.assertEqual(date(2026, 3, 30), plans[0].business_date)
        self.assertEqual(date(2026, 3, 30), plans[1].business_date)
        self.assertEqual(("story-1",), plans[0].story_keys)
        self.assertEqual(("story-1", "story-2"), plans[1].story_keys)
        self.assertEqual(2, len(call_log))
        story_keys_by_call = []
        for call in call_log:
            messages = call["messages"]
            user_message = messages[1]["content"]
            payload = json.loads(user_message)
            story_keys_by_call.append(tuple(story["story_key"] for story in payload["stories"]))
        self.assertEqual(
            [("story-1",), ("story-1", "story-2")],
            story_keys_by_call,
        )
        for call in call_log:
            self.assertNotIn("response_format", call)

    def test_build_plans_for_day_returns_empty_when_no_faceted_stories(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        session.query(StoryFacet).delete()
        session.commit()
        call_log: list[dict[str, object]] = []
        service = DigestPackagingService(
            client=_build_fake_llm_client(
                ['{"digests":[]}'],
                call_log=call_log,
            ),
            rate_limiter=_build_fake_rate_limiter(),
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
            client=_build_fake_llm_client(
                ['{"digests":[]}'],
                call_log=call_log,
            ),
            rate_limiter=_build_fake_rate_limiter(),
        )

        with self.assertRaisesRegex(ValueError, "unsupported runtime facet"):
            asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))
        self.assertEqual([], call_log)
