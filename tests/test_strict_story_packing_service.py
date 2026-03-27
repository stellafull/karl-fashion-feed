"""Tests for strict-story packing within one business day."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, date, datetime

from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError

from backend.app.core.database import Base
from backend.app.models import (
    Article,
    Digest,
    DigestStrictStory,
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


class _FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        message = type("Message", (), {"content": content})
        choice = type("Choice", (), {"message": message()})
        self.choices = [choice()]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    async def create(self, **kwargs: object) -> _FakeCompletionResponse:
        del kwargs
        return _FakeCompletionResponse(self._content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


class StrictStoryPackingServiceTest(unittest.TestCase):
    """Verify business-day strict-story packing and rerun replacement behavior."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self.engine, "connect", self._enable_foreign_keys)
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.service = StrictStoryPackingService()
        self.business_day = date(2026, 3, 27)
        self._seed_day_data(run_id="run-1")

    def _enable_foreign_keys(self, dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
                        frame_membership_json=["frame-1"],
                        created_run_id="run-old",
                        packing_status="done",
                    ),
                    StrictStory(
                        strict_story_key="old-b",
                        business_date=self.business_day,
                        synopsis_zh="old-b",
                        signature_json=signature,
                        frame_membership_json=["frame-2"],
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

    def test_task5_frame_replacement_uses_historical_membership_for_overlap(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()
        first_runway_key = next(
            story.strict_story_key for story in first if story.signature_json.get("event_type") == "runway_show"
        )

        with self.session_factory() as session:
            session.execute(delete(ArticleEventFrame).where(ArticleEventFrame.event_frame_id == "frame-2"))
            session.add(
                ArticleEventFrame(
                    event_frame_id="frame-4",
                    article_id="article-3",
                    business_date=self.business_day,
                    event_type="runway_show",
                    action_text="show update",
                    signature_json={"brand": "brand-a", "season": "fw26"},
                    extraction_confidence=0.92,
                )
            )
            session.commit()

        with self.session_factory() as session:
            second = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()
        second_runway_key = next(
            story.strict_story_key for story in second if story.signature_json.get("event_type") == "runway_show"
        )

        self.assertNotEqual(first_runway_key, second_runway_key)

    def test_tie_break_rejects_invalid_non_chinese_synopsis(self) -> None:
        invalid_payload = json.dumps(
            {
                "choice": {
                    "reuse_strict_story_key": "old-b",
                    "synopsis_zh": "invalid synopsis only english",
                }
            },
            ensure_ascii=False,
        )
        service = StrictStoryPackingService(client=_FakeClient(invalid_payload))
        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 7, 30, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-old-invalid",
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
                        frame_membership_json=["frame-1"],
                        created_run_id="run-old-invalid",
                        packing_status="done",
                    ),
                    StrictStory(
                        strict_story_key="old-b",
                        business_date=self.business_day,
                        synopsis_zh="old-b",
                        signature_json=signature,
                        frame_membership_json=["frame-2"],
                        created_run_id="run-old-invalid",
                        packing_status="done",
                    ),
                    StrictStoryFrame(strict_story_key="old-a", event_frame_id="frame-1", rank=0),
                    StrictStoryFrame(strict_story_key="old-b", event_frame_id="frame-2", rank=0),
                ]
            )
            session.commit()

        with self.session_factory() as session:
            with self.assertRaises(ValidationError):
                asyncio.run(service.pack_business_day(session, self.business_day, run_id="run-1"))

    def test_tie_break_rejects_json_like_synopsis_noise(self) -> None:
        invalid_payload = json.dumps(
            {
                "choice": {
                    "reuse_strict_story_key": "old-b",
                    "synopsis_zh": '{"事件":"品牌动态","summary":"这是噪声"}',
                }
            },
            ensure_ascii=False,
        )
        service = StrictStoryPackingService(client=_FakeClient(invalid_payload))
        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 7, 31, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-old-invalid-json",
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
                        frame_membership_json=["frame-1"],
                        created_run_id="run-old-invalid-json",
                        packing_status="done",
                    ),
                    StrictStory(
                        strict_story_key="old-b",
                        business_date=self.business_day,
                        synopsis_zh="old-b",
                        signature_json=signature,
                        frame_membership_json=["frame-2"],
                        created_run_id="run-old-invalid-json",
                        packing_status="done",
                    ),
                    StrictStoryFrame(strict_story_key="old-a", event_frame_id="frame-1", rank=0),
                    StrictStoryFrame(strict_story_key="old-b", event_frame_id="frame-2", rank=0),
                ]
            )
            session.commit()

        with self.session_factory() as session:
            with self.assertRaises(ValidationError):
                asyncio.run(service.pack_business_day(session, self.business_day, run_id="run-1"))

    def test_tie_break_choice_rejects_garbled_mixed_synopsis(self) -> None:
        with self.assertRaises(ValidationError):
            StrictStoryTieBreakChoice(
                reuse_strict_story_key="old-b",
                synopsis_zh="总结ab12cd34ef56gh78",
            )

    def test_rerun_keeps_digest_membership_when_key_is_reused(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()
        runway_key = next(
            story.strict_story_key for story in first if story.signature_json.get("event_type") == "runway_show"
        )

        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 10, 0, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-digest",
                    business_date=self.business_day,
                    run_type="digest_daily",
                    status="success",
                    metadata_json={},
                    started_at=now,
                )
            )
            session.commit()

        with self.session_factory() as session:
            session.add(
                Digest(
                    digest_key="digest-1",
                    business_date=self.business_day,
                    facet="top",
                    title_zh="今日摘要",
                    dek_zh="导语",
                    body_markdown="正文",
                    created_run_id="run-digest",
                    generation_status="done",
                )
            )
            session.add(
                DigestStrictStory(
                    digest_key="digest-1",
                    strict_story_key=runway_key,
                    rank=0,
                )
            )
            session.commit()

        with self.session_factory() as session:
            asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        with self.session_factory() as session:
            links = session.scalars(select(DigestStrictStory.strict_story_key)).all()
        self.assertEqual(links, [runway_key])

    def test_rerun_with_membership_change_reuses_key_and_keeps_digest_link(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()
        runway_key = next(
            story.strict_story_key for story in first if story.signature_json.get("event_type") == "runway_show"
        )

        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 10, 1, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-digest-rerun",
                    business_date=self.business_day,
                    run_type="digest_daily",
                    status="success",
                    metadata_json={},
                    started_at=now,
                )
            )
            session.commit()

        with self.session_factory() as session:
            session.add(
                Digest(
                    digest_key="digest-2",
                    business_date=self.business_day,
                    facet="top",
                    title_zh="复跑摘要",
                    dek_zh="导语",
                    body_markdown="正文",
                    created_run_id="run-digest-rerun",
                    generation_status="done",
                )
            )
            session.add(DigestStrictStory(digest_key="digest-2", strict_story_key=runway_key, rank=0))
            session.add(
                ArticleEventFrame(
                    event_frame_id="frame-4",
                    article_id="article-3",
                    business_date=self.business_day,
                    event_type="runway_show",
                    action_text="show follow-up",
                    signature_json={"brand": "brand-a", "season": "fw26"},
                    extraction_confidence=0.9,
                )
            )
            session.commit()

        with self.session_factory() as session:
            second = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()
        second_runway_key = next(
            story.strict_story_key for story in second if story.signature_json.get("event_type") == "runway_show"
        )
        self.assertEqual(second_runway_key, runway_key)

        with self.session_factory() as session:
            links = session.scalars(
                select(DigestStrictStory.strict_story_key).where(DigestStrictStory.digest_key == "digest-2")
            ).all()
            frame_ids = session.scalars(
                select(StrictStoryFrame.event_frame_id)
                .where(StrictStoryFrame.strict_story_key == runway_key)
                .order_by(StrictStoryFrame.rank.asc())
            ).all()
        self.assertEqual(links, [runway_key])
        self.assertEqual(frame_ids, ["frame-1", "frame-2", "frame-4"])

    def test_mints_new_key_when_overlap_ratio_is_below_half(self) -> None:
        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 7, 35, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-old-low-overlap",
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
            session.add(
                StrictStory(
                    strict_story_key="old-low-overlap",
                    business_date=self.business_day,
                    synopsis_zh="旧故事",
                    signature_json=signature,
                    frame_membership_json=["frame-1", "frame-3"],
                    created_run_id="run-old-low-overlap",
                    packing_status="done",
                )
            )
            session.add_all(
                [
                    StrictStoryFrame(strict_story_key="old-low-overlap", event_frame_id="frame-1", rank=0),
                    StrictStoryFrame(strict_story_key="old-low-overlap", event_frame_id="frame-3", rank=1),
                ]
            )
            session.commit()

        with self.session_factory() as session:
            stories = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()
        runway_story = next(
            story for story in stories if story.signature_json.get("event_type") == "runway_show"
        )
        self.assertNotEqual(runway_story.strict_story_key, "old-low-overlap")

    def test_incompatible_signatures_do_not_reuse_existing_key(self) -> None:
        with self.session_factory() as session:
            now = datetime(2026, 3, 27, 7, 45, tzinfo=UTC).replace(tzinfo=None)
            session.add(
                PipelineRun(
                    run_id="run-old-incompatible",
                    business_date=self.business_day,
                    run_type="digest_daily",
                    status="success",
                    metadata_json={},
                    started_at=now,
                )
            )
            signature = {
                "event_type": "brand_appointment",
                "signature_json": {"brand": "brand-a", "season": "fw26"},
            }
            session.add(
                StrictStory(
                    strict_story_key="old-incompatible",
                    business_date=self.business_day,
                    synopsis_zh="旧不兼容故事",
                    signature_json=signature,
                    frame_membership_json=["frame-1"],
                    created_run_id="run-old-incompatible",
                    packing_status="done",
                )
            )
            session.add(StrictStoryFrame(strict_story_key="old-incompatible", event_frame_id="frame-1", rank=0))
            session.commit()

        with self.session_factory() as session:
            stories = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        runway_story = next(
            story for story in stories if story.signature_json.get("event_type") == "runway_show"
        )
        self.assertNotEqual(runway_story.strict_story_key, "old-incompatible")

    def test_default_synopsis_is_chinese_readable_text(self) -> None:
        with self.session_factory() as session:
            stories = asyncio.run(self.service.pack_business_day(session, self.business_day, run_id="run-1"))
            session.commit()

        runway_story = next(
            story for story in stories if story.signature_json.get("event_type") == "runway_show"
        )
        self.assertNotIn("{", runway_story.synopsis_zh)
        self.assertNotIn("}", runway_story.synopsis_zh)
        self.assertNotIn("runway_show", runway_story.synopsis_zh)
        self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in runway_story.synopsis_zh))

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
