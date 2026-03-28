"""Tests for the same-day digest review bundle dev script."""

from __future__ import annotations

import importlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch


class TodayDigestPipelineScriptTest(unittest.TestCase):
    """Verify the dev digest pipeline script emits a review bundle."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.output_dir = Path(self._temp_dir.name)

    def run_script(self, *args: str) -> tuple[int, str]:
        """Execute the script entrypoint and capture stdout for assertions."""
        module = importlib.import_module("backend.app.scripts.dev_run_today_digest_pipeline")
        argv = [*args, "--output-dir", str(self.output_dir)]
        stdout = io.StringIO()
        fake_now = datetime(2026, 3, 28, 1, 0, tzinfo=UTC)
        fake_digests = [{"digest_key": "digest-1", "title_zh": "测试摘要"}]
        fake_articles = [{"article_id": "article-1", "title_raw": "Test article"}]

        with patch.object(module, "datetime") as datetime_mock:
            datetime_mock.now.return_value = fake_now
            with patch.object(module, "business_day_for_runtime", return_value=date(2026, 3, 28)):
                with patch.object(module, "DailyRunCoordinatorService") as coordinator_mock:
                    coordinator_mock.return_value.tick.return_value = "run-1"
                    with patch.object(module, "load_day_digests", return_value=fake_digests):
                        with patch.object(module, "load_day_articles", return_value=fake_articles):
                            with redirect_stdout(stdout):
                                exit_code = module.main(argv)
        return exit_code, stdout.getvalue()

    def test_dev_run_today_digest_pipeline_outputs_review_bundle(self) -> None:
        exit_code, stdout = self.run_script("--skip-collect")

        self.assertEqual(exit_code, 0)
        self.assertIn("review bundle:", stdout)
        self.assertTrue((self.output_dir / "digests.json").exists())
        self.assertTrue((self.output_dir / "articles.json").exists())
        self.assertTrue((self.output_dir / "summary.md").exists())


if __name__ == "__main__":
    unittest.main()
