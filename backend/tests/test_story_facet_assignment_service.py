from __future__ import annotations

import asyncio
import unittest
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Article, PipelineRun, Story, StoryArticle, StoryFacet, ensure_article_storage_schema
from backend.app.service.story_facet_assignment_service import StoryFacetAssignmentService


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
    def test_assign_for_day_persists_story_facet_rows(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        service = StoryFacetAssignmentService(
            client=_build_fake_llm_client(
                (
                    '{"stories":['
                    '{"story_key":"story-1","facets":["runway_series","trend_summary"]},'
                    '{"story_key":"story-2","facets":[]}'
                    "]}"
                )
            ),
            rate_limiter=_build_fake_rate_limiter(),
        )

        persisted = asyncio.run(service.assign_for_day(session, date(2026, 3, 30)))

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

    def test_assign_for_day_raises_on_unsupported_runtime_facet(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        service = StoryFacetAssignmentService(
            client=_build_fake_llm_client(
                (
                    '{"stories":['
                    '{"story_key":"story-1","facets":["trend_watch"]},'
                    '{"story_key":"story-2","facets":[]}'
                    "]}"
                )
            ),
            rate_limiter=_build_fake_rate_limiter(),
        )

        with self.assertRaisesRegex(ValueError, "unsupported runtime facet"):
            asyncio.run(service.assign_for_day(session, date(2026, 3, 30)))
