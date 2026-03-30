"""Business-day digest generation service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article, Digest, DigestArticle, DigestStory, Story, StoryArticle
from backend.app.prompts.digest_generation_prompt import build_digest_generation_prompt
from backend.app.schemas.llm.digest_generation import DigestGenerationSchema
from backend.app.service.llm_rate_limiter import LlmRateLimiter

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class _StrictStoryInput:
    strict_story_key: str
    synopsis_zh: str
    event_type: str
    article_ids: tuple[str, ...]
    source_names: tuple[str, ...]


@dataclass(frozen=True)
class _ResolvedPlan:
    facet: str
    strict_story_keys: tuple[str, ...]
    title_zh: str
    dek_zh: str
    body_markdown: str
    article_ids: tuple[str, ...]
    source_names: tuple[str, ...]


class DigestGenerationService:
    """Generate immutable digest rows from one business-day strict-story set."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        rate_limiter: LlmRateLimiter | None = None,
    ) -> None:
        self._client = client
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    async def generate_for_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[Digest]:
        strict_stories = self._load_day_strict_stories(session, business_day)
        plans = await self._select_digest_plans(strict_stories)
        resolved = self._resolve_plans(strict_stories, plans)
        return self._replace_day_digests(session, business_day, run_id=run_id, plans=resolved)

    def _load_day_strict_stories(self, session: Session, business_day: date) -> list[_StrictStoryInput]:
        stories = list(
            session.scalars(
                select(Story)
                .where(Story.business_date == business_day)
                .order_by(Story.story_key.asc())
            ).all()
        )
        if not stories:
            return []

        article_pairs = session.execute(
            select(
                StoryArticle.story_key,
                StoryArticle.article_id,
                Article.source_name,
            )
            .join(Article, Article.article_id == StoryArticle.article_id)
            .where(StoryArticle.story_key.in_([story.story_key for story in stories]))
            .order_by(
                StoryArticle.story_key.asc(),
                StoryArticle.rank.asc(),
                StoryArticle.article_id.asc(),
            )
        ).all()
        by_story_articles: dict[str, list[str]] = {}
        by_story_sources: dict[str, list[str]] = {}
        for story_key, article_id, source_name in article_pairs:
            by_story_articles.setdefault(story_key, []).append(article_id)
            by_story_sources.setdefault(story_key, []).append(source_name)

        payloads: list[_StrictStoryInput] = []
        for story in stories:
            event_type = story.event_type.strip() or "general"
            source_names = sorted(set(by_story_sources.get(story.story_key, [])))
            payloads.append(
                _StrictStoryInput(
                    strict_story_key=story.story_key,
                    synopsis_zh=story.synopsis_zh.strip(),
                    event_type=event_type,
                    article_ids=tuple(by_story_articles.get(story.story_key, [])),
                    source_names=tuple(source_names),
                )
            )
        return payloads

    async def _select_digest_plans(
        self, strict_stories: list[_StrictStoryInput]
    ) -> DigestGenerationSchema:
        if not strict_stories:
            return DigestGenerationSchema()
        client = self._get_client()
        with self._rate_limiter.lease("digest_generation"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=0,
                messages=[
                    {"role": "system", "content": build_digest_generation_prompt()},
                    {
                        "role": "user",
                        "content": self._build_user_message(strict_stories),
                    },
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return DigestGenerationSchema.model_validate_json(raw_content)

    def _resolve_plans(
        self,
        strict_stories: list[_StrictStoryInput],
        schema: DigestGenerationSchema,
    ) -> list[_ResolvedPlan]:
        story_by_key = {item.strict_story_key: item for item in strict_stories}
        resolved: list[_ResolvedPlan] = []
        assigned_story_to_digest: dict[str, int] = {}
        for digest_index, plan in enumerate(schema.digests):
            facet = plan.facet.strip()
            if not facet:
                raise ValueError(f"digest[{digest_index}] facet cannot be blank")
            title_zh = plan.title_zh.strip()
            if not title_zh:
                raise ValueError(f"digest[{digest_index}] title_zh cannot be blank")
            body_markdown = plan.body_markdown.strip()
            if not body_markdown:
                raise ValueError(f"digest[{digest_index}] body_markdown cannot be blank")

            plan_story_keys = [key.strip() for key in plan.strict_story_keys]
            if any(not key for key in plan_story_keys):
                raise ValueError(f"digest[{digest_index}] strict_story_keys contains blank value")
            if len(set(plan_story_keys)) != len(plan_story_keys):
                raise ValueError(f"digest[{digest_index}] strict_story_keys contains duplicates")
            unknown_story_keys = sorted(key for key in plan_story_keys if key not in story_by_key)
            if unknown_story_keys:
                raise ValueError(
                    f"digest[{digest_index}] unknown strict_story_key(s): {', '.join(unknown_story_keys)}"
                )
            for strict_story_key in plan_story_keys:
                if strict_story_key in assigned_story_to_digest:
                    previous_digest_index = assigned_story_to_digest[strict_story_key]
                    raise ValueError(
                        "strict_story_key assigned more than once across digests: "
                        f"{strict_story_key} (digest[{previous_digest_index}] and digest[{digest_index}])"
                    )
                assigned_story_to_digest[strict_story_key] = digest_index
            strict_story_keys = tuple(sorted(plan_story_keys))

            ordered_articles: list[str] = []
            seen_articles: set[str] = set()
            source_names: set[str] = set()
            for strict_story_key in strict_story_keys:
                story = story_by_key[strict_story_key]
                source_names.update(story.source_names)
                for article_id in story.article_ids:
                    if article_id in seen_articles:
                        continue
                    seen_articles.add(article_id)
                    ordered_articles.append(article_id)

            resolved.append(
                _ResolvedPlan(
                    facet=facet,
                    strict_story_keys=strict_story_keys,
                    title_zh=title_zh,
                    dek_zh=plan.dek_zh.strip(),
                    body_markdown=body_markdown,
                    article_ids=tuple(ordered_articles),
                    source_names=tuple(sorted(source_names)),
                )
            )

        resolved.sort(key=lambda item: (item.facet, item.strict_story_keys))
        return resolved

    def _replace_day_digests(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
        plans: list[_ResolvedPlan],
    ) -> list[Digest]:
        existing_digests = list(
            session.scalars(
                select(Digest).where(Digest.business_date == business_day).order_by(Digest.digest_key.asc())
            ).all()
        )
        existing_memberships = list(
            session.execute(
                select(DigestStory.digest_key, DigestStory.story_key)
                .where(DigestStory.digest_key.in_([row.digest_key for row in existing_digests]))
                .order_by(DigestStory.digest_key.asc(), DigestStory.rank.asc())
            ).all()
        )
        memberships_by_digest: dict[str, tuple[str, ...]] = {}
        for digest_key, story_key in existing_memberships:
            memberships_by_digest.setdefault(digest_key, tuple())
            memberships_by_digest[digest_key] = tuple(
                sorted(set((*memberships_by_digest[digest_key], story_key)))
            )

        reusable_key_map: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        for digest in existing_digests:
            membership = memberships_by_digest.get(digest.digest_key, tuple())
            reusable_key_map.setdefault((digest.facet, membership), []).append(digest.digest_key)

        old_keys = [row.digest_key for row in existing_digests]
        if old_keys:
            session.execute(delete(DigestArticle).where(DigestArticle.digest_key.in_(old_keys)))
            session.execute(delete(DigestStory).where(DigestStory.digest_key.in_(old_keys)))
            session.execute(delete(Digest).where(Digest.digest_key.in_(old_keys)))

        digests: list[Digest] = []
        digest_story_rows: list[DigestStory] = []
        article_rows: list[DigestArticle] = []
        for plan in plans:
            reuse_bucket = reusable_key_map.get((plan.facet, plan.strict_story_keys), [])
            digest_key = reuse_bucket.pop(0) if reuse_bucket else str(uuid4())
            digest = Digest(
                digest_key=digest_key,
                business_date=business_day,
                facet=plan.facet,
                title_zh=plan.title_zh,
                dek_zh=plan.dek_zh,
                body_markdown=plan.body_markdown,
                source_article_count=len(plan.article_ids),
                source_names_json=list(plan.source_names),
                created_run_id=run_id,
                generation_status="done",
                generation_error=None,
            )
            digests.append(digest)
            digest_story_rows.extend(
                [
                    DigestStory(
                        digest_key=digest_key,
                        story_key=strict_story_key,
                        rank=rank,
                    )
                    for rank, strict_story_key in enumerate(plan.strict_story_keys)
                ]
            )
            article_rows.extend(
                [
                    DigestArticle(
                        digest_key=digest_key,
                        article_id=article_id,
                        rank=rank,
                    )
                    for rank, article_id in enumerate(plan.article_ids)
                ]
            )

        session.add_all(digests)
        session.flush()
        session.add_all(digest_story_rows)
        session.add_all(article_rows)
        session.flush()
        for digest in digests:
            session.expunge(digest)
        return digests

    def _build_user_message(self, strict_stories: list[_StrictStoryInput]) -> str:
        payload = {
            "strict_stories": [
                {
                    "strict_story_key": story.strict_story_key,
                    "synopsis_zh": story.synopsis_zh,
                    "event_type": story.event_type,
                    "article_ids": list(story.article_ids),
                    "source_names": list(story.source_names),
                }
                for story in strict_stories
            ]
        }
        return json.dumps(payload, ensure_ascii=False)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
            if not api_key:
                raise RuntimeError("digest generation requires configured API key")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client
