"""Generate immutable story drafts from clustered articles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.prompts.story_generation_prompt import STORY_GENERATION_PROMPT
from backend.app.schemas.llm.story_generation import StoryGenerationSchema
from backend.app.service.llm_client_service import (
    BatchChatRequest,
    OpenAICompatibleClient,
)
from backend.app.service.story_pipeline_contracts import EmbeddedArticle, StoryDraft


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
        llm_client: OpenAICompatibleClient | Any | None = None,
    ) -> None:
        self._llm_client = llm_client or OpenAICompatibleClient()

    def generate_story(self, cluster: list[EmbeddedArticle]) -> StoryDraft:
        return self.build_story_draft(cluster, self._generate_result(cluster))

    def generate_stories_batch(
        self,
        clusters: list[list[EmbeddedArticle]],
    ) -> list[StoryDraft]:
        if not clusters:
            return []

        if not hasattr(self._llm_client, "complete_json_batch"):
            return [self.generate_story(cluster) for cluster in clusters]

        requests = [
            BatchChatRequest(
                custom_id=f"story:{index}",
                messages=self.build_messages(cluster),
            )
            for index, cluster in enumerate(clusters)
        ]
        try:
            batch_results = self._llm_client.complete_json_batch(
                model_config=STORY_SUMMARIZATION_MODEL_CONFIG,
                requests=requests,
                schema=StoryGenerationSchema,
                metadata={"stage": "story_generation"},
            )
        except Exception:
            return [self.generate_story(cluster) for cluster in clusters]

        drafts: list[StoryDraft] = []
        for index, cluster in enumerate(clusters):
            custom_id = f"story:{index}"
            outcome = batch_results.get(custom_id)
            if outcome is None or outcome.error or not isinstance(outcome.value, StoryGenerationSchema):
                drafts.append(self.generate_story(cluster))
                continue
            drafts.append(self.build_story_draft(cluster, outcome.value))
        return drafts

    def build_messages(self, cluster: list[EmbeddedArticle]) -> list[dict[str, str]]:
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
        return [
            {"role": "system", "content": STORY_GENERATION_PROMPT},
            {"role": "user", "content": _render_json_payload([asdict(item) for item in payload])},
        ]

    def build_story_draft(
        self,
        cluster: list[EmbeddedArticle],
        result: StoryGenerationSchema,
    ) -> StoryDraft:
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

    def _generate_result(self, cluster: list[EmbeddedArticle]) -> StoryGenerationSchema:
        return self._llm_client.complete_json(
            model_config=STORY_SUMMARIZATION_MODEL_CONFIG,
            messages=self.build_messages(cluster),
            schema=StoryGenerationSchema,
        )


def _render_json_payload(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
