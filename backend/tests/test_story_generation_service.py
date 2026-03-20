from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from backend.app.schemas.llm.story_generation import StoryGenerationSchema
from backend.app.service.article_cluster_service import EmbeddedArticle
from backend.app.service.article_enrichment_service import EnrichedArticle
from backend.app.service.story_generation_service import StoryGenerationService


class StubStoryClient:
    def __init__(self) -> None:
        self.calls = 0
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
        self.calls += 1
        if self.calls == 1:
            parsed = StoryGenerationSchema(
                title_zh="第一组",
                summary_zh="第一组结果",
                key_points=["point-1"],
                tags=["时尚"],
                category="高端时装",
            )
        else:
            parsed = StoryGenerationSchema(
                title_zh="第二组",
                summary_zh="第二组结果",
                key_points=["point-2"],
                tags=["零售"],
                category="行业动态",
            )
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
                                {"parsed": parsed},
                            )()
                        },
                    )()
                ]
            },
        )()


def build_cluster(article_id: str) -> list[EmbeddedArticle]:
    published_at = datetime(2026, 3, 13, 8, 0, 0)
    return [
        EmbeddedArticle(
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
            embedding=(1.0, 0.0),
        )
    ]


class StoryGenerationServiceTest(unittest.TestCase):
    def test_generate_stories_returns_drafts(self) -> None:
        service = StoryGenerationService(client=StubStoryClient())

        drafts = asyncio.run(service.generate_stories([build_cluster("a-1"), build_cluster("a-2")]))

        self.assertEqual([draft.title_zh for draft in drafts], ["第一组", "第二组"])
        self.assertEqual([draft.category for draft in drafts], ["高端时装", "行业动态"])


if __name__ == "__main__":
    unittest.main()
