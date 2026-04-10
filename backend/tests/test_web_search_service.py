from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.app.service.RAG.web_search_service import WebSearchService


class WebSearchServiceTest(unittest.TestCase):
    def test_parse_brave_image_results_normalizes_visual_contract(self) -> None:
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}, clear=True):
            service = WebSearchService()

        payload = {
            "results": [
                {
                    "title": "Green rectangular sunglasses",
                    "url": "https://example.com/look-1",
                    "source": "example.com",
                    "thumbnail": {"src": "https://example.com/thumb.jpg"},
                    "properties": {"url": "https://example.com/image.jpg"},
                }
            ]
        }

        results = service._parse_brave_image_results(
            payload=payload,
            query="green rectangular sunglasses fashion",
            limit=5,
        )

        self.assertEqual(1, len(results))
        self.assertEqual("brave_image", results[0].provider)
        self.assertEqual("https://example.com/look-1", results[0].source_page_url)
        self.assertEqual("https://example.com/image.jpg", results[0].image_url)
        self.assertEqual("https://example.com/thumb.jpg", results[0].thumbnail_url)

    def test_parse_brave_llm_context_results_accepts_sources_list(self) -> None:
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}, clear=True):
            service = WebSearchService()

        payload = {
            "sources": [
                {
                    "title": "Look breakdown",
                    "url": "https://example.com/look-1",
                    "description": "A bold acetate sunglasses look.",
                    "content": "This page describes bold acetate rectangular sunglasses.",
                }
            ]
        }

        results = service._parse_brave_llm_context_results(
            payload=payload,
            query="green rectangular sunglasses fashion",
            limit=5,
        )

        self.assertEqual(1, len(results))
        self.assertEqual("brave_llm_context", results[0].provider)
        self.assertEqual("https://example.com/look-1", results[0].url)
        self.assertIn("rectangular sunglasses", results[0].content)


if __name__ == "__main__":
    unittest.main()
