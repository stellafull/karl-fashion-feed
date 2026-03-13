from __future__ import annotations

import unittest

from backend.app.models.article import Article
from backend.app.scripts.backfill_article_storage import _legacy_blocks


class BackfillArticleStorageTest(unittest.TestCase):
    def test_legacy_blocks_include_hero_placeholder_and_body(self) -> None:
        article = Article(
            article_id="article-1",
            source_name="Legacy",
            source_type="rss",
            source_lang="en",
            category="高端时装",
            canonical_url="https://example.com/story",
            original_url="https://example.com/story",
            title_raw="Legacy Story",
            summary_raw="Legacy summary",
            content_raw="Paragraph one.\n\nParagraph two.",
        )

        blocks = _legacy_blocks(article, "img-1")
        self.assertEqual(blocks[0].kind, "image")
        self.assertEqual(blocks[1].text, "Paragraph one.")
        self.assertEqual(blocks[2].text, "Paragraph two.")


if __name__ == "__main__":
    unittest.main()
