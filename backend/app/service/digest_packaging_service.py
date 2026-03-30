"""Business-day digest packaging service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article, Story, StoryArticle, StoryFacet
from backend.app.prompts.digest_packaging_prompt import build_digest_packaging_prompt
from backend.app.schemas.llm.digest_packaging import DigestPackagingSchema
from backend.app.service.llm_rate_limiter import LlmRateLimiter

if TYPE_CHECKING:
    from openai import AsyncOpenAI


RUNTIME_FACETS = frozenset(
    {
        "runway_series",
        "street_style",
        "trend_summary",
        "brand_market",
    }
)


@dataclass(frozen=True)
class _ArticlePackagingInput:
    article_id: str
    source_name: str
    title_raw: str
    summary_raw: str


@dataclass(frozen=True)
class _StoryPackagingInput:
    story_key: str
    synopsis_zh: str
    event_type: str
    facets: tuple[str, ...]
    article_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    articles: tuple[_ArticlePackagingInput, ...]


@dataclass(frozen=True)
class ResolvedDigestPlan:
    business_date: date
    facet: str
    story_keys: tuple[str, ...]
    article_ids: tuple[str, ...]
    editorial_angle: str
    title_zh: str
    dek_zh: str
    source_names: tuple[str, ...]


class DigestPackagingService:
    """Package faceted stories into digest plans for one business day."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        rate_limiter: LlmRateLimiter | None = None,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    async def build_plans_for_day(self, session: Session, business_day: date) -> list[ResolvedDigestPlan]:
        story_inputs = self._load_day_story_inputs(session, business_day)
        if not story_inputs:
            return []

        story_inputs_by_facet = self._group_story_inputs_by_facet(story_inputs)
        plans: list[ResolvedDigestPlan] = []
        for facet in sorted(story_inputs_by_facet):
            facet_story_inputs = story_inputs_by_facet[facet]
            schema = await self._select_digest_plans(
                facet=facet,
                story_inputs=facet_story_inputs,
            )
            plans.extend(
                self._resolve_plans(
                    business_day=business_day,
                    facet=facet,
                    story_inputs=facet_story_inputs,
                    schema=schema,
                )
            )
        return plans

    def _load_day_story_inputs(self, session: Session, business_day: date) -> list[_StoryPackagingInput]:
        stories = list(
            session.scalars(
                select(Story)
                .where(Story.business_date == business_day)
                .order_by(Story.story_key.asc())
            ).all()
        )
        if not stories:
            return []

        story_keys = [story.story_key for story in stories]
        facet_rows = session.execute(
            select(StoryFacet.story_key, StoryFacet.facet)
            .where(StoryFacet.story_key.in_(story_keys))
            .order_by(StoryFacet.story_key.asc(), StoryFacet.facet.asc())
        ).all()
        facets_by_story: dict[str, list[str]] = {}
        for story_key, facet in facet_rows:
            if facet not in RUNTIME_FACETS:
                raise ValueError(f"unsupported runtime facet: {facet}")
            facets_by_story.setdefault(story_key, []).append(facet)

        article_rows = session.execute(
            select(
                StoryArticle.story_key,
                Article.article_id,
                Article.source_name,
                Article.title_raw,
                Article.summary_raw,
            )
            .join(Article, Article.article_id == StoryArticle.article_id)
            .where(StoryArticle.story_key.in_(story_keys))
            .order_by(StoryArticle.story_key.asc(), StoryArticle.rank.asc(), Article.article_id.asc())
        ).all()
        articles_by_story: dict[str, list[_ArticlePackagingInput]] = {}
        for story_key, article_id, source_name, title_raw, summary_raw in article_rows:
            articles_by_story.setdefault(story_key, []).append(
                _ArticlePackagingInput(
                    article_id=article_id,
                    source_name=source_name,
                    title_raw=title_raw,
                    summary_raw=summary_raw,
                )
            )

        inputs: list[_StoryPackagingInput] = []
        for story in stories:
            facets = tuple(sorted(set(facets_by_story.get(story.story_key, []))))
            if not facets:
                continue
            articles = tuple(articles_by_story.get(story.story_key, []))
            inputs.append(
                _StoryPackagingInput(
                    story_key=story.story_key,
                    synopsis_zh=story.synopsis_zh.strip(),
                    event_type=story.event_type.strip() or "general",
                    facets=facets,
                    article_ids=tuple(article.article_id for article in articles),
                    source_names=tuple(sorted({article.source_name for article in articles})),
                    articles=articles,
                )
            )
        return inputs

    def _group_story_inputs_by_facet(
        self,
        story_inputs: list[_StoryPackagingInput],
    ) -> dict[str, list[_StoryPackagingInput]]:
        grouped: dict[str, list[_StoryPackagingInput]] = {}
        for item in story_inputs:
            for facet in item.facets:
                grouped.setdefault(facet, []).append(item)
        return grouped

    async def _select_digest_plans(
        self,
        *,
        facet: str,
        story_inputs: list[_StoryPackagingInput],
    ) -> DigestPackagingSchema:
        if not story_inputs:
            return DigestPackagingSchema()
        client = self._get_client()
        with self._rate_limiter.lease("digest_packaging"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=0,
                messages=[
                    {"role": "system", "content": build_digest_packaging_prompt()},
                    {"role": "user", "content": self._build_user_message(facet, story_inputs)},
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return DigestPackagingSchema.model_validate_json(raw_content)

    def _resolve_plans(
        self,
        *,
        business_day: date,
        facet: str,
        story_inputs: list[_StoryPackagingInput],
        schema: DigestPackagingSchema,
    ) -> list[ResolvedDigestPlan]:
        story_by_key = {item.story_key: item for item in story_inputs}
        article_by_id = {
            article.article_id: article
            for item in story_inputs
            for article in item.articles
        }
        resolved: list[ResolvedDigestPlan] = []

        for index, plan in enumerate(schema.digests):
            plan_facet = plan.facet.strip()
            if not plan_facet:
                raise ValueError(f"digests[{index}] facet cannot be blank")
            if plan_facet not in RUNTIME_FACETS:
                raise ValueError(f"digests[{index}] unsupported runtime facet: {plan_facet}")
            if plan_facet != facet:
                raise ValueError(
                    f"digests[{index}] facet {plan_facet} does not match requested facet {facet}"
                )
            title_zh = plan.title_zh.strip()
            if not title_zh:
                raise ValueError(f"digests[{index}] title_zh cannot be blank")
            dek_zh = plan.dek_zh.strip()
            if not dek_zh:
                raise ValueError(f"digests[{index}] dek_zh cannot be blank")
            editorial_angle = plan.editorial_angle.strip()
            if not editorial_angle:
                raise ValueError(f"digests[{index}] editorial_angle cannot be blank")

            story_keys = [story_key.strip() for story_key in plan.story_keys]
            if any(not story_key for story_key in story_keys):
                raise ValueError(f"digests[{index}] story_keys contains blank value")
            if len(set(story_keys)) != len(story_keys):
                raise ValueError(f"digests[{index}] story_keys contains duplicates")
            unknown_story_keys = sorted(story_key for story_key in story_keys if story_key not in story_by_key)
            if unknown_story_keys:
                joined = ", ".join(unknown_story_keys)
                raise ValueError(f"digests[{index}] unknown story_key(s): {joined}")
            for story_key in story_keys:
                if facet not in story_by_key[story_key].facets:
                    raise ValueError(
                        f"digests[{index}] facet {facet} not assigned to story_key {story_key}"
                    )

            allowed_article_ids = [
                article_id
                for story_key in story_keys
                for article_id in story_by_key[story_key].article_ids
            ]
            allowed_article_id_set = set(allowed_article_ids)
            article_ids = [article_id.strip() for article_id in plan.article_ids]
            if any(not article_id for article_id in article_ids):
                raise ValueError(f"digests[{index}] article_ids contains blank value")
            if len(set(article_ids)) != len(article_ids):
                raise ValueError(f"digests[{index}] article_ids contains duplicates")
            unknown_article_ids = sorted(
                article_id for article_id in article_ids if article_id not in allowed_article_id_set
            )
            if unknown_article_ids:
                joined = ", ".join(unknown_article_ids)
                raise ValueError(f"digests[{index}] unknown article_id(s): {joined}")

            source_names = tuple(sorted({article_by_id[article_id].source_name for article_id in article_ids}))
            resolved.append(
                ResolvedDigestPlan(
                    business_date=business_day,
                    facet=plan_facet,
                    story_keys=tuple(story_keys),
                    article_ids=tuple(article_ids),
                    editorial_angle=editorial_angle,
                    title_zh=title_zh,
                    dek_zh=dek_zh,
                    source_names=source_names,
                )
            )

        return resolved

    def _build_user_message(self, facet: str, story_inputs: list[_StoryPackagingInput]) -> str:
        payload = {
            "facet": facet,
            "stories": [
                {
                    "story_key": item.story_key,
                    "synopsis_zh": item.synopsis_zh,
                    "event_type": item.event_type,
                    "facets": list(item.facets),
                    "article_ids": list(item.article_ids),
                    "source_names": list(item.source_names),
                    "articles": [
                        {
                            "article_id": article.article_id,
                            "source_name": article.source_name,
                            "title_raw": article.title_raw,
                            "summary_raw": article.summary_raw,
                        }
                        for article in item.articles
                    ],
                }
                for item in story_inputs
            ]
        }
        return json.dumps(payload, ensure_ascii=False)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
            if not api_key:
                raise RuntimeError("digest packaging requires configured API key")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client


__all__ = ["DigestPackagingService", "ResolvedDigestPlan"]
