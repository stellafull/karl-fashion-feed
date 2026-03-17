from __future__ import annotations

import unittest
from datetime import datetime

from backend.app.config.embedding_config import (
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
    EmbeddingModelConfig,
)
from backend.app.service.embedding_service import EmbeddingService, StoryEmbeddingService
from backend.app.service.story_pipeline_contracts import EnrichedArticleRecord


class EmbeddingServiceTest(unittest.TestCase):
    def test_story_embedding_service_defaults_to_story_summarization_embedding_config(self) -> None:
        service = StoryEmbeddingService(embed_texts=lambda _: [])

        self.assertEqual(service._model_config, DENSE_SUMMARIZATION_EMBEDDING_CONFIG)

    def test_embedding_service_is_story_embedding_alias(self) -> None:
        service = EmbeddingService(embed_texts=lambda _: [])

        self.assertIsInstance(service, StoryEmbeddingService)
        self.assertEqual(service._model_config, DENSE_SUMMARIZATION_EMBEDDING_CONFIG)

    def test_embed_articles_chunks_requests_by_batch_limit(self) -> None:
        calls: list[list[str]] = []

        def embed_texts(batch: list[str]) -> list[list[float]]:
            calls.append(batch)
            return [[float(index)] for index, _ in enumerate(batch, start=1)]

        service = EmbeddingService(
            model_config=EmbeddingModelConfig(
                model_name="text-embedding",
                vector_dimension=1,
                batch_size=10,
            ),
            embed_texts=embed_texts,
        )
        articles = [
            EnrichedArticleRecord(
                article_id=f"article-{index}",
                title_zh=f"title-{index}",
                summary_zh=f"summary-{index}",
                tags=("tag",),
                brands=(),
                category_candidates=("高端时装",),
                cluster_text=f"text-{index}",
                published_at=datetime(2026, 3, 13, 8, 0, 0),
                ingested_at=datetime(2026, 3, 13, 8, 0, 0),
                hero_image_url=None,
                source_name="Vogue",
            )
            for index in range(23)
        ]

        embedded = service.embed_articles(articles)

        self.assertEqual(len(calls), 3)
        self.assertEqual([len(batch) for batch in calls], [10, 10, 3])
        self.assertEqual(len(embedded), 23)


if __name__ == "__main__":
    unittest.main()
