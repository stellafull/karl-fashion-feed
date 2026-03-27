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
            "markdown_rel_path",
            "parse_status",
            "parse_attempts",
            "parse_error",
            "parse_updated_at",
            "event_frame_status",
            "event_frame_attempts",
            "event_frame_error",
            "event_frame_updated_at",
        }
        self.assertTrue(expected.issubset(Article.__table__.columns.keys()))
        self.assertNotIn("normalization_status", Article.__table__.columns.keys())
        self.assertNotIn("title_zh", Article.__table__.columns.keys())
        self.assertNotIn("summary_zh", Article.__table__.columns.keys())
        self.assertNotIn("body_zh_rel_path", Article.__table__.columns.keys())

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

    def test_legacy_story_tables_require_runtime_reset_instead_of_silent_drop(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE story (
                        story_key VARCHAR(36) PRIMARY KEY,
                        created_run_id VARCHAR(36) NOT NULL,
                        title_zh TEXT NOT NULL,
                        summary_zh TEXT NOT NULL,
                        key_points_json JSON NOT NULL,
                        tags_json JSON NOT NULL,
                        category VARCHAR(64) NOT NULL,
                        hero_image_url TEXT NULL,
                        source_article_count INTEGER NOT NULL,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )

        with self.assertRaises(RuntimeError) as context:
            ensure_article_storage_schema(engine)

        self.assertIn("story", str(context.exception).lower())
        self.assertIn("reset", str(context.exception).lower())

    def test_legacy_article_normalization_columns_require_runtime_reset(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE article (
                        article_id VARCHAR(36) PRIMARY KEY,
                        source_name VARCHAR(120) NOT NULL,
                        source_type VARCHAR(16) NOT NULL,
                        source_lang VARCHAR(16) NOT NULL,
                        category VARCHAR(64) NOT NULL,
                        canonical_url TEXT NOT NULL,
                        original_url TEXT NOT NULL,
                        title_raw TEXT NOT NULL,
                        summary_raw TEXT NOT NULL,
                        markdown_rel_path TEXT NULL,
                        normalization_status VARCHAR(32) NOT NULL,
                        normalization_attempts INTEGER NOT NULL,
                        normalization_error TEXT NULL,
                        normalization_updated_at TIMESTAMP NOT NULL,
                        title_zh TEXT NULL,
                        summary_zh TEXT NULL,
                        body_zh_rel_path TEXT NULL,
                        published_at TIMESTAMP NULL,
                        discovered_at TIMESTAMP NOT NULL,
                        ingested_at TIMESTAMP NOT NULL,
                        metadata_json JSON NOT NULL
                    )
                    """
                )
            )

        with self.assertRaises(RuntimeError) as context:
            ensure_article_storage_schema(engine)

        message = str(context.exception).lower()
        self.assertIn("article", message)
        self.assertIn("reset", message)
        self.assertIn("normalization", message)

    def test_legacy_article_normalization_columns_fail_before_runtime_tables_are_created(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE article (
                        article_id VARCHAR(36) PRIMARY KEY,
                        source_name VARCHAR(120) NOT NULL,
                        source_type VARCHAR(16) NOT NULL,
                        source_lang VARCHAR(16) NOT NULL,
                        category VARCHAR(64) NOT NULL,
                        canonical_url TEXT NOT NULL,
                        original_url TEXT NOT NULL,
                        title_raw TEXT NOT NULL,
                        summary_raw TEXT NOT NULL,
                        markdown_rel_path TEXT NULL,
                        normalization_status VARCHAR(32) NOT NULL,
                        normalization_attempts INTEGER NOT NULL,
                        normalization_error TEXT NULL,
                        normalization_updated_at TIMESTAMP NOT NULL,
                        title_zh TEXT NULL,
                        summary_zh TEXT NULL,
                        body_zh_rel_path TEXT NULL,
                        published_at TIMESTAMP NULL,
                        discovered_at TIMESTAMP NOT NULL,
                        ingested_at TIMESTAMP NOT NULL,
                        metadata_json JSON NOT NULL
                    )
                    """
                )
            )

        with self.assertRaises(RuntimeError):
            ensure_article_storage_schema(engine)

        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }

        self.assertEqual(tables, {"article"})

    def test_legacy_article_image_table_gets_required_columns_repaired(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE article (
                        article_id VARCHAR(36) PRIMARY KEY,
                        source_name VARCHAR(120) NOT NULL,
                        source_type VARCHAR(16) NOT NULL,
                        source_lang VARCHAR(16) NOT NULL,
                        category VARCHAR(64) NOT NULL,
                        canonical_url TEXT NOT NULL,
                        original_url TEXT NOT NULL,
                        title_raw TEXT NOT NULL,
                        summary_raw TEXT NOT NULL,
                        published_at TIMESTAMP NULL,
                        discovered_at TIMESTAMP NOT NULL,
                        ingested_at TIMESTAMP NOT NULL,
                        metadata_json JSON NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE article_image (
                        image_id VARCHAR(36) PRIMARY KEY,
                        article_id VARCHAR(36) NOT NULL,
                        source_url TEXT NOT NULL,
                        normalized_url TEXT NOT NULL
                    )
                    """
                )
            )

        ensure_article_storage_schema(engine)

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info('article_image')")).fetchall()
            }

        self.assertIn("image_hash", columns)
        self.assertIn("visual_attempts", columns)

    def test_legacy_strict_story_table_gets_frame_membership_column_repaired(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE pipeline_run (
                        run_id VARCHAR(36) PRIMARY KEY,
                        business_date DATE NOT NULL,
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
                    CREATE TABLE strict_story (
                        strict_story_key VARCHAR(36) PRIMARY KEY,
                        business_date DATE NOT NULL,
                        synopsis_zh TEXT NOT NULL,
                        signature_json JSON NOT NULL,
                        created_run_id VARCHAR(36) NOT NULL,
                        packing_status VARCHAR(32) NOT NULL,
                        packing_error TEXT NULL,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )

        ensure_article_storage_schema(engine)

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info('strict_story')")).fetchall()
            }
        self.assertIn("frame_membership_json", columns)

    def test_runtime_shaped_pipeline_run_with_rows_repairs_stage_columns_on_sqlite(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE pipeline_run (
                        run_id VARCHAR(36) PRIMARY KEY,
                        business_date DATE NOT NULL,
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
                        business_date,
                        run_type,
                        status,
                        started_at,
                        finished_at,
                        watermark_ingested_at,
                        error_message,
                        metadata_json
                    ) VALUES (
                        'run-1',
                        '2026-03-27',
                        'digest_daily',
                        'success',
                        '2026-03-27 08:00:00',
                        NULL,
                        NULL,
                        NULL,
                        '{}'
                    )
                    """
                )
            )

        ensure_article_storage_schema(engine)

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info('pipeline_run')")).fetchall()
            }
            row = connection.execute(
                text(
                    """
                    SELECT
                        strict_story_status,
                        strict_story_attempts,
                        strict_story_updated_at,
                        digest_status,
                        digest_attempts,
                        digest_updated_at
                    FROM pipeline_run
                    WHERE run_id = 'run-1'
                    """
                )
            ).one()

        self.assertIn("strict_story_status", columns)
        self.assertIn("strict_story_attempts", columns)
        self.assertIn("strict_story_updated_at", columns)
        self.assertIn("digest_status", columns)
        self.assertIn("digest_attempts", columns)
        self.assertIn("digest_updated_at", columns)
        self.assertEqual(row[0], "pending")
        self.assertEqual(row[1], 0)
        self.assertIsNotNone(row[2])
        self.assertEqual(row[3], "pending")
        self.assertEqual(row[4], 0)
        self.assertIsNotNone(row[5])

    def test_runtime_shaped_strict_story_rebuilds_stale_frame_membership_from_existing_rows(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE pipeline_run (
                        run_id VARCHAR(36) PRIMARY KEY,
                        business_date DATE NOT NULL,
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
                        business_date,
                        run_type,
                        status,
                        started_at,
                        finished_at,
                        watermark_ingested_at,
                        error_message,
                        metadata_json
                    ) VALUES (
                        'run-1',
                        '2026-03-27',
                        'digest_daily',
                        'success',
                        '2026-03-27 08:00:00',
                        NULL,
                        NULL,
                        NULL,
                        '{}'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE strict_story (
                        strict_story_key VARCHAR(36) PRIMARY KEY,
                        business_date DATE NOT NULL,
                        synopsis_zh TEXT NOT NULL,
                        signature_json JSON NOT NULL,
                        frame_membership_json JSON NOT NULL,
                        created_run_id VARCHAR(36) NOT NULL,
                        packing_status VARCHAR(32) NOT NULL,
                        packing_error TEXT NULL,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO strict_story (
                        strict_story_key,
                        business_date,
                        synopsis_zh,
                        signature_json,
                        frame_membership_json,
                        created_run_id,
                        packing_status,
                        packing_error,
                        created_at
                    ) VALUES (
                        'story-1',
                        '2026-03-27',
                        'old story',
                        '{}',
                        '["stale-frame"]',
                        'run-1',
                        'done',
                        NULL,
                        '2026-03-27 08:00:00'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE strict_story_frame (
                        strict_story_key VARCHAR(36) NOT NULL,
                        event_frame_id VARCHAR(36) NOT NULL,
                        rank INTEGER NOT NULL,
                        PRIMARY KEY (strict_story_key, event_frame_id)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO strict_story_frame (strict_story_key, event_frame_id, rank)
                    VALUES
                        ('story-1', 'frame-b', 1),
                        ('story-1', 'frame-a', 0)
                    """
                )
            )

        ensure_article_storage_schema(engine)

        with engine.connect() as connection:
            membership = connection.execute(
                text(
                    """
                    SELECT frame_membership_json
                    FROM strict_story
                    WHERE strict_story_key = 'story-1'
                    """
                )
            ).scalar_one()
        self.assertEqual(membership, '["frame-a", "frame-b"]')
