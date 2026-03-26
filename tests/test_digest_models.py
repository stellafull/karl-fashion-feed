"""Contract tests for the digest runtime ORM bootstrap."""

import importlib
import sys
import unittest

from sqlalchemy import create_engine, text

from backend.app.models.article import ensure_article_storage_schema
from backend.app.models import (
    Article,
    ArticleEventFrame,
    Digest,
    DigestArticle,
    DigestStrictStory,
    PipelineRun,
    SourceRunState,
    StrictStory,
    StrictStoryArticle,
    StrictStoryFrame,
)


class DigestModelContractTest(unittest.TestCase):
    """Verify the digest runtime ORM contract is exported and shaped correctly."""

    def test_article_stage_columns_match_digest_runtime_contract(self) -> None:
        expected = {
            "parse_status",
            "parse_attempts",
            "parse_error",
            "parse_updated_at",
            "normalization_status",
            "normalization_attempts",
            "normalization_error",
            "normalization_updated_at",
            "event_frame_status",
            "event_frame_attempts",
            "event_frame_error",
            "event_frame_updated_at",
            "title_zh",
            "summary_zh",
            "body_zh_rel_path",
        }
        self.assertTrue(expected.issubset(Article.__table__.columns.keys()))

    def test_pipeline_run_owns_explicit_batch_stage_columns(self) -> None:
        expected = {
            "business_date",
            "strict_story_status",
            "strict_story_attempts",
            "strict_story_error",
            "strict_story_updated_at",
            "digest_status",
            "digest_attempts",
            "digest_error",
            "digest_updated_at",
        }
        self.assertTrue(expected.issubset(PipelineRun.__table__.columns.keys()))

    def test_new_digest_runtime_tables_replace_story_read_model(self) -> None:
        self.assertEqual(ArticleEventFrame.__tablename__, "article_event_frame")
        self.assertEqual(StrictStoryFrame.__tablename__, "strict_story_frame")
        self.assertEqual(DigestArticle.__tablename__, "digest_article")
        self.assertEqual(SourceRunState.__tablename__, "source_run_state")

    def test_app_main_imports_without_story_route_wiring(self) -> None:
        sys.modules.pop("backend.app.app_main", None)
        module = importlib.import_module("backend.app.app_main")
        self.assertEqual(module.app.title, "KARL Fashion Feed Backend")

    def test_story_era_modules_are_removed(self) -> None:
        sys.modules.pop("backend.app.models.story", None)
        sys.modules.pop("backend.app.router.story_router", None)
        sys.modules.pop("backend.app.service.scheduler_service", None)

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("backend.app.models.story")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("backend.app.router.story_router")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("backend.app.service.scheduler_service")

    def test_legacy_pipeline_run_requires_runtime_reset_instead_of_not_null_backfill(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE pipeline_run (
                        run_id VARCHAR(36) PRIMARY KEY,
                        run_type VARCHAR(64) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        started_at TIMESTAMP NOT NULL,
                        finished_at TIMESTAMP NULL,
                        watermark_ingested_at TIMESTAMP NULL,
                        error_message TEXT NULL,
                        metadata_json JSON NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO pipeline_run (
                        run_id,
                        run_type,
                        status,
                        started_at,
                        finished_at,
                        watermark_ingested_at,
                        error_message,
                        metadata_json
                    ) VALUES (
                        'run-1',
                        'daily_story',
                        'success',
                        '2026-03-26 08:00:00',
                        NULL,
                        NULL,
                        NULL,
                        '{}'
                    )
                    """
                )
            )

        with self.assertRaises(RuntimeError) as context:
            ensure_article_storage_schema(engine)

        self.assertIn("reset", str(context.exception).lower())
