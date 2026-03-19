from __future__ import annotations

import unittest

from backend.app.service.article_chunk_service import split_markdown_into_text_chunks


class ArticleChunkServiceTest(unittest.TestCase):
    def test_split_markdown_uses_heading_aware_sections(self) -> None:
        markdown = """# Main Title

Intro paragraph.

## First Section
Alpha paragraph.

## Second Section
Beta paragraph.
"""

        chunks = split_markdown_into_text_chunks(
            markdown,
            source_id="article-1",
            chunk_size=200,
            chunk_overlap=20,
        )

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["page_content"], "Intro paragraph.")
        self.assertEqual(chunks[0]["metadata"]["heading_path"], [])
        self.assertEqual(chunks[1]["page_content"], "Alpha paragraph.")
        self.assertEqual(chunks[1]["metadata"]["heading_path"], ["First Section"])
        self.assertEqual(chunks[2]["page_content"], "Beta paragraph.")
        self.assertEqual(chunks[2]["metadata"]["heading_path"], ["Second Section"])
        self.assertEqual(chunks[0]["metadata"]["next_chunk_id"], "article-1:text:1")
        self.assertEqual(chunks[1]["metadata"]["prev_chunk_id"], "article-1:text:0")
        self.assertEqual(chunks[1]["metadata"]["next_chunk_id"], "article-1:text:2")
        self.assertEqual(chunks[2]["metadata"]["prev_chunk_id"], "article-1:text:1")
        self.assertIsNone(chunks[2]["metadata"]["next_chunk_id"])

    def test_split_markdown_uses_multilingual_separators_for_long_sections(self) -> None:
        markdown = """# Main Title

## Japanese Section
「これは最初の文です。」これは二番目の文です。これは三番目の文です。これは四番目の文です。これは五番目の文です。
"""

        chunks = split_markdown_into_text_chunks(
            markdown,
            source_id="article-2",
            chunk_size=25,
            chunk_overlap=0,
        )

        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0]["metadata"]["heading_path"], ["Japanese Section"])
        self.assertEqual(chunks[0]["page_content"], "「これは最初の文です。」")
        self.assertTrue(all(chunk["page_content"].endswith(("。", "。」")) for chunk in chunks))
        self.assertTrue(all(chunk["metadata"]["text_start"] >= 0 for chunk in chunks))
        self.assertTrue(all(chunk["metadata"]["text_end"] > chunk["metadata"]["text_start"] for chunk in chunks))
        self.assertTrue(any("「" in chunk["page_content"] for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
