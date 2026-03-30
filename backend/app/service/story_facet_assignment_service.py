"""Business-day story facet assignment service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article, Story, StoryArticle, StoryFacet
from backend.app.prompts.facet_assignment_prompt import build_facet_assignment_prompt
from backend.app.schemas.llm.facet_assignment import FacetAssignmentSchema
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
class _StoryFacetInput:
    story_key: str
    synopsis_zh: str
    event_type: str
    source_names: tuple[str, ...]


class StoryFacetAssignmentService:
    """Assign and persist story facets for one business day."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        rate_limiter: LlmRateLimiter | None = None,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    async def assign_for_day(self, session: Session, business_day: date) -> list[StoryFacet]:
        story_inputs = self._load_day_story_inputs(session, business_day)
        schema = await self._assign_facets(story_inputs)
        assignments = self._resolve_assignments(story_inputs, schema)
        return self._replace_day_rows(session, business_day, assignments)

    def _load_day_story_inputs(self, session: Session, business_day: date) -> list[_StoryFacetInput]:
        stories = list(
            session.scalars(
                select(Story)
                .where(Story.business_date == business_day)
                .order_by(Story.story_key.asc())
            ).all()
        )
        if not stories:
            return []

        source_rows = session.execute(
            select(StoryArticle.story_key, Article.source_name)
            .join(Article, Article.article_id == StoryArticle.article_id)
            .where(StoryArticle.story_key.in_([story.story_key for story in stories]))
            .order_by(StoryArticle.story_key.asc(), StoryArticle.rank.asc(), Article.article_id.asc())
        ).all()
        source_names_by_story: dict[str, list[str]] = {}
        for story_key, source_name in source_rows:
            source_names_by_story.setdefault(story_key, []).append(source_name)

        return [
            _StoryFacetInput(
                story_key=story.story_key,
                synopsis_zh=story.synopsis_zh.strip(),
                event_type=story.event_type.strip() or "general",
                source_names=tuple(sorted(set(source_names_by_story.get(story.story_key, [])))),
            )
            for story in stories
        ]

    async def _assign_facets(self, story_inputs: list[_StoryFacetInput]) -> FacetAssignmentSchema:
        if not story_inputs:
            return FacetAssignmentSchema()
        client = self._get_client()
        with self._rate_limiter.lease("facet_assignment"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=0,
                messages=[
                    {"role": "system", "content": build_facet_assignment_prompt()},
                    {"role": "user", "content": self._build_user_message(story_inputs)},
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return FacetAssignmentSchema.model_validate_json(raw_content)

    def _resolve_assignments(
        self,
        story_inputs: list[_StoryFacetInput],
        schema: FacetAssignmentSchema,
    ) -> dict[str, tuple[str, ...]]:
        known_story_keys = {item.story_key for item in story_inputs}
        resolved: dict[str, tuple[str, ...]] = {item.story_key: tuple() for item in story_inputs}
        seen_story_keys: set[str] = set()

        for index, assignment in enumerate(schema.stories):
            story_key = assignment.story_key.strip()
            if not story_key:
                raise ValueError(f"stories[{index}] story_key cannot be blank")
            if story_key not in known_story_keys:
                raise ValueError(f"stories[{index}] unknown story_key: {story_key}")
            if story_key in seen_story_keys:
                raise ValueError(f"stories[{index}] duplicated story_key: {story_key}")
            seen_story_keys.add(story_key)

            cleaned_facets = [facet.strip() for facet in assignment.facets]
            if any(not facet for facet in cleaned_facets):
                raise ValueError(f"stories[{index}] facets contains blank value")
            for facet in cleaned_facets:
                if facet not in RUNTIME_FACETS:
                    raise ValueError(f"stories[{index}] unsupported runtime facet: {facet}")
            resolved[story_key] = tuple(sorted(set(cleaned_facets)))

        return resolved

    def _replace_day_rows(
        self,
        session: Session,
        business_day: date,
        assignments_by_story: dict[str, tuple[str, ...]],
    ) -> list[StoryFacet]:
        story_keys = list(
            session.scalars(
                select(Story.story_key)
                .where(Story.business_date == business_day)
                .order_by(Story.story_key.asc())
            ).all()
        )
        if story_keys:
            session.execute(delete(StoryFacet).where(StoryFacet.story_key.in_(story_keys)))

        rows = [
            StoryFacet(story_key=story_key, facet=facet)
            for story_key in sorted(assignments_by_story)
            for facet in assignments_by_story[story_key]
        ]
        if rows:
            session.add_all(rows)
            session.flush()
            for row in rows:
                session.expunge(row)
        return rows

    def _build_user_message(self, story_inputs: list[_StoryFacetInput]) -> str:
        payload = {
            "stories": [
                {
                    "story_key": item.story_key,
                    "synopsis_zh": item.synopsis_zh,
                    "event_type": item.event_type,
                    "source_names": list(item.source_names),
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
                raise RuntimeError("story facet assignment requires configured API key")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client
