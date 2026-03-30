from __future__ import annotations

import asyncio
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


def _build_fake_llm_client(raw_content: str) -> SimpleNamespace:
    async def create(**_: object) -> SimpleNamespace:
        return response

    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw_content))])
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
            StoryFacet(story_key="story-1", facet="trend_watch"),
            StoryFacet(story_key="story-2", facet="trend_watch"),
        ]
    )
    session.commit()
    return session


class DigestPackagingServiceTest(unittest.TestCase):
    def test_package_for_day_allows_story_overlap_across_digest_plans(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        service = DigestPackagingService(
            client=_build_fake_llm_client(
                (
                    '{"digests":['
                    '{"facet":"runway_series","story_keys":["story-1"],'
                    '"article_ids":["article-1","article-2"],'
                    '"editorial_angle":"秀场造型作为独立看点",'
                    '"title_zh":"Acme 秀场速览","dek_zh":"聚焦造型变化"},'
                    '{"facet":"trend_watch","story_keys":["story-1","story-2"],'
                    '"article_ids":["article-2","article-3"],'
                    '"editorial_angle":"设计语言与组织动作共同指向新趋势",'
                    '"title_zh":"本日趋势联动","dek_zh":"从秀场延展到品牌动作"}'
                    "]}"
                )
            ),
            rate_limiter=_build_fake_rate_limiter(),
        )

        plans = asyncio.run(service.package_for_day(session, date(2026, 3, 30)))

        self.assertEqual(2, len(plans))
        self.assertEqual(("story-1",), plans[0].story_keys)
        self.assertEqual(("story-1", "story-2"), plans[1].story_keys)

    def test_package_for_day_fails_when_non_empty_input_produces_zero_plans(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        service = DigestPackagingService(
            client=_build_fake_llm_client('{"digests":[]}'),
            rate_limiter=_build_fake_rate_limiter(),
        )

        with self.assertRaisesRegex(RuntimeError, "zero plans"):
            asyncio.run(service.package_for_day(session, date(2026, 3, 30)))
