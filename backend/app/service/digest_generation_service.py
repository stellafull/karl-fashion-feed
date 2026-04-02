"""Business-day digest generation orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import Digest, DigestArticle, DigestStory, Story, StoryFacet
from backend.app.service.digest_packaging_service import DigestPackagingService, ResolvedDigestPlan
from backend.app.service.digest_report_writing_service import DigestReportWritingService
from backend.app.service.llm_rate_limiter import LlmRateLimiter
from backend.app.service.story_facet_assignment_service import StoryFacetAssignmentService

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class _WrittenPlanDigest:
    plan: ResolvedDigestPlan
    digest: Digest
    article_ids: tuple[str, ...]


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
            rate_limiter=shared_rate_limiter,
        )
        self._packaging_service = packaging_service or DigestPackagingService(
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
        await self._facet_assignment_service.assign_for_day(session, business_day, run_id=run_id)
        plans = await self._packaging_service.build_plans_for_day(session, business_day, run_id=run_id)
        if self._has_packaging_input(session, business_day) and not plans:
            raise RuntimeError(
                f"digest packaging produced zero digest plans for business day {business_day.isoformat()}"
            )

        written_digests: list[_WrittenPlanDigest] = []
        for plan in plans:
            if plan.business_date != business_day:
                raise RuntimeError(
                    "digest plan business_date mismatch: "
                    f"expected {business_day.isoformat()}, got {plan.business_date.isoformat()}"
                )
            digest = await self._report_writing_service.write_digest(session, plan, run_id=run_id)
            if digest.business_date != plan.business_date:
                raise RuntimeError(
                    "digest business_date mismatch: "
                    f"expected {plan.business_date.isoformat()}, got {digest.business_date.isoformat()}"
                )
            written_digests.append(
                _WrittenPlanDigest(
                    plan=plan,
                    digest=digest,
                    article_ids=self._extract_writer_selected_article_ids(digest=digest, plan=plan),
                )
            )
        return self._replace_day_digests(session, business_day, written_digests=written_digests)

    def _has_packaging_input(self, session: Session, business_day: date) -> bool:
        row = session.execute(
            select(StoryFacet.story_key)
            .join(Story, Story.story_key == StoryFacet.story_key)
            .where(Story.business_date == business_day)
            .limit(1)
        ).first()
        return row is not None

    def _replace_day_digests(
        self,
        session: Session,
        business_day: date,
        *,
        written_digests: list[_WrittenPlanDigest],
    ) -> list[Digest]:
        old_digest_keys = list(
            session.scalars(
                select(Digest.digest_key)
                .where(Digest.business_date == business_day)
                .order_by(Digest.digest_key.asc())
            ).all()
        )
        if old_digest_keys:
            session.execute(delete(DigestArticle).where(DigestArticle.digest_key.in_(old_digest_keys)))
            session.execute(delete(DigestStory).where(DigestStory.digest_key.in_(old_digest_keys)))
            session.execute(delete(Digest).where(Digest.digest_key.in_(old_digest_keys)))

        digests: list[Digest] = []
        for item in written_digests:
            digest = item.digest
            if not digest.digest_key:
                digest.digest_key = str(uuid4())
            if digest.business_date != business_day:
                raise RuntimeError(
                    "digest business_date mismatch at persistence: "
                    f"expected {business_day.isoformat()}, got {digest.business_date.isoformat()}"
                )
            digests.append(digest)

        if digests:
            session.add_all(digests)
            session.flush()

        digest_story_rows: list[DigestStory] = []
        digest_article_rows: list[DigestArticle] = []
        for item in written_digests:
            digest_key = item.digest.digest_key
            if not digest_key:
                raise RuntimeError("digest key missing after persistence")
            digest_story_rows.extend(
                [
                    DigestStory(
                        digest_key=digest_key,
                        story_key=story_key,
                        rank=rank,
                    )
                    for rank, story_key in enumerate(item.plan.story_keys)
                ]
            )
            digest_article_rows.extend(
                [
                    DigestArticle(
                        digest_key=digest_key,
                        article_id=article_id,
                        rank=rank,
                    )
                    for rank, article_id in enumerate(item.article_ids)
                ]
            )

        if digest_story_rows:
            session.add_all(digest_story_rows)
        if digest_article_rows:
            session.add_all(digest_article_rows)
        if digest_story_rows or digest_article_rows:
            session.flush()

        for digest in digests:
            session.expunge(digest)
        return digests

    def _extract_writer_selected_article_ids(
        self,
        *,
        digest: Digest,
        plan: ResolvedDigestPlan,
    ) -> tuple[str, ...]:
        raw_value = digest.selected_source_article_ids
        if not isinstance(raw_value, (tuple, list)):
            raise RuntimeError("digest report writing must provide selected_source_article_ids")

        article_ids = [str(article_id).strip() for article_id in raw_value]
        if not article_ids or any(not article_id for article_id in article_ids):
            raise RuntimeError("digest report writing returned invalid selected_source_article_ids")
        if len(set(article_ids)) != len(article_ids):
            raise RuntimeError("digest report writing returned duplicate selected_source_article_ids")

        allowed_article_ids = set(plan.article_ids)
        unknown_article_ids = sorted(article_id for article_id in article_ids if article_id not in allowed_article_ids)
        if unknown_article_ids:
            joined = ", ".join(unknown_article_ids)
            raise RuntimeError(f"digest report writing returned unknown source_article_ids: {joined}")
        return tuple(article_ids)


__all__ = ["DigestGenerationService"]
