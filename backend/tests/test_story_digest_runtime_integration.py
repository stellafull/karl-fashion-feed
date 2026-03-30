from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    Article,
    ArticleEventFrame,
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
from backend.app.service.story_clustering_service import StoryClusteringService


def _build_fake_story_cluster_llm() -> SimpleNamespace:
    raw_content = (
        '{"groups":[{"seed_event_frame_id":"frame-1","member_event_frame_ids":["frame-1"],'
        '"synopsis_zh":"Acme 同日秀场事件","event_type":"runway_show","anchor_json":{"brand":"Acme"}}]}'
    )

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
    session.add(
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
        )
    )
    session.add(
        ArticleEventFrame(
            event_frame_id="frame-1",
            article_id="article-1",
            business_date=business_day,
            event_type="runway_show",
            subject_json={"brand": "Acme", "person": "Jane"},
            action_text="发布",
            object_text="新品",
            place_text="Paris",
            collection_text="FW26",
            season_text="FW26",
            show_context_text="",
            evidence_json=[{"quote": "Acme FW26"}],
            signature_json={"brand": "Acme"},
            extraction_confidence=0.95,
            extraction_status="done",
            extraction_error=None,
        )
    )
    session.commit()
    return session


class _FakeFacetAssignmentService:
    async def assign_for_day(self, session: Session, business_day: date, *, run_id: str) -> list[StoryFacet]:
        _ = run_id
        story_keys = list(
            session.scalars(
                select(Story.story_key)
                .where(Story.business_date == business_day)
                .order_by(Story.story_key.asc())
            ).all()
        )
        rows = [StoryFacet(story_key=story_key, facet="runway_series") for story_key in story_keys]
        if rows:
            session.add_all(rows)
            session.flush()
            for row in rows:
                session.expunge(row)
        return rows


class _FakePackagingService:
    async def build_plans_for_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[ResolvedDigestPlan]:
        _ = run_id
        row = session.execute(
            select(Story.story_key, StoryArticle.article_id, Article.source_name)
            .join(StoryArticle, StoryArticle.story_key == Story.story_key)
            .join(Article, Article.article_id == StoryArticle.article_id)
            .where(Story.business_date == business_day)
            .order_by(Story.story_key.asc(), StoryArticle.rank.asc())
            .limit(1)
        ).first()
        if row is None:
            return []
        return [
            ResolvedDigestPlan(
                business_date=business_day,
                facet="runway_series",
                story_keys=(row[0],),
                article_ids=(row[1],),
                editorial_angle="同日秀场摘要",
                title_zh="同日秀场摘要",
                dek_zh="聚焦同日关键事件",
                source_names=(row[2],),
            )
        ]


class _FakeReportWritingService:
    async def write_digest(
        self,
        session: Session,
        plan: ResolvedDigestPlan,
        *,
        run_id: str,
    ) -> Digest:
        _ = session
        digest = Digest(
            business_date=plan.business_date,
            facet=plan.facet,
            title_zh="同日时尚摘要",
            dek_zh="覆盖当日核心事件",
            body_markdown="## 长文正文\n\nAcme 在巴黎发布 FW26 系列，形成单一主事件聚合。",
            source_article_count=len(plan.article_ids),
            source_names_json=list(plan.source_names),
            created_run_id=run_id,
            generation_status="done",
            generation_error=None,
        )
        digest.selected_source_article_ids = plan.article_ids
        return digest


class StoryDigestRuntimeIntegrationTest(unittest.TestCase):
    def test_same_day_runtime_clusters_stories_then_generates_digest(self) -> None:
        session = _build_session()
        self.addCleanup(session.close)
        business_day = date(2026, 3, 30)
        run_id = "run-1"

        stories = asyncio.run(
            StoryClusteringService(
                client=_build_fake_story_cluster_llm(),
                rate_limiter=_build_fake_rate_limiter(),
            ).cluster_business_day(
                session,
                business_day,
                run_id=run_id,
            )
        )

        digests = asyncio.run(
            DigestGenerationService(
                facet_assignment_service=_FakeFacetAssignmentService(),
                packaging_service=_FakePackagingService(),
                report_writing_service=_FakeReportWritingService(),
            ).generate_for_day(
                session,
                business_day,
                run_id=run_id,
            )
        )

        self.assertEqual(1, len(stories))
        self.assertEqual(1, len(digests))
        persisted_stories = session.scalars(select(Story).where(Story.business_date == business_day)).all()
        self.assertEqual(1, len(persisted_stories))
        persisted_digests = session.scalars(select(Digest).where(Digest.business_date == business_day)).all()
        self.assertEqual(1, len(persisted_digests))
        self.assertIn("长文正文", persisted_digests[0].body_markdown)
        self.assertGreater(len(persisted_digests[0].body_markdown.strip()), 20)

        digest_story_rows = session.execute(select(DigestStory.story_key)).all()
        digest_article_rows = session.execute(select(DigestArticle.article_id)).all()
        self.assertEqual(1, len(digest_story_rows))
        self.assertEqual(1, len(digest_article_rows))

        project_root = Path(__file__).resolve().parents[1]
        self.assertFalse((project_root / "app/service/strict_story_packing_service.py").exists())
        self.assertFalse((project_root / "app/prompts/strict_story_tiebreak_prompt.py").exists())
        self.assertFalse((project_root / "app/schemas/llm/strict_story_tiebreak.py").exists())
