from __future__ import annotations

import unittest
from datetime import datetime

from backend.app.schemas.llm.story_generation import StoryGenerationSchema
from backend.app.service.story_generation_service import StoryGenerationService
from backend.app.service.story_pipeline_contracts import EmbeddedArticle, EnrichedArticleRecord


class StubStoryBatchResult:
    def __init__(self, *, value: StoryGenerationSchema | None = None, error: str | None = None) -> None:
        self.value = value
        self.error = error


class StubStoryClient:
    def complete_json_batch(self, **_: object):
        return {
            "story:0": StubStoryBatchResult(
                value=StoryGenerationSchema(
                    title_zh="批量成功",
                    summary_zh="第一组使用批量结果。",
                    key_points=["point-1"],
                    tags=["时尚"],
                    category="高端时装",
                )
            ),
            "story:1": StubStoryBatchResult(error="missing result"),
        }

    def complete_json(self, **_: object) -> StoryGenerationSchema:
        return StoryGenerationSchema(
            title_zh="单条回退",
            summary_zh="第二组走单条回退。",
            key_points=["point-2"],
            tags=["零售"],
            category="行业动态",
        )


def build_cluster(article_id: str) -> list[EmbeddedArticle]:
    published_at = datetime(2026, 3, 13, 8, 0, 0)
    return [
        EmbeddedArticle(
            article=EnrichedArticleRecord(
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
    def test_generate_stories_batch_falls_back_per_failed_cluster(self) -> None:
        service = StoryGenerationService(llm_client=StubStoryClient())

        drafts = service.generate_stories_batch([build_cluster("a-1"), build_cluster("a-2")])

        self.assertEqual([draft.title_zh for draft in drafts], ["批量成功", "单条回退"])
        self.assertEqual([draft.category for draft in drafts], ["高端时装", "行业动态"])


if __name__ == "__main__":
    unittest.main()
