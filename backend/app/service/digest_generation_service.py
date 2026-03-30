"""Business-day digest generation orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import Article, Digest, DigestArticle, DigestStory, Story, StoryArticle
from backend.app.service.digest_packaging_service import DigestPackagingService
from backend.app.service.digest_report_writing_service import DigestReportWritingService, _WrittenDigest
from backend.app.service.llm_rate_limiter import LlmRateLimiter
from backend.app.service.story_facet_assignment_service import StoryFacetAssignmentService

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
    """Assign facets, package stories, write digests, and replace day memberships."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        facet_assignment_service: StoryFacetAssignmentService | None = None,
        packaging_service: DigestPackagingService | None = None,
        report_writing_service: DigestReportWritingService | None = None,
    ) -> None:
        shared_rate_limiter = rate_limiter or LlmRateLimiter()
        self._facet_assignment_service = facet_assignment_service or StoryFacetAssignmentService(
            client=client,
            rate_limiter=shared_rate_limiter,
        )
        self._packaging_service = packaging_service or DigestPackagingService(
            client=client,
            rate_limiter=shared_rate_limiter,
        )
        self._report_writing_service = report_writing_service or DigestReportWritingService(
            client=client,
            rate_limiter=shared_rate_limiter,
        )

    async def generate_for_day(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
    ) -> list[Digest]:
        await self._facet_assignment_service.assign_for_day(session, business_day)
        plans = await self._packaging_service.package_for_day(session, business_day)
        written_digests = await self._report_writing_service.write_digests(
            session,
            business_day,
            run_id=run_id,
            plans=plans,
        )
        return self._replace_day_digests(session, business_day, written_digests=written_digests)

    def _replace_day_digests(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str | None = None,
        written_digests: list[_WrittenDigest] | None = None,
        plans: list[_ResolvedPlan] | None = None,
    ) -> list[Digest]:
        effective_written_digests = list(written_digests or [])
        if plans is not None:
            effective_written_digests.extend(
                self._convert_legacy_plans(
                    business_day,
                    run_id=run_id,
                    plans=plans,
                )
            )

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
        for written_digest in effective_written_digests:
            reuse_bucket = reusable_key_map.get((written_digest.digest.facet, written_digest.story_keys), [])
            digest_key = reuse_bucket.pop(0) if reuse_bucket else str(uuid4())
            digest = written_digest.digest
            digest.digest_key = digest_key
            digest.business_date = business_day
            digests.append(digest)
            digest_story_rows.extend(
                [
                    DigestStory(
                        digest_key=digest_key,
                        story_key=story_key,
                        rank=rank,
                    )
                    for rank, story_key in enumerate(written_digest.story_keys)
                ]
            )
            article_rows.extend(
                [
                    DigestArticle(
                        digest_key=digest_key,
                        article_id=article_id,
                        rank=rank,
                    )
                    for rank, article_id in enumerate(written_digest.article_ids)
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
        article_ids_by_story: dict[str, list[str]] = {}
        source_names_by_story: dict[str, list[str]] = {}
        for story_key, article_id, source_name in article_pairs:
            article_ids_by_story.setdefault(story_key, []).append(article_id)
            source_names_by_story.setdefault(story_key, []).append(source_name)

        return [
            _StrictStoryInput(
                strict_story_key=story.story_key,
                synopsis_zh=story.synopsis_zh.strip(),
                event_type=story.event_type.strip() or "general",
                article_ids=tuple(article_ids_by_story.get(story.story_key, [])),
                source_names=tuple(sorted(set(source_names_by_story.get(story.story_key, [])))),
            )
            for story in stories
        ]

    def _convert_legacy_plans(
        self,
        business_day: date,
        *,
        run_id: str | None,
        plans: list[_ResolvedPlan],
    ) -> list[_WrittenDigest]:
        return [
            _WrittenDigest(
                digest=Digest(
                    business_date=business_day,
                    facet=plan.facet,
                    title_zh=plan.title_zh,
                    dek_zh=plan.dek_zh,
                    body_markdown=plan.body_markdown,
                    source_article_count=len(plan.article_ids),
                    source_names_json=list(plan.source_names),
                    created_run_id=run_id or "legacy-digest-generation-service",
                    generation_status="done",
                    generation_error=None,
                ),
                story_keys=plan.strict_story_keys,
                article_ids=plan.article_ids,
            )
            for plan in plans
        ]


__all__ = ["DigestGenerationService", "_ResolvedPlan", "_StrictStoryInput"]
