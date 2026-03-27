"""Tests for digest generation from strict stories."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import (
    Article,
    Digest,
    DigestStrictStory,
    PipelineRun,
    StrictStory,
    StrictStoryArticle,
)
from backend.app.schemas.llm.digest_generation import DigestGenerationSchema, DigestPlan
from backend.app.service.digest_generation_service import DigestGenerationService, _StrictStoryInput


class StubDigestGenerationService(DigestGenerationService):
    """Deterministic digest planner for service-level persistence tests."""

    async def _select_digest_plans(
        self, strict_stories: list[object]
    ) -> DigestGenerationSchema:  # type: ignore[override]
        del strict_stories

        return DigestGenerationSchema(
            digests=[
                DigestPlan(
                    facet="runway",
                    strict_story_keys=["strict-story-1"],
                    title_zh="秀场要闻",
                    dek_zh="当日核心秀场动态",
                    body_markdown="## Runway\n- A",
                ),
                DigestPlan(
                    facet="brand",
                    strict_story_keys=["strict-story-2"],
                    title_zh="品牌要闻",
                    dek_zh="当日品牌动态",
                    body_markdown="## Brand\n- B",
                ),
            ]
        )


class _FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        message = type("Message", (), {"content": content})
        choice = type("Choice", (), {"message": message()})
        self.choices = [choice()]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> _FakeCompletionResponse:
        self.calls.append(dict(kwargs))
        return _FakeCompletionResponse(self._content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


class DigestGenerationServiceTest(unittest.TestCase):
    """Verify digest generation replacement and key reuse behavior."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self.engine, "connect", self._enable_foreign_keys)
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.business_day = date(2026, 3, 27)
        self._seed_day_data(run_id="run-1")
        self.service = StubDigestGenerationService()

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
            session.add_all(
                [
                    Article(
                        article_id="article-1",
                        source_name="Vogue Runway",
                        source_type="rss",
                        source_lang="en",
                        category="fashion",
                        canonical_url="https://example.com/article-1",
                        original_url="https://example.com/original/article-1",
                        title_raw="article 1",
                        summary_raw="summary",
                        markdown_rel_path="2026-03-27/article-1.md",
                        published_at=now,
                        discovered_at=now,
                        ingested_at=now,
                        metadata_json={},
                    ),
                    Article(
                        article_id="article-2",
                        source_name="WWD",
                        source_type="rss",
                        source_lang="en",
                        category="fashion",
                        canonical_url="https://example.com/article-2",
                        original_url="https://example.com/original/article-2",
                        title_raw="article 2",
                        summary_raw="summary",
                        markdown_rel_path="2026-03-27/article-2.md",
                        published_at=now,
                        discovered_at=now,
                        ingested_at=now,
                        metadata_json={},
                    ),
                ]
            )
            session.add_all(
                [
                    StrictStory(
                        strict_story_key="strict-story-1",
                        business_date=self.business_day,
                        synopsis_zh="story 1",
                        signature_json={"event_type": "runway_show"},
                        frame_membership_json=["frame-1"],
                        created_run_id=run_id,
                        packing_status="done",
                    ),
                    StrictStory(
                        strict_story_key="strict-story-2",
                        business_date=self.business_day,
                        synopsis_zh="story 2",
                        signature_json={"event_type": "brand_appointment"},
                        frame_membership_json=["frame-2"],
                        created_run_id=run_id,
                        packing_status="done",
                    ),
                    StrictStoryArticle(
                        strict_story_key="strict-story-1",
                        article_id="article-1",
                        rank=0,
                    ),
                    StrictStoryArticle(
                        strict_story_key="strict-story-2",
                        article_id="article-2",
                        rank=0,
                    ),
                ]
            )
            session.commit()

    def test_generate_digests_persists_body_markdown_and_memberships(self) -> None:
        with self.session_factory() as session:
            digests = asyncio.run(
                self.service.generate_for_day(session, self.business_day, run_id="run-1")
            )
            session.commit()

        self.assertEqual(len(digests), 2)
        self.assertTrue(all(digest.body_markdown for digest in digests))

        with self.session_factory() as session:
            persisted_links = list(
                session.scalars(
                    select(DigestStrictStory).order_by(DigestStrictStory.digest_key.asc())
                ).all()
            )
        self.assertEqual(len(persisted_links), 2)

    def test_generate_digests_supports_omission_and_multi_story_digest(self) -> None:
        service = StubDigestGenerationService()
        with self.session_factory() as session:
            session.add(
                StrictStory(
                    strict_story_key="strict-story-3",
                    business_date=self.business_day,
                    synopsis_zh="story 3",
                    signature_json={"event_type": "campaign_launch"},
                    frame_membership_json=["frame-3"],
                    created_run_id="run-1",
                    packing_status="done",
                )
            )
            session.add(
                StrictStoryArticle(
                    strict_story_key="strict-story-3",
                    article_id="article-1",
                    rank=0,
                )
            )
            session.commit()

        async def grouped_plans(_: list[object]) -> DigestGenerationSchema:
            return DigestGenerationSchema(
                digests=[
                    DigestPlan(
                        facet="runway",
                        strict_story_keys=["strict-story-1", "strict-story-2"],
                        title_zh="综合要闻",
                        dek_zh="聚合两条 strict_story",
                        body_markdown="## 综合",
                    )
                ]
            )

        service._select_digest_plans = grouped_plans  # type: ignore[method-assign]
        with self.session_factory() as session:
            digests = asyncio.run(service.generate_for_day(session, self.business_day, run_id="run-1"))
            session.commit()

        self.assertEqual(len(digests), 1)
        with self.session_factory() as session:
            links = list(
                session.scalars(
                    select(DigestStrictStory)
                    .order_by(DigestStrictStory.rank.asc(), DigestStrictStory.strict_story_key.asc())
                ).all()
            )
        self.assertEqual([link.strict_story_key for link in links], ["strict-story-1", "strict-story-2"])

    def test_select_digest_plans_uses_prompt_and_structured_schema(self) -> None:
        llm_content = json.dumps(
            {
                "digests": [
                    {
                        "facet": "industry",
                        "strict_story_keys": ["strict-story-1", "strict-story-2"],
                        "title_zh": "行业综合",
                        "dek_zh": "两条聚合",
                        "body_markdown": "## 行业综合",
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake_client = _FakeClient(llm_content)
        service = DigestGenerationService(client=fake_client)  # type: ignore[arg-type]
        strict_stories = [
            _StrictStoryInput(
                strict_story_key="strict-story-1",
                synopsis_zh="story 1",
                event_type="runway_show",
                article_ids=("article-1",),
                source_names=("Vogue Runway",),
            ),
            _StrictStoryInput(
                strict_story_key="strict-story-2",
                synopsis_zh="story 2",
                event_type="brand_appointment",
                article_ids=("article-2",),
                source_names=("WWD",),
            ),
            _StrictStoryInput(
                strict_story_key="strict-story-3",
                synopsis_zh="story 3",
                event_type="campaign_launch",
                article_ids=("article-3",),
                source_names=("BoF",),
            ),
        ]

        with patch(
            "backend.app.service.digest_generation_service.build_digest_generation_prompt",
            return_value="DIGEST_PROMPT",
        ) as prompt_mock:
            result = asyncio.run(service._select_digest_plans(strict_stories))

        self.assertEqual(len(result.digests), 1)
        self.assertEqual(
            result.digests[0].strict_story_keys,
            ["strict-story-1", "strict-story-2"],
        )
        prompt_mock.assert_called_once_with()
        self.assertEqual(len(fake_client.chat.completions.calls), 1)
        call = fake_client.chat.completions.calls[0]
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertEqual(call["messages"][0]["content"], "DIGEST_PROMPT")
        user_payload = json.loads(call["messages"][1]["content"])
        self.assertEqual(
            [item["strict_story_key"] for item in user_payload["strict_stories"]],
            ["strict-story-1", "strict-story-2", "strict-story-3"],
        )

    def test_generate_digests_reuses_digest_key_for_same_facet_and_members(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.generate_for_day(session, self.business_day, run_id="run-1"))
            session.commit()

        with self.session_factory() as session:
            second = asyncio.run(self.service.generate_for_day(session, self.business_day, run_id="run-1"))
            session.commit()

        self.assertEqual([item.digest_key for item in first], [item.digest_key for item in second])

    def test_rerun_removes_stale_digests_for_same_day(self) -> None:
        with self.session_factory() as session:
            first = asyncio.run(self.service.generate_for_day(session, self.business_day, run_id="run-1"))
            session.commit()

        with self.session_factory() as session:
            self._delete_one_digest_candidate(session, self.business_day)
            session.commit()

        with self.session_factory() as session:
            second = asyncio.run(self.service.generate_for_day(session, self.business_day, run_id="run-1"))
            session.commit()

        self.assertLess(len(second), len(first))
        with self.session_factory() as session:
            self.assertTrue(self._no_stale_digest_rows_remain(session, self.business_day))

    def _delete_one_digest_candidate(self, session: Session, business_day: date) -> None:
        del business_day
        session.execute(
            delete(StrictStory).where(StrictStory.strict_story_key == "strict-story-2")
        )

    def _no_stale_digest_rows_remain(self, session: Session, business_day: date) -> bool:
        digests = list(
            session.scalars(select(Digest).where(Digest.business_date == business_day)).all()
        )
        if not digests:
            return True
        digest_keys = {item.digest_key for item in digests}
        links = list(
            session.scalars(
                select(DigestStrictStory).where(DigestStrictStory.digest_key.in_(digest_keys))
            ).all()
        )
        linked_story_keys = {item.strict_story_key for item in links}
        stories = list(
            session.scalars(select(StrictStory).where(StrictStory.business_date == business_day)).all()
        )
        valid_story_keys = {item.strict_story_key for item in stories}
        return linked_story_keys.issubset(valid_story_keys)


if __name__ == "__main__":
    unittest.main()
