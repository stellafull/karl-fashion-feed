from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect, text

from backend.app.models import ensure_article_storage_schema


class StorageSchemaTest(unittest.TestCase):
    def test_schema_creates_story_pipeline_columns(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")

        ensure_article_storage_schema(engine)

        inspector = inspect(engine)
        story_columns = {column["name"] for column in inspector.get_columns("story")}
        self.assertTrue(
            {
                "story_key",
                "created_run_id",
                "title_zh",
                "summary_zh",
                "key_points_json",
                "tags_json",
                "category",
                "hero_image_url",
                "source_article_count",
            }.issubset(story_columns)
        )

        story_article_columns = {column["name"] for column in inspector.get_columns("story_article")}
        self.assertTrue({"story_key", "article_id", "rank"}.issubset(story_article_columns))

    def test_schema_recreates_legacy_story_tables(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE story (
                        story_key VARCHAR(36) PRIMARY KEY,
                        title TEXT NOT NULL,
                        summary TEXT,
                        key_points JSON NOT NULL,
                        topic_tags JSON NOT NULL,
                        category_id VARCHAR(64),
                        category_name VARCHAR(64),
                        cover_image_url TEXT,
                        representative_article_id VARCHAR(36),
                        rank_score NUMERIC,
                        importance_score NUMERIC,
                        freshness_score NUMERIC,
                        article_count INTEGER NOT NULL,
                        source_count INTEGER NOT NULL,
                        first_seen_at TIMESTAMP NOT NULL,
                        last_aggregated_at TIMESTAMP NOT NULL,
                        newest_published_at TIMESTAMP,
                        metadata JSON NOT NULL,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE story_article (
                        story_key VARCHAR(36) NOT NULL,
                        article_id VARCHAR(36) NOT NULL,
                        member_score NUMERIC,
                        sort_order INTEGER NOT NULL,
                        is_representative BOOLEAN NOT NULL,
                        created_at TIMESTAMP NOT NULL,
                        PRIMARY KEY (story_key, article_id)
                    )
                    """
                )
            )

        ensure_article_storage_schema(engine)

        inspector = inspect(engine)
        story_columns = {column["name"] for column in inspector.get_columns("story")}
        self.assertTrue(
            {
                "story_key",
                "created_run_id",
                "title_zh",
                "summary_zh",
                "key_points_json",
                "tags_json",
                "category",
                "hero_image_url",
                "source_article_count",
                "created_at",
            }.issubset(story_columns)
        )
        self.assertFalse({"title", "topic_tags", "article_count", "metadata"} & story_columns)

        story_article_columns = {column["name"] for column in inspector.get_columns("story_article")}
        self.assertEqual(story_article_columns, {"story_key", "article_id", "rank"})


if __name__ == "__main__":
    unittest.main()
