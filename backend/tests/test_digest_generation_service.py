from __future__ import annotations

import asyncio
import importlib
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config.llm_config import Configuration
from backend.app.models import (
    Article,
    Digest,
    DigestArticle,
    DigestStory,
    PipelineRun,
    Story,
    StoryArticle,
    StoryFacet,
    ensure_article_storage_schema,
)
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.digest_packaging_service import ResolvedDigestPlan


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
            Story(
                story_key="story-2",
                business_date=business_day,
                event_type="market_update",
                synopsis_zh="Beta 品牌动作",
                anchor_json={"brand": "Beta"},
                article_membership_json=["article-2"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            StoryArticle(story_key="story-1", article_id="article-1", rank=0),
            StoryArticle(story_key="story-1", article_id="article-2", rank=1),
            StoryArticle(story_key="story-2", article_id="article-2", rank=0),
            StoryFacet(story_key="story-1", facet="runway_series"),
            StoryFacet(story_key="story-1", facet="trend_summary"),
            StoryFacet(story_key="story-2", facet="trend_summary"),
        ]
    )
    session.commit()
    return session


class _FakeFacetAssignmentService:
    def __init__(self) -> None:
        self.calls: list[tuple[Session, date, str]] = []

    async def assign_for_day(self, session: Session, business_day: date, *, run_id: str) -> list[object]:
        self.calls.append((session, business_day, run_id))
        return []


class _FakePackagingService:
    def __init__(self, plans: list[ResolvedDigestPlan]) -> None:
        self.calls: list[tuple[Session, date, str]] = []
        self._plans = plans

    async def build_plans_for_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[ResolvedDigestPlan]:
        self.calls.append((session, business_day, run_id))
        return self._plans


