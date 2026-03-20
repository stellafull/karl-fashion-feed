from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from backend.app.schemas.llm.story_cluster_review import (
    StoryClusterGroupSchema,
    StoryClusterReviewSchema,
)
from backend.app.service.article_cluster_service import ArticleClusterService, EmbeddedArticle
from backend.app.service.article_enrichment_service import EnrichedArticle


class StubReviewClient:
    def __init__(self, result: StoryClusterReviewSchema | Exception) -> None:
        self._result = result
        self.beta = type(
            "BetaAPI",
            (),
            {
                "chat": type(
                    "ChatAPI",
                    (),
                    {
                        "completions": type(
                            "CompletionsAPI",
                            (),
                            {"parse": self.parse},
                        )()
                    },
                )()
            },
        )()

    async def parse(self, **_: object):
        if isinstance(self._result, Exception):
            raise self._result
        return type(
            "ParsedResponse",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": type(
                                "Message",
                                (),
                                {"parsed": self._result},
                            )()
                        },
                    )()
                ]
            },
        )()


def build_embedded(article_id: str, vector: tuple[float, ...], *, published_at: datetime) -> EmbeddedArticle:
    return EmbeddedArticle(
        article=EnrichedArticle(
            article_id=article_id,
            title_zh=f"title-{article_id}",
            summary_zh=f"summary-{article_id}",
            tags=("tag",),
            brands=("brand",),
            category_candidates=("高端时装",),
            cluster_text=f"cluster-{article_id}",
            published_at=published_at,
            ingested_at=published_at,
            hero_image_url=None,
            source_name="Vogue",
        ),
        embedding=vector,
    )


class ArticleClusterServiceTest(unittest.TestCase):
    def test_cluster_articles_groups_similar_vectors(self) -> None:
        service = ArticleClusterService(
            client=StubReviewClient(
                StoryClusterReviewSchema(
                    groups=[
                        StoryClusterGroupSchema(article_ids=["a-1", "a-2"]),
                    ]
                )
            )
        )
        articles = [
            build_embedded("a-1", (1.0, 0.0), published_at=datetime(2026, 3, 13, 8, 0, 0)),
            build_embedded("a-2", (0.98, 0.02), published_at=datetime(2026, 3, 13, 7, 0, 0)),
            build_embedded("a-3", (-1.0, 0.0), published_at=datetime(2026, 3, 12, 8, 0, 0)),
        ]

        clusters = asyncio.run(service.cluster_articles(articles))

        self.assertEqual(len(clusters), 2)
        self.assertEqual([item.article.article_id for item in clusters[0]], ["a-1", "a-2"])
        self.assertEqual([item.article.article_id for item in clusters[1]], ["a-3"])

    def test_review_can_split_initial_cluster(self) -> None:
        service = ArticleClusterService(
            client=StubReviewClient(
                StoryClusterReviewSchema(
                    groups=[
                        StoryClusterGroupSchema(article_ids=["a-1"]),
                        StoryClusterGroupSchema(article_ids=["a-2"]),
                    ]
                )
            ),
            distance_threshold=0.5,
        )
        articles = [
            build_embedded("a-1", (1.0, 0.0), published_at=datetime(2026, 3, 13, 8, 0, 0)),
            build_embedded("a-2", (0.99, 0.01), published_at=datetime(2026, 3, 13, 7, 0, 0)),
        ]

        clusters = asyncio.run(service.cluster_articles(articles))

        self.assertEqual(len(clusters), 2)
        self.assertEqual([item.article.article_id for item in clusters[0]], ["a-1"])
        self.assertEqual([item.article.article_id for item in clusters[1]], ["a-2"])

    def test_review_raises_when_result_ids_do_not_match_cluster(self) -> None:
        service = ArticleClusterService(
            client=StubReviewClient(
                StoryClusterReviewSchema(
                    groups=[
                        StoryClusterGroupSchema(article_ids=["a-1", "missing"]),
                    ]
                )
            )
        )
        articles = [
            build_embedded("a-1", (1.0, 0.0), published_at=datetime(2026, 3, 13, 8, 0, 0)),
            build_embedded("a-2", (0.98, 0.02), published_at=datetime(2026, 3, 13, 7, 0, 0)),
        ]

        with self.assertRaisesRegex(ValueError, "mismatched article ids"):
            asyncio.run(service.cluster_articles(articles))


if __name__ == "__main__":
    unittest.main()
