"""Generate immutable story drafts from clustered articles."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from typing import Any

from openai import AsyncOpenAI
from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.prompts.story_generation_prompt import STORY_GENERATION_PROMPT
from backend.app.schemas.llm.story_generation import StoryGenerationSchema
from backend.app.service.article_cluster_service import EmbeddedArticle


@dataclass(frozen=True)
class StoryDraft:
    title_zh: str
    summary_zh: str
    key_points: tuple[str, ...]
    tags: tuple[str, ...]
    category: str
    article_ids: tuple[str, ...]
    hero_image_url: str | None
    source_article_count: int


@dataclass(frozen=True)
class StoryGenerationArticleInput:
    article_id: str
    title_zh: str
    summary_zh: str
    tags: tuple[str, ...]
    brands: tuple[str, ...]
    category_candidates: tuple[str, ...]
    source_name: str


class StoryGenerationService:
    def __init__(
        self,
        *,
        client: Any | None = None,
    ) -> None:
        api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
        if client is None and not api_key:
            raise ValueError(f"missing API key for {STORY_SUMMARIZATION_MODEL_CONFIG.model_name}")
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
            timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
        )

    async def generate_story(self, cluster: list[EmbeddedArticle]) -> StoryDraft:
        payload = [
            StoryGenerationArticleInput(
                article_id=item.article.article_id,
                title_zh=item.article.title_zh,
                summary_zh=item.article.summary_zh,
                tags=item.article.tags,
                brands=item.article.brands,
                category_candidates=item.article.category_candidates,
                source_name=item.article.source_name,
            )
            for item in cluster
        ]
        response = await self._client.beta.chat.completions.parse(
            model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
            temperature=STORY_SUMMARIZATION_MODEL_CONFIG.temperature,
            response_format=StoryGenerationSchema,
            messages=[
                {"role": "system", "content": STORY_GENERATION_PROMPT},
                {"role": "user", "content": _render_json_payload([asdict(item) for item in payload])},
            ],
        )
        result = response.choices[0].message.parsed
        if result is None:
            raise ValueError("story generation response missing parsed payload")
        lead = cluster[0].article
        return StoryDraft(
            title_zh=result.title_zh.strip(),
            summary_zh=result.summary_zh.strip(),
            key_points=tuple(point.strip() for point in result.key_points if point.strip()),
            tags=tuple(tag.strip() for tag in result.tags if tag.strip()),
            category=result.category.strip(),
            article_ids=tuple(item.article.article_id for item in cluster),
            hero_image_url=lead.hero_image_url,
            source_article_count=len(cluster),
        )

    async def generate_stories(
        self,
        clusters: list[list[EmbeddedArticle]],
    ) -> list[StoryDraft]:
        if not clusters:
            return []
        results = await asyncio.gather(
            *(self.generate_story(cluster) for cluster in clusters),
            return_exceptions=False,
        )
        return list(results)


def _render_json_payload(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
