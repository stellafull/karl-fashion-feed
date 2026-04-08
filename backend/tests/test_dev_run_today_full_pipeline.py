from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Digest, PipelineRun, Story, ensure_article_storage_schema
from backend.app.models.event_frame import ArticleEventFrame
from backend.app.scripts import dev_run_today_full_pipeline as script_module


class DevRunTodayFullPipelineScriptTest(unittest.TestCase):
    def test_parser_accepts_expected_flags(self) -> None:
        parser = script_module._build_parser()

        args = parser.parse_args(
            [
                "--source-name",
                "Vogue",
                "--source-name",
                "WWD",
                "--limit-sources",
                "2",
                "--output-dir",
                "/tmp/full-run",
                "--llm-artifact-dir",
                "/tmp/llm-artifacts",
            ]
        )

        self.assertEqual(["Vogue", "WWD"], args.source_names)
        self.assertEqual(2, args.limit_sources)
        self.assertEqual(Path("/tmp/full-run"), args.output_dir)
        self.assertEqual(Path("/tmp/llm-artifacts"), args.llm_artifact_dir)

    def test_filter_articles_by_published_today_uses_shanghai_bounds(self) -> None:
        articles = [
            {"article_id": "a-null", "published_at": None},
            {"article_id": "a-before", "published_at": datetime(2026, 4, 3, 15, 59, 59)},
            {"article_id": "a-start", "published_at": datetime(2026, 4, 3, 16, 0, 0)},
            {"article_id": "a-middle", "published_at": datetime(2026, 4, 4, 3, 0, 0)},
            {"article_id": "a-end", "published_at": datetime(2026, 4, 4, 16, 0, 0)},
        ]

        filtered = script_module.filter_articles_by_published_today(
            articles,
            business_day=date(2026, 4, 4),
        )

        self.assertEqual(
            ["a-start", "a-middle"],
            [row["article_id"] for row in filtered],
        )

    def test_assert_clean_business_day_raises_when_today_rows_exist(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ensure_article_storage_schema(engine)
        session_factory = sessionmaker(bind=engine, future=True)
        business_day = date(2026, 4, 4)

        with session_factory() as session:
            session.add(
                PipelineRun(
                    run_id="run-1",
                    business_date=business_day,
                    run_type="dev_today_full_pipeline",
                )
            )
            session.add(
                ArticleEventFrame(
                    event_frame_id="frame-1",
                    article_id="article-1",
                    business_date=business_day,
                    event_type="runway_show",
                    subject_json={},
                    action_text="发布",
                    object_text="系列",
                    place_text="Paris",
                    collection_text="FW26",
                    season_text="FW26",
                    show_context_text="",
                    evidence_json=[],
                    signature_json={},
                    extraction_confidence=0.9,
                    extraction_status="done",
                    extraction_error=None,
                )
            )
            session.add(
                Story(
                    story_key="story-1",
                    business_date=business_day,
                    event_type="runway_show",
                    synopsis_zh="今日故事",
                    anchor_json={},
                    article_membership_json=[],
                    created_run_id="run-1",
                )
            )
            session.add(
                Digest(
                    digest_key="digest-1",
                    business_date=business_day,
                    facet="runway_series",
                    title_zh="今日摘要",
                    dek_zh="今日导语",
                    body_markdown="正文",
                    source_article_count=0,
                    source_names_json=[],
                    created_run_id="run-1",
                )
            )
            session.commit()

            with self.assertRaisesRegex(RuntimeError, "frame_count_today=1"):
                script_module.assert_clean_business_day(session, business_day)

    def test_write_review_bundle_writes_summary_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            review_dir = script_module.write_review_bundle(
                summary={
                    "business_day": "2026-04-04",
                    "collection": {"inserted": 3},
                    "eligible_article_ids": ["a-1", "a-2"],
                    "rag": {"upserted_units": 8},
                },
                digests=[
                    {
                        "digest_key": "digest-1",
                        "facet": "runway_series",
                    }
                ],
                articles=[
                    {
                        "article_id": "a-1",
                        "published_at": datetime(2026, 4, 4, 1, 0, 0),
                    }
                ],
                output_dir=Path(tmp_dir),
            )

            self.assertTrue((review_dir / "summary.json").exists())
            self.assertTrue((review_dir / "summary.md").exists())
            self.assertTrue((review_dir / "digests.json").exists())
            self.assertTrue((review_dir / "articles.json").exists())
            summary_json = (review_dir / "summary.json").read_text(encoding="utf-8")
            self.assertIn('"business_day": "2026-04-04"', summary_json)
            self.assertIn('"upserted_units": 8', summary_json)

    def test_main_exports_llm_artifact_dir(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                script_module,
                "run_full_pipeline",
                return_value=Path("/tmp/full-pipeline-review"),
            ) as run_full_pipeline,
        ):
            rc = script_module.main(
                [
                    "--llm-artifact-dir",
                    "/tmp/llm-artifacts",
                ]
            )

        self.assertEqual(0, rc)
        self.assertEqual("/tmp/llm-artifacts", os.environ["KARL_LLM_DEBUG_ARTIFACT_DIR"])
        run_full_pipeline.assert_called_once()

