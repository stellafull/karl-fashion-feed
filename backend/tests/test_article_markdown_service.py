from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.app.models.article import ArticleImage
from backend.app.service.article_contracts import MarkdownBlock
from backend.app.service.article_markdown_service import ArticleMarkdownService


class ArticleMarkdownServiceTest(unittest.TestCase):
    def test_write_and_materialize_markdown(self) -> None:
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
                    MarkdownBlock(kind="image", image_index=0),
                    MarkdownBlock(kind="paragraph", text="Paragraph body."),
                ),
                image_ids_by_index={0: "img-1"},
            )
            service.write_markdown(relative_path=rel_path, content=markdown)

            materialized = service.render_materialized_markdown(
                relative_path=rel_path,
                images=[
                    ArticleImage(
                        image_id="img-1",
                        article_id="article-1",
                        source_url="https://example.com/image.jpg",
                        normalized_url="https://example.com/image.jpg",
                        observed_description="A model in a red coat.",
                        contextual_interpretation="Likely a runway look from the collection.",
                    )
                ],
            )

            self.assertEqual(rel_path, "2026-03-13/article-1.md")
            self.assertIn("[image:img-1]", markdown)
            self.assertIn("A model in a red coat.", materialized)
            self.assertIn("Likely a runway look", materialized)


if __name__ == "__main__":
    unittest.main()
