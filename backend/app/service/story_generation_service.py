"""Generate immutable story drafts from clustered articles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json

from openai import AsyncOpenAI

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.prompts.story_generation_prompt import build_story_generation_prompt
from backend.app.schemas.llm.story_generation import StoryGenerationSchema
from backend.app.schemas.llm.story_taxonomy import StoryCategory
from backend.app.service.article_cluster_service import EmbeddedArticle


@dataclass(frozen=True)
class StoryDraft:
    title_zh: str
    summary_zh: str
    key_points: tuple[str, ...]
    tags: tuple[str, ...]
    category: StoryCategory
    article_ids: tuple[str, ...]
    hero_image_url: str | None
    source_article_count: int


@dataclass(frozen=True)
class CategoryScopedCluster:
    category: StoryCategory
    articles: tuple[EmbeddedArticle, ...]


class StoryGenerationService:
    def __init__(self) -> None:
        api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
        if not api_key:
            raise ValueError(f"missing API key for {STORY_SUMMARIZATION_MODEL_CONFIG.model_name}")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
            timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
        )

    async def generate_story(
        self,
        cluster: CategoryScopedCluster,
        *,
        cluster_index: int | None = None,
    ) -> StoryDraft:
        payload = [
            {
                "article_id": item.article.article_id,
                "title_zh": item.article.title_zh,
                "summary_zh": item.article.summary_zh,
                "tags": item.article.tags,
                "brands": item.article.brands,
                "categories": item.article.categories,
                "source_name": item.article.source_name,
            }
            for item in cluster.articles
        ]
        try:
            response = await self._client.beta.chat.completions.parse(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=STORY_SUMMARIZATION_MODEL_CONFIG.temperature,
                response_format=StoryGenerationSchema,
                messages=[
                    {
                        "role": "system",
                        "content": build_story_generation_prompt(
                            category=cluster.category
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "category": cluster.category,
                                "articles": payload,
                            },
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        ),
                    },
                ],
            )
        except Exception as exc:
            exc.add_note(
                "stage=story_generation_request "
                f"category={cluster.category} "
                f"cluster_index={cluster_index} "
                f"cluster_size={len(cluster.articles)} "
                f"article_ids={[item.article.article_id for item in cluster.articles]}"
            )
            raise
        result = response.choices[0].message.parsed
        if result is None:
            raise ValueError("story generation response missing parsed payload")
        lead = cluster.articles[0].article
        return StoryDraft(
            title_zh=result.title_zh.strip(),
            summary_zh=result.summary_zh.strip(),
            key_points=tuple(point.strip() for point in result.key_points if point.strip()),
            tags=tuple(tag.strip() for tag in result.tags if tag.strip()),
            category=cluster.category,
            article_ids=tuple(item.article.article_id for item in cluster.articles),
            hero_image_url=lead.hero_image_url,
            source_article_count=len(cluster.articles),
        )

    async def generate_stories(
        self,
        clusters: list[CategoryScopedCluster],
    ) -> list[StoryDraft]:
        if not clusters:
            return []
        results = await asyncio.gather(
            *(
                self.generate_story(cluster, cluster_index=index)
                for index, cluster in enumerate(clusters)
            ),
            return_exceptions=False,
        )
        return list(results)
