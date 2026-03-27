"""Tests for public digest feed and digest detail APIs."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.app_main import app
from backend.app.core.database import Base, get_db
from backend.app.models import (
    Article,
    Digest,
    DigestArticle,
    DigestStrictStory,
    PipelineRun,
    StrictStory,
)


class DigestApiTest(unittest.TestCase):
    """Validate public digest API payload contract."""

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
        self._seed_data()

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def _enable_foreign_keys(self, dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def _seed_data(self) -> None:
        now = datetime(2026, 3, 27, 8, 0, tzinfo=UTC).replace(tzinfo=None)
        with self.session_factory() as session:
            self._insert_pipeline_run(session, now)
            self._insert_articles(session, now)
            self._insert_stories(session)
            self._insert_digest(session, now)
            self._insert_non_done_digest(session, now)
            session.commit()

    def _insert_pipeline_run(self, session: Session, now: datetime) -> None:
        session.add(
            PipelineRun(
                run_id="run-1",
                business_date=self.business_day,
                run_type="digest_daily",
                status="success",
                metadata_json={},
                started_at=now,
            )
        )
        session.flush()

    def _insert_articles(self, session: Session, now: datetime) -> None:
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
                    title_raw="Runway recap",
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
                    title_raw="Brand appointment",
                    summary_raw="summary",
                    markdown_rel_path="2026-03-27/article-2.md",
                    published_at=now,
                    discovered_at=now,
                    ingested_at=now,
                    metadata_json={},
                ),
            ]
        )

    def _insert_stories(self, session: Session) -> None:
        session.add(
            StrictStory(
                strict_story_key="strict-story-1",
                business_date=self.business_day,
                synopsis_zh="story 1",
                signature_json={"event_type": "runway_show"},
                frame_membership_json=["frame-1"],
                created_run_id="run-1",
                packing_status="done",
            )
        )
        session.flush()

    def _insert_digest(self, session: Session, now: datetime) -> None:
        session.add(
            Digest(
                digest_key="digest-1",
                business_date=self.business_day,
                facet="runway",
                title_zh="秀场要闻",
                dek_zh="核心摘要",
                body_markdown="## Body",
                hero_image_url="https://img.example.com/hero.jpg",
                source_article_count=2,
                source_names_json=["Vogue Runway", "WWD"],
                created_run_id="run-1",
                generation_status="done",
                created_at=now,
            )
        )
        session.flush()
        session.add_all(
            [
                DigestStrictStory(digest_key="digest-1", strict_story_key="strict-story-1", rank=0),
                DigestArticle(digest_key="digest-1", article_id="article-1", rank=0),
                DigestArticle(digest_key="digest-1", article_id="article-2", rank=1),
            ]
        )

    def _insert_non_done_digest(self, session: Session, now: datetime) -> None:
        session.add(
            Digest(
                digest_key="digest-pending",
                business_date=self.business_day,
                facet="brand",
                title_zh="处理中摘要",
                dek_zh="处理中",
                body_markdown="## pending",
                hero_image_url="",
                source_article_count=1,
                source_names_json=["WWD"],
                created_run_id="run-1",
                generation_status="running",
                created_at=now,
            )
        )
        session.flush()
        session.add(DigestArticle(digest_key="digest-pending", article_id="article-2", rank=0))

    def test_digest_feed_returns_public_cards_only(self) -> None:
        response = self.client.get("/api/v1/digests/feed")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["digests"]], ["digest-1"])
        self.assertTrue(
            {
                "id",
                "facet",
                "title",
                "dek",
                "image",
                "published",
                "article_count",
                "source_count",
                "source_names",
            }.issubset(set(payload["digests"][0]))
        )
        self.assertNotIn("topics", payload)

    def test_digest_detail_returns_flattened_sources_without_strict_story_internals(self) -> None:
        response = self.client.get("/api/v1/digests/digest-1")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(
            {
                "id",
                "facet",
                "title",
                "dek",
                "body_markdown",
                "hero_image",
                "published",
                "sources",
            }.issubset(set(payload))
        )
        self.assertNotIn("strict_stories", payload)

    def test_digest_detail_hides_non_done_digest(self) -> None:
        response = self.client.get("/api/v1/digests/digest-pending")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
