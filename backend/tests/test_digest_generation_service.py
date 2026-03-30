from __future__ import annotations

import asyncio
import importlib
import unittest
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    Article,
    Digest,
    DigestArticle,
    DigestStory,
    PipelineRun,
    Story,
    StoryArticle,
    ensure_article_storage_schema,
)
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.digest_packaging_service import _ResolvedPlan
from backend.app.service.digest_report_writing_service import _WrittenDigest


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
                article_membership_json=["article-1", "article-2"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            StoryArticle(story_key="story-1", article_id="article-1", rank=0),
            StoryArticle(story_key="story-1", article_id="article-2", rank=1),
        ]
    )
    session.commit()
    return session


class _FakeFacetAssignmentService:
    def __init__(self) -> None:
        self.calls: list[tuple[Session, date]] = []

    async def assign_for_day(self, session: Session, business_day: date) -> list[object]:
        self.calls.append((session, business_day))
        return []


class _FakePackagingService:
    def __init__(self) -> None:
        self.calls: list[tuple[Session, date]] = []

    async def package_for_day(self, session: Session, business_day: date) -> list[_ResolvedPlan]:
        self.calls.append((session, business_day))
        return [
            _ResolvedPlan(
                facet="trend_watch",
                story_keys=("story-1",),
                article_ids=("article-2", "article-1"),
                editorial_angle="品牌动作解释趋势",
                title_zh="包装标题",
                dek_zh="包装导语",
                source_names=("Vogue", "WWD"),
            )
        ]


class _FakeReportWritingService:
    def __init__(self) -> None:
        self.calls: list[tuple[Session, date, str, tuple[_ResolvedPlan, ...]]] = []

    async def write_digests(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
        plans: list[_ResolvedPlan],
    ) -> list[_WrittenDigest]:
        self.calls.append((session, business_day, run_id, tuple(plans)))
        return [
            _WrittenDigest(
                digest=Digest(
                    business_date=business_day,
                    facet="trend_watch",
                    title_zh="写作标题",
                    dek_zh="写作导语",
                    body_markdown="# 正文",
                    source_article_count=2,
                    source_names_json=["Vogue", "WWD"],
                    created_run_id=run_id,
                    generation_status="done",
                    generation_error=None,
                ),
                story_keys=("story-1",),
                article_ids=("article-2", "article-1"),
            )
        ]


class DigestGenerationServiceTest(unittest.TestCase):
    def test_runtime_modules_are_importable_after_service_split(self) -> None:
        modules = (
            "backend.app.service.story_facet_assignment_service",
            "backend.app.service.digest_packaging_service",
            "backend.app.service.digest_report_writing_service",
            "backend.app.service.digest_generation_service",
        )

        for module_name in modules:
            with self.subTest(module_name=module_name):
                importlib.import_module(module_name)

    def test_generate_for_day_orchestrates_subservices_and_persists_memberships(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        facet_assignment_service = _FakeFacetAssignmentService()
        packaging_service = _FakePackagingService()
        report_writing_service = _FakeReportWritingService()
        service = DigestGenerationService(
            facet_assignment_service=facet_assignment_service,
            packaging_service=packaging_service,
            report_writing_service=report_writing_service,
        )

        persisted = asyncio.run(service.generate_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(1, len(facet_assignment_service.calls))
        self.assertEqual(1, len(packaging_service.calls))
        self.assertEqual(1, len(report_writing_service.calls))
        self.assertEqual(1, len(persisted))
        digest_rows = session.scalars(select(Digest).order_by(Digest.digest_key.asc())).all()
        self.assertEqual(1, len(digest_rows))
        self.assertEqual("写作标题", digest_rows[0].title_zh)
        digest_story_rows = session.execute(
            select(DigestStory.story_key, DigestStory.rank).order_by(DigestStory.rank.asc())
        ).all()
        self.assertEqual([("story-1", 0)], digest_story_rows)
        digest_article_rows = session.execute(
            select(DigestArticle.article_id, DigestArticle.rank).order_by(DigestArticle.rank.asc())
        ).all()
        self.assertEqual([("article-2", 0), ("article-1", 1)], digest_article_rows)
