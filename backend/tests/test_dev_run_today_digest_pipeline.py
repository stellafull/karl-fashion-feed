from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.scripts import dev_run_today_digest_pipeline as script_module


class _FakeCoordinator:
    def __init__(self, *, source_names: list[str] | None, limit_sources: int | None) -> None:
        self.source_names = source_names
        self.limit_sources = limit_sources

    def tick(self, *, now: datetime) -> str:
        _ = now
        return "run-dev"

    def drain_until_idle(
        self,
        *,
        run_id: str,
        business_day: date,
        skip_collect: bool = False,
        max_ticks: int = 120,
    ) -> None:
        _ = (run_id, business_day, skip_collect, max_ticks)


class DevRunTodayDigestPipelineScriptTest(unittest.TestCase):
    def test_parser_accepts_published_today_only_and_llm_artifact_dir(self) -> None:
        parser = script_module._build_parser()
        args = parser.parse_args(["--published-today-only", "--llm-artifact-dir", "/tmp/llm-artifacts"])

        self.assertTrue(args.published_today_only)
        self.assertEqual(Path("/tmp/llm-artifacts"), args.llm_artifact_dir)

    def test_filter_dev_articles_by_published_today_excludes_null_published_at(self) -> None:
        articles = [
            {"article_id": "a-null", "published_at": None},
            {"article_id": "a-prev", "published_at": datetime(2026, 3, 29, 23, 59, 59)},
            {"article_id": "a-match", "published_at": datetime(2026, 3, 30, 8, 0, 0)},
        ]

        filtered = script_module.filter_dev_articles_by_published_today(
            articles,
            business_day=date(2026, 3, 30),
        )

        self.assertEqual(["a-match"], [row["article_id"] for row in filtered])

    def test_main_exports_llm_artifact_dir_for_dev_run(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(script_module, "DailyRunCoordinatorService", _FakeCoordinator),
            patch.object(script_module, "business_day_for_runtime", return_value=date(2026, 3, 30)),
            patch.object(script_module, "load_day_digests", return_value=[]),
            patch.object(script_module, "load_day_articles", return_value=[]),
            patch.object(
                script_module,
                "write_review_bundle",
                return_value=Path("/tmp/review-bundle"),
            ),
        ):
            rc = script_module.main(
                [
                    "--skip-collect",
                    "--llm-artifact-dir",
                    "/tmp/llm-artifacts",
                ]
            )
            self.assertEqual("/tmp/llm-artifacts", os.environ["KARL_LLM_DEBUG_ARTIFACT_DIR"])

        self.assertEqual(0, rc)
