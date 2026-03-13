import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_mock_engine, inspect
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import backend.app.models  # noqa: F401
from backend.app.core.database import Base, create_engine_from_url
from backend.app.models import Document, DocumentAsset, RetrievalUnitRef, Story, StoryArticle


class ModelMetadataTests(unittest.TestCase):
    def test_create_all_registers_raw_document_and_story_tables(self):
        engine = create_engine_from_url("sqlite+pysqlite:///:memory:")

        Base.metadata.create_all(engine)

        inspector = inspect(engine)
        self.assertEqual(
            set(inspector.get_table_names()),
            {
                "document",
                "document_asset",
                "retrieval_unit_ref",
                "story",
                "story_article",
            },
        )

    def test_document_asset_unique_constraint_is_defined(self):
        constraints = {constraint.name for constraint in DocumentAsset.__table__.constraints}

        self.assertIn("uq_document_asset_article_url", constraints)

    def test_retrieval_unit_ref_unique_constraint_is_defined(self):
        constraints = {constraint.name for constraint in RetrievalUnitRef.__table__.constraints}

        self.assertIn("uq_retrieval_unit_ref_article_unit_chunk", constraints)

    def test_story_article_uses_composite_primary_key(self):
        primary_keys = {column.name for column in StoryArticle.__table__.primary_key.columns}

        self.assertEqual(primary_keys, {"story_key", "article_id"})

    def test_story_table_keeps_immutable_snapshot_fields_only(self):
        columns = set(Story.__table__.columns.keys())

        self.assertIn("story_key", columns)
        self.assertIn("first_seen_at", columns)
        self.assertIn("last_aggregated_at", columns)
        self.assertNotIn("status", columns)
        self.assertNotIn("last_seen_at", columns)
        self.assertNotIn("merged_into_story_key", columns)
        self.assertNotIn("updated_at", columns)

    def test_story_article_has_story_sort_order_index(self):
        index_names = {index.name for index in StoryArticle.__table__.indexes}

        self.assertIn("ix_story_article_story_key_sort_order", index_names)

    def test_postgres_schema_generation_mentions_new_tables(self):
        statements: list[str] = []
        engine = create_mock_engine("postgresql://", lambda sql, *_, **__: statements.append(str(sql)))

        Base.metadata.create_all(engine)

        ddl = "\n".join(statements)
        self.assertIn("CREATE TABLE document_asset", ddl)
        self.assertIn("CREATE TABLE retrieval_unit_ref", ddl)
        self.assertIn("CREATE TABLE story", ddl)
        self.assertIn("CREATE TABLE story_article", ddl)


class StoryRepresentativeCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def test_representative_article_membership_is_required(self):
        with self.session_factory() as session:
            session.add_all(
                [
                    self._build_document("article_001"),
                    self._build_document("article_002"),
                    self._build_story(representative_article_id="article_002"),
                    StoryArticle(
                        story_key="story-001",
                        article_id="article_001",
                        is_representative=True,
                    ),
                ]
            )

            with self.assertRaisesRegex(ValueError, "representative_article_id must match a story_article"):
                session.commit()

    def test_representative_flag_is_normalized_from_story_snapshot(self):
        with self.session_factory() as session:
            session.add_all(
                [
                    self._build_document("article_001"),
                    self._build_document("article_002"),
                    self._build_story(representative_article_id="article_001"),
                    StoryArticle(
                        story_key="story-001",
                        article_id="article_001",
                        is_representative=False,
                    ),
                    StoryArticle(
                        story_key="story-001",
                        article_id="article_002",
                        is_representative=True,
                    ),
                ]
            )

            session.commit()

            stored_articles = {
                story_article.article_id: story_article.is_representative
                for story_article in session.query(StoryArticle).all()
            }

        self.assertEqual(
            stored_articles,
            {
                "article_001": True,
                "article_002": False,
            },
        )

    def _build_document(self, article_id: str) -> Document:
        return Document(
            article_id=article_id,
            source_id="source-001",
            canonical_url=f"https://example.com/{article_id}",
            title=f"Title for {article_id}",
            source_payload={},
        )

    def _build_story(self, representative_article_id: str | None) -> Story:
        timestamp = datetime(2026, 3, 12, 8, 0, tzinfo=timezone.utc)
        return Story(
            story_key="story-001",
            title="Story 001",
            key_points=[],
            topic_tags=[],
            representative_article_id=representative_article_id,
            article_count=2,
            source_count=1,
            first_seen_at=timestamp,
            last_aggregated_at=timestamp,
            metadata_json={},
        )
