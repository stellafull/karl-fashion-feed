"""Focused metadata tests for retrieval-core image hits."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from backend.app.models import Article, ArticleImage
from backend.app.schemas.rag_query import CitationLocator, GroundingText
from backend.app.service.RAG.query_service import QueryService


class QueryServiceMetadataTests(unittest.TestCase):
    """Ensure live image hits stay source-grounded and avoid fabricated metadata."""

    def test_build_image_hits_omits_visual_only_and_fabricated_chinese_metadata(self) -> None:
        article_id = str(uuid4())
        image_id = str(uuid4())
        article = Article(
            article_id=article_id,
            source_name="Vogue Runway",
            source_type="rss",
            source_lang="en",
            category="fashion",
            canonical_url=f"https://example.com/{article_id}",
            original_url=f"https://example.com/original/{article_id}",
            title_raw="Raw title",
            summary_raw="Raw summary",
            metadata_json={},
        )
        image = ArticleImage(
            image_id=image_id,
            article_id=article_id,
            source_url="https://example.com/image.jpg",
            normalized_url="https://example.com/image.jpg",
            caption_raw="Look 1 backstage",
            alt_text="Model detail",
            credit_raw="Photo: Karl",
            context_snippet="Backstage fitting notes",
            ocr_text="LOOK 1",
            observed_description="A model standing backstage.",
            contextual_interpretation="Backstage mood before the show.",
        )
        grounding_text = GroundingText(
            chunk_index=0,
            content="Grounding copy",
            citation_locator=CitationLocator(
                article_id=article_id,
                chunk_index=0,
                source_name=article.source_name,
                canonical_url=article.canonical_url,
            ),
        )
        point = SimpleNamespace(
            payload={
                "retrieval_unit_id": f"image:{image_id}",
                "article_id": article_id,
                "article_image_id": image_id,
            },
            score=0.91,
        )
        service = QueryService.__new__(QueryService)
        service._load_articles = lambda article_ids: {article_id: article}
        service._load_images = lambda image_ids: {image_id: image}
        service._load_grounding_texts = lambda loaded_article: [grounding_text]

        [hit] = service._build_image_hits([(point, 0.91)])

        self.assertEqual(hit.modality, "image")
        self.assertEqual(hit.source_url, "https://example.com/image.jpg")
        self.assertEqual(hit.caption_raw, "Look 1 backstage")
        self.assertEqual(hit.alt_text, "Model detail")
        self.assertEqual(hit.credit_raw, "Photo: Karl")
        self.assertEqual(hit.context_snippet, "Backstage fitting notes")
        self.assertIsNone(hit.ocr_text)
        self.assertIsNone(hit.observed_description)
        self.assertIsNone(hit.contextual_interpretation)
        self.assertIsNone(hit.title_zh)
        self.assertIsNone(hit.summary_zh)
        self.assertEqual(hit.grounding_texts, [grounding_text])
