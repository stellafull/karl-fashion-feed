from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect

from backend.app.models.article import ensure_article_storage_schema


class StorySchemaBootstrapTest(unittest.TestCase):
    def test_ensure_article_storage_schema_creates_story_tables(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ensure_article_storage_schema(engine)
        table_names = set(inspect(engine).get_table_names())
        self.assertIn("story", table_names)
        self.assertIn("story_frame", table_names)
        self.assertIn("story_article", table_names)
        self.assertIn("story_facet", table_names)
        self.assertIn("digest_story", table_names)
        self.assertNotIn("strict_story", table_names)

    def test_pipeline_run_uses_story_stage_columns(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ensure_article_storage_schema(engine)
        columns = {column["name"] for column in inspect(engine).get_columns("pipeline_run")}
        self.assertIn("story_status", columns)
        self.assertIn("story_attempts", columns)
        self.assertIn("story_error", columns)
        self.assertIn("story_updated_at", columns)
        self.assertIn("story_token", columns)
        self.assertNotIn("strict_story_status", columns)