class _FakeReportWritingService:
    def __init__(
        self,
        *,
        selected_article_ids_by_facet: dict[str, tuple[str, ...]] | None = None,
        business_date_offset_days: int = 0,
        expose_selected_article_ids: bool = True,
    ) -> None:
        self.calls: list[tuple[Session, str, ResolvedDigestPlan]] = []
        self._selected_article_ids_by_facet = selected_article_ids_by_facet or {}
        self._business_date_offset_days = business_date_offset_days
        self._expose_selected_article_ids = expose_selected_article_ids

    async def write_digest(
        self,
        session: Session,
        plan: ResolvedDigestPlan,
        *,
        run_id: str,
    ) -> Digest:
        self.calls.append((session, run_id, plan))
        selected_article_ids = self._selected_article_ids_by_facet.get(plan.facet, plan.article_ids)
        digest = Digest(
            business_date=plan.business_date.fromordinal(plan.business_date.toordinal() + self._business_date_offset_days),
            facet=plan.facet,
            title_zh=f"写作标题-{plan.facet}",
            dek_zh="写作导语",
            body_markdown="# 正文",
            source_article_count=len(selected_article_ids),
            source_names_json=list(plan.source_names),
            created_run_id=run_id,
            generation_status="done",
            generation_error=None,
        )
        if self._expose_selected_article_ids:
            digest.selected_source_article_ids = selected_article_ids
        return digest


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

    def test_generate_for_day_orchestrates_subservices_and_allows_story_overlap(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        facet_assignment_service = _FakeFacetAssignmentService()
        packaging_service = _FakePackagingService(
            plans=[
                ResolvedDigestPlan(
                    business_date=date(2026, 3, 30),
                    facet="runway_series",
                    story_keys=("story-1",),
                    article_ids=("article-1",),
                    editorial_angle="秀场单稿",
                    title_zh="包装标题-1",
                    dek_zh="包装导语-1",
                    source_names=("Vogue",),
                ),
                ResolvedDigestPlan(
                    business_date=date(2026, 3, 30),
                    facet="trend_summary",
                    story_keys=("story-1", "story-2"),
                    article_ids=("article-2",),
                    editorial_angle="趋势综合稿",
                    title_zh="包装标题-2",
                    dek_zh="包装导语-2",
                    source_names=("WWD",),
                ),
            ]
        )
        report_writing_service = _FakeReportWritingService()
        service = DigestGenerationService(
            facet_assignment_service=facet_assignment_service,
            packaging_service=packaging_service,
            report_writing_service=report_writing_service,
        )

        persisted = asyncio.run(service.generate_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(1, len(facet_assignment_service.calls))
        self.assertEqual(1, len(packaging_service.calls))
        self.assertEqual(2, len(report_writing_service.calls))
        self.assertEqual(2, len(persisted))
        digest_rows = session.scalars(select(Digest).order_by(Digest.digest_key.asc())).all()
        self.assertEqual(2, len(digest_rows))
        digest_story_rows = session.execute(
            select(DigestStory.digest_key, DigestStory.story_key, DigestStory.rank).order_by(
                DigestStory.digest_key.asc(),
                DigestStory.rank.asc(),
            )
        ).all()
        self.assertEqual(3, len(digest_story_rows))
        story_one_occurrences = [row for row in digest_story_rows if row[1] == "story-1"]
        self.assertEqual(2, len(story_one_occurrences))
        digest_article_rows = session.execute(
            select(DigestArticle.article_id, DigestArticle.rank).order_by(
                DigestArticle.digest_key.asc(),
                DigestArticle.rank.asc(),
            )
        ).all()
        self.assertEqual(
            [("article-1", 0), ("article-2", 0)],
            sorted(digest_article_rows),
        )

    def test_generate_for_day_persists_writer_selected_source_article_order_and_subset(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        facet_assignment_service = _FakeFacetAssignmentService()
        packaging_service = _FakePackagingService(
            plans=[
                ResolvedDigestPlan(
                    business_date=date(2026, 3, 30),
                    facet="runway_series",
                    story_keys=("story-1",),
                    article_ids=("article-1", "article-2"),
                    editorial_angle="秀场单稿",
                    title_zh="包装标题-1",
                    dek_zh="包装导语-1",
                    source_names=("Vogue", "WWD"),
                ),
                ResolvedDigestPlan(
                    business_date=date(2026, 3, 30),
                    facet="trend_summary",
                    story_keys=("story-1", "story-2"),
                    article_ids=("article-2",),
                    editorial_angle="趋势综合稿",
                    title_zh="包装标题-2",
                    dek_zh="包装导语-2",
                    source_names=("WWD",),
                ),
            ]
        )
        report_writing_service = _FakeReportWritingService(
            selected_article_ids_by_facet={
                "runway_series": ("article-2",),
                "trend_summary": ("article-2",),
            }
        )
        service = DigestGenerationService(
            facet_assignment_service=facet_assignment_service,
            packaging_service=packaging_service,
            report_writing_service=report_writing_service,
        )

        asyncio.run(service.generate_for_day(session, date(2026, 3, 30), run_id="run-1"))

        digest_article_rows = session.execute(
            select(DigestArticle.article_id, DigestArticle.rank).order_by(
                DigestArticle.digest_key.asc(),
                DigestArticle.rank.asc(),
            )
        ).all()
        self.assertEqual(
            [("article-2", 0), ("article-2", 0)],
            digest_article_rows,
        )

    def test_constructor_propagates_shared_configuration_and_rate_limiter_to_nested_services(self) -> None:
        configuration = Configuration(
            api_key="test-key",
            base_url="https://openai.example/v1",
            story_summarization_model="story-model",
            story_summarization_model_max_tokens=1234,
            story_summarization_temperature=0.1,
            story_summarization_timeout_seconds=55,
            rag_model="rag-model",
            rag_model_max_tokens=4321,
            rag_temperature=0.2,
            rag_timeout_seconds=44,
            max_structured_output_retries=5,
            max_react_tool_calls=8,
        )
        shared_rate_limiter = object()
        fake_facet_service = object()
        fake_packaging_service = object()
        fake_report_writing_service = object()

        with patch(
            "backend.app.service.digest_generation_service.StoryFacetAssignmentService",
            return_value=fake_facet_service,
        ) as facet_service_mock:
            with patch(
                "backend.app.service.digest_generation_service.DigestPackagingService",
                return_value=fake_packaging_service,
            ) as packaging_service_mock:
                with patch(
                    "backend.app.service.digest_generation_service.DigestReportWritingService",
                    return_value=fake_report_writing_service,
                ) as report_service_mock:
                    service = DigestGenerationService(
                        configuration=configuration,
                        rate_limiter=shared_rate_limiter,
                    )

        facet_service_mock.assert_called_once_with(
            configuration=configuration,
            rate_limiter=shared_rate_limiter,
        )
        packaging_service_mock.assert_called_once_with(
            configuration=configuration,
            rate_limiter=shared_rate_limiter,
        )
        report_service_mock.assert_called_once_with(
            configuration=configuration,
            rate_limiter=shared_rate_limiter,
        )
        self.assertIs(service._facet_assignment_service, fake_facet_service)
        self.assertIs(service._packaging_service, fake_packaging_service)
        self.assertIs(service._report_writing_service, fake_report_writing_service)

    def test_generate_for_day_raises_when_packaging_returns_zero_plans_for_non_empty_input(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        facet_assignment_service = _FakeFacetAssignmentService()
        packaging_service = _FakePackagingService(plans=[])
        report_writing_service = _FakeReportWritingService()
        service = DigestGenerationService(
            facet_assignment_service=facet_assignment_service,
            packaging_service=packaging_service,
            report_writing_service=report_writing_service,
        )

        with self.assertRaisesRegex(RuntimeError, "zero digest plans.*2026-03-30"):
            asyncio.run(service.generate_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(0, len(report_writing_service.calls))

    def test_generate_for_day_raises_when_plan_business_date_mismatches_target_day(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        facet_assignment_service = _FakeFacetAssignmentService()
        packaging_service = _FakePackagingService(
            plans=[
                ResolvedDigestPlan(
                    business_date=date(2026, 3, 29),
                    facet="runway_series",
                    story_keys=("story-1",),
                    article_ids=("article-1",),
                    editorial_angle="秀场单稿",
                    title_zh="包装标题-1",
                    dek_zh="包装导语-1",
                    source_names=("Vogue",),
                )
            ]
        )
        report_writing_service = _FakeReportWritingService()
        service = DigestGenerationService(
            facet_assignment_service=facet_assignment_service,
            packaging_service=packaging_service,
            report_writing_service=report_writing_service,
        )

        with self.assertRaisesRegex(RuntimeError, "plan business_date mismatch"):
            asyncio.run(service.generate_for_day(session, date(2026, 3, 30), run_id="run-1"))

        self.assertEqual(0, len(report_writing_service.calls))

    def test_generate_for_day_raises_when_writer_contract_omits_selected_source_article_ids(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        facet_assignment_service = _FakeFacetAssignmentService()
        packaging_service = _FakePackagingService(
            plans=[
                ResolvedDigestPlan(
                    business_date=date(2026, 3, 30),
                    facet="runway_series",
                    story_keys=("story-1",),
                    article_ids=("article-1",),
                    editorial_angle="秀场单稿",
                    title_zh="包装标题-1",
                    dek_zh="包装导语-1",
                    source_names=("Vogue",),
                )
            ]
        )
        report_writing_service = _FakeReportWritingService(expose_selected_article_ids=False)
        service = DigestGenerationService(
            facet_assignment_service=facet_assignment_service,
            packaging_service=packaging_service,
            report_writing_service=report_writing_service,
        )

        with self.assertRaisesRegex(RuntimeError, "selected_source_article_ids"):
            asyncio.run(service.generate_for_day(session, date(2026, 3, 30), run_id="run-1"))
