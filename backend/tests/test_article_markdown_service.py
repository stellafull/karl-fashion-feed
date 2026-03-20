from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.app.service.article_contracts import MarkdownBlock
from backend.app.service.article_parse_service import ArticleMarkdownService


class ArticleMarkdownServiceTest(unittest.TestCase):
    def test_write_pure_text_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ArticleMarkdownService(Path(tmp_dir))
            rel_path = service.build_relative_path(
                article_id="article-1",
                reference_time=datetime(2026, 3, 13, 8, 0, 0),
            )
            markdown = service.render_canonical_markdown(
                title="Runway Story",
                summary="Front row summary",
                blocks=(
                    MarkdownBlock(kind="heading", text="Look One"),
                    MarkdownBlock(kind="paragraph", text="Paragraph body."),
                ),
            )
            service.write_markdown(relative_path=rel_path, content=markdown)

            self.assertEqual(rel_path, "2026-03-13/article-1.md")
            self.assertIn("# Runway Story", markdown)
            self.assertIn("## Look One", markdown)
            self.assertIn("Paragraph body.", markdown)
            self.assertNotIn("[image:", markdown)


if __name__ == "__main__":
    unittest.main()
