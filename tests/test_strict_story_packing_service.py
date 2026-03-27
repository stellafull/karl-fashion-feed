"""Tests for strict-story packing within one business day."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, date, datetime

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import (
    Article,
    ArticleEventFrame,
    PipelineRun,
    StrictStory,
    StrictStoryFrame,
)
from backend.app.schemas.llm.strict_story_tiebreak import (
    StrictStoryTieBreakChoice,
    StrictStoryTieBreakSchema,
)
from backend.app.service.strict_story_packing_service import StrictStoryPackingService


class StubTieBreakPackingService(StrictStoryPackingService):
    """Test double that bypasses external LLM calls for tie-break resolution."""

    def __init__(self, tie_break_schema: StrictStoryTieBreakSchema) -> None:
        super().__init__()
        self.tie_break_schema = tie_break_schema
        self.tie_break_calls = 0

    async def _run_tie_break(self, group: object, candidates: object) -> StrictStoryTieBreakSchema:
        del group, candidates
        self.tie_break_calls += 1
        return self.tie_break_schema


class StrictStoryPackingServiceTest(unittest.TestCase):
    """Verify business-day strict-story packing and rerun replacement behavior."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.service = StrictStoryPackingService()
        self.business_day = date(2026, 3, 27)
        self._seed_day_data(run_id="run-1")

    def _seed_day_data(self, *, run_id: str) -> None:
        now = datetime(2026, 3, 27, 8, 0, tzinfo=UTC).replace(tzinfo=None)
        with self.session_factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    business_date=self.business_day,
                    run_type="digest_daily",
                    status="running",
                    metadata_json={},
                    started_at=now,
                )
            )

            article_ids = ("article-1", "article-2", "article-3")
            for article_id in article_ids:
                session.add(
                    Article(
                        article_id=article_id,
                        source_name="Vogue Runway",
                        source_type="rss",
                        source_lang="en",
                        category="fashion",
                        canonical_url=f"https://example.com/{article_id}",
                        original_url=f"https://example.com/original/{article_id}",
                        title_raw=f"title {article_id}",
                        summary_raw="summary",
                        markdown_rel_path=f"2026-03-27/{article_id}.md",
                        published_at=now,
                        discovered_at=now,
                        ingested_at=now,
                        metadata_json={},
                    )
                )

            session.add_all(
                [
                    ArticleEventFrame(
                        event_frame_id="frame-1",
                        article_id="article-1",
                        business_date=self.business_day,
                        event_type="runway_show",
                        action_text="show staged",
                        signature_json={"brand": "brand-a", "season": "fw26"},
                        extraction_confidence=0.98,
                    ),
                    ArticleEventFrame(
                        event_frame_id="frame-2",
                        article_id="article-2",
                        business_date=self.business_day,
                        event_type="runway_show",
                        action_text="show recap",
                        signature_json={"brand": "brand-a", "season": "fw26"},
                        extraction_confidence=0.91,
                    ),
                    ArticleEventFrame(
                        event_frame_id="frame-3",
                        article_id="article-3",
                        business_date=self.business_day,
                        event_type="brand_appointment",
                        action_text="appointment",
                        signature_json={"brand": "brand-b", "person": "person-x"},
                        extraction_confidence=0.95,
                    ),
                ]
            )
            session.commit()

    def test_pack_day_groups_frames_into_strict_stories(self) -> None:
        with self.session_factory() as session:
            stories = asyncio.run(
                self.service.pack_business_day(session, self.business_day, run_id="run-1")
            )
            session.commit()

        self.assertEqual(len(stories), 2)

    def test_pack_day_reuses_strict_story_key_when_signature_and_membership_match(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        with self.session_factory() as session:
            second = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        self.assertEqual(
            [item.strict_story_key for item in first],
            [item.strict_story_key for item in second],
        )

    def test_rerun_removes_stale_strict_story_rows_for_same_day(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        with self.session_factory() as session:
            self._delete_one_frame(session)
            session.commit()

        with self.session_factory() as session:
            second = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        self.assertLess(len(second), len(first))

        with self.session_factory() as session:
            self.assertTrue(self._no_stale_strict_story_rows_remain(session))

    def test_tie_break_reads_choice_payload_and_reuses_selected_key(self) -> None:
        service = StubTieBreakPackingService(
            StrictStoryTieBreakSchema(
                choice=StrictStoryTieBreakChoice(
                    reuse_strict_story_key="old-b",
                    synopsis_zh="模型复核：沿用 old-b",
                )
            )
        )
        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 7, 30, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-old",
                    business_date=self.business_day,
                    run_type="digest_daily",
                    status="success",
                    metadata_json={},
                    started_at=now,
                )
            )
            signature = {
                "event_type": "runway_show",
                "signature_json": {"brand": "brand-a", "season": "fw26"},
            }
            session.add_all(
                [
                    StrictStory(
                        strict_story_key="old-a",
                        business_date=self.business_day,
                        synopsis_zh="old-a",
                        signature_json=signature,
                        created_run_id="run-old",
                        packing_status="done",
                    ),
                    StrictStory(
                        strict_story_key="old-b",
                        business_date=self.business_day,
                        synopsis_zh="old-b",
                        signature_json=signature,
                        created_run_id="run-old",
                        packing_status="done",
                    ),
                    StrictStoryFrame(strict_story_key="old-a", event_frame_id="frame-1", rank=0),
                    StrictStoryFrame(strict_story_key="old-b", event_frame_id="frame-2", rank=0),
                ]
            )
            session.commit()

        with self.session_factory() as session:
            stories = asyncio.run(service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        runway_story = next(
            story for story in stories if story.signature_json.get("event_type") == "runway_show"
        )
        self.assertEqual(runway_story.strict_story_key, "old-b")
        self.assertEqual(runway_story.synopsis_zh, "模型复核：沿用 old-b")
        self.assertEqual(service.tie_break_calls, 1)

    def _delete_one_frame(self, session: Session) -> None:
        session.execute(
            delete(ArticleEventFrame).where(
                ArticleEventFrame.business_date == self.business_day,
                ArticleEventFrame.event_frame_id == "frame-3",
            )
        )

    def _no_stale_strict_story_rows_remain(self, session: Session) -> bool:
        strict_story_keys = session.scalars(
            select(StrictStory.strict_story_key).where(StrictStory.business_date == self.business_day)
        ).all()
        if not strict_story_keys:
            return True

        mapped_frame_ids = session.scalars(
            select(StrictStoryFrame.event_frame_id).where(StrictStoryFrame.strict_story_key.in_(strict_story_keys))
        ).all()
        current_frame_ids = session.scalars(
            select(ArticleEventFrame.event_frame_id).where(ArticleEventFrame.business_date == self.business_day)
        ).all()
        return sorted(mapped_frame_ids) == sorted(current_frame_ids)
