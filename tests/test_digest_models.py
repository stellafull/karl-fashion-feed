"""Contract tests for the digest runtime ORM bootstrap."""

import unittest

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
