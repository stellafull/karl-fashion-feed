from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Article
from backend.app.service.story_pipeline_contracts import EmbeddedArticle, StoryDraft
from backend.app.service.story_workflow_service import StoryWorkflowService


class StubClusterService:
    async def cluster_articles(self, articles: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
        return [articles]


class StubStoryGenerationService:
    async def generate_stories(self, clusters: list[list[EmbeddedArticle]]) -> list[StoryDraft]:
        return [
            StoryDraft(
                title_zh="聚合话题",
                summary_zh="摘要",
                key_points=("要点",),
                tags=("时尚",),
                category="高端时装",
                article_ids=tuple(item.article.article_id for item in cluster),
                hero_image_url=None,
                source_article_count=len(cluster),
            )
            for cluster in clusters
        ]


class StoryWorkflowServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def test_build_story_drafts_uses_article_summary_embedding(self) -> None:
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id="article-1",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/1",
                    original_url="https://example.com/1",
                    title_raw="raw title",
                    summary_raw="raw summary",
                    title_zh="标题",
                    summary_zh="摘要",
                    tags_json=["时尚"],
                    brands_json=["Karl"],
                    category_candidates_json=["高端时装"],
                    cluster_text="聚类文本",
                    should_publish=True,
                    enrichment_status="done",
                    parse_status="done",
                    ingested_at=datetime(2026, 3, 18, 8, 0, 0),
                )
            )
            session.commit()

        service = StoryWorkflowService(
            session_factory=self.session_factory,
            cluster_service=StubClusterService(),
            story_generation_service=StubStoryGenerationService(),
        )

        with patch(
            "backend.app.service.story_workflow_service.generate_article_summary_embedding",
            return_value=[0.1, 0.2, 0.3],
        ) as embedding_mock:
            result = asyncio.run(service.build_story_drafts(["article-1"]))

        embedding_mock.assert_called_once_with("聚类文本")
        self.assertEqual(result.stages_completed, ("story_embedding", "semantic_cluster", "cluster_review", "story_generation"))
        self.assertEqual(result.stages_skipped, ())
        self.assertEqual(len(result.story_drafts), 1)
        self.assertEqual(result.story_drafts[0].article_ids, ("article-1",))


if __name__ == "__main__":
    unittest.main()
