"""End-to-end runtime integration test for business-day digest persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import asyncio
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import Article, ArticleEventFrame, Digest, PipelineRun, StrictStory, ensure_article_storage_schema
from backend.app.schemas.llm.digest_generation import DigestGenerationSchema, DigestPlan
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.tasks.aggregation_tasks import generate_digests_for_day, pack_strict_stories_for_day


class _SeededDigestGenerationService(DigestGenerationService):
    """Return one deterministic digest plan for the seeded strict-story set."""

    async def _select_digest_plans(  # type: ignore[override]
        self,
        strict_stories: list[object],
    ) -> DigestGenerationSchema:
        if not strict_stories:
            return DigestGenerationSchema()
        strict_story_key = strict_stories[0].strict_story_key
        return DigestGenerationSchema(
            digests=[
                DigestPlan(
                    facet="runway",
                    strict_story_keys=[strict_story_key],
                    title_zh="当日要闻",
                    dek_zh="测试摘要",
                    body_markdown="## 当日要闻\n- 测试内容",
                )
            ]
        )


@dataclass(frozen=True)
class BusinessDayRuntimeResult:
    """Captured runtime state after the seeded business-day run finishes."""

    digest_count: int
    strict_story_count: int
    pipeline_status: str
    table_names: tuple[str, ...]


def _enable_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def run_seeded_business_day() -> BusinessDayRuntimeResult:
    """Execute strict-story packing and digest generation against a seeded day."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", _enable_foreign_keys)
    session_factory = sessionmaker(bind=engine)
    ensure_article_storage_schema(engine)

    business_day = date(2026, 3, 27)
    observed_at = datetime(2026, 3, 27, 8, 0, tzinfo=UTC).replace(tzinfo=None)

    with session_factory() as session:
        session.add(
            PipelineRun(
                run_id="run-1",
                business_date=business_day,
                run_type="digest_daily",
                status="running",
                strict_story_status="queued",
                strict_story_updated_at=observed_at,
                strict_story_token=1,
                digest_status="pending",
                digest_updated_at=observed_at,
                digest_token=0,
                started_at=observed_at,
                metadata_json={},
            )
        )
        session.add(
            Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-1",
                original_url="https://example.com/original/article-1",
                title_raw="Article 1",
                summary_raw="Summary",
                markdown_rel_path="2026-03-27/article-1.md",
                published_at=observed_at,
                discovered_at=observed_at,
                ingested_at=observed_at,
                metadata_json={},
                parse_status="done",
                parse_updated_at=observed_at,
                event_frame_status="done",
                event_frame_updated_at=observed_at,
            )
        )
        session.add(
            ArticleEventFrame(
                event_frame_id="frame-1",
                article_id="article-1",
                business_date=business_day,
                event_type="runway_show",
                action_text="show staged",
                extraction_confidence=0.99,
                signature_json={"brand": "brand-a", "season": "fw26"},
            )
        )
        session.commit()

    with patch("backend.app.tasks.aggregation_tasks.SessionLocal", session_factory):
        pack_strict_stories_for_day(business_day.isoformat(), "run-1", 1)

        with session_factory() as session:
            run = session.get(PipelineRun, "run-1")
            assert run is not None
            run.digest_status = "queued"
            run.digest_updated_at = observed_at
            run.digest_token = 1
            session.commit()

        with patch(
            "backend.app.tasks.aggregation_tasks.DigestGenerationService",
            _SeededDigestGenerationService,
        ):
            generate_digests_for_day(business_day.isoformat(), "run-1", 1)

    inspector = inspect(engine)
    with session_factory() as session:
        digest_count = len(session.scalars(select(Digest)).all())
        strict_story_count = len(session.scalars(select(StrictStory)).all())
        run = session.get(PipelineRun, "run-1")
        assert run is not None
        return BusinessDayRuntimeResult(
            digest_count=digest_count,
            strict_story_count=strict_story_count,
            pipeline_status=run.status,
            table_names=tuple(sorted(inspector.get_table_names())),
        )


class DigestRuntimeIntegrationTest(unittest.TestCase):
    """Verify business-day runtime persistence after the digest refactor."""

    def test_business_day_runtime_persists_digests_without_story_tables(self) -> None:
        result = run_seeded_business_day()

        self.assertEqual(result.digest_count, 1)
        self.assertEqual(result.strict_story_count, 1)
        self.assertEqual(result.pipeline_status, "done")
        self.assertNotIn("story", result.table_names)
        self.assertNotIn("story_article", result.table_names)


if __name__ == "__main__":
    unittest.main()
