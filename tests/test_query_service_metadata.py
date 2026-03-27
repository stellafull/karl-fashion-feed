"""Focused metadata tests for retrieval-core retrieval contracts."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from backend.app.models import Article, ArticleImage
from backend.app.schemas.rag_query import (
    ArticlePackage,
    CitationLocator,
    GroundingText,
    RetrievalHit,
)
from backend.app.service.RAG.query_service import QueryService


class QueryServiceMetadataTests(unittest.TestCase):
    """Ensure live retrieval DTOs stay source-grounded and neutral."""

    def test_build_image_hits_expose_only_source_text_image_metadata(self) -> None:
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
        self.assertEqual(hit.title, "Raw title")
        self.assertEqual(hit.summary, "Raw summary")
        self.assertEqual(hit.grounding_texts, [grounding_text])

        serialized_hit = hit.model_dump()
        self.assertNotIn("ocr_text", serialized_hit)
        self.assertNotIn("observed_description", serialized_hit)
        self.assertNotIn("contextual_interpretation", serialized_hit)
        self.assertNotIn("title_zh", serialized_hit)
        self.assertNotIn("summary_zh", serialized_hit)

    def test_build_packages_use_neutral_article_metadata_fields(self) -> None:
        article_id = str(uuid4())
        locator = CitationLocator(
            article_id=article_id,
            source_name="Vogue Runway",
            canonical_url=f"https://example.com/{article_id}",
        )
        text_hit = RetrievalHit(
            retrieval_unit_id=f"text:{article_id}:0",
            modality="text",
            article_id=article_id,
            content="Text evidence",
            score=0.71,
            citation_locator=locator,
            title="Raw title",
            summary="Raw summary",
        )
        image_hit = RetrievalHit(
            retrieval_unit_id=f"image:{article_id}",
            modality="image",
            article_id=article_id,
            article_image_id=str(uuid4()),
            content="Image evidence",
            score=0.93,
            citation_locator=locator,
            title="Raw title",
            summary="Raw summary",
        )

        service = QueryService.__new__(QueryService)
        [package] = service._build_packages(
            text_results=[text_hit],
            image_results=[image_hit],
        )

        self.assertIsInstance(package, ArticlePackage)
        self.assertEqual(package.article_id, article_id)
        self.assertEqual(package.title, "Raw title")
        self.assertEqual(package.summary, "Raw summary")
        self.assertEqual(package.combined_score, 0.93)

        serialized_package = package.model_dump()
        self.assertNotIn("title_zh", serialized_package)
        self.assertNotIn("summary_zh", serialized_package)
