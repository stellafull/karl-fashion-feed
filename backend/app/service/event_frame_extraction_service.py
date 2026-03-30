"""Sparse event frame extraction for one parsed article."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, date
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.core.database import SessionLocal
from backend.app.models import Article, ArticleEventFrame
from backend.app.models import ensure_article_storage_schema
from backend.app.models.article import _utcnow_naive
from backend.app.prompts.event_frame_extraction_prompt import build_event_frame_extraction_prompt
from backend.app.schemas.llm.event_frame_extraction import (
    EventFrameExtractionSchema,
    ExtractedEventFrame,
)
from backend.app.service.article_parse_service import ArticleMarkdownService
from backend.app.service.llm_rate_limiter import LlmRateLimiter

if TYPE_CHECKING:
    from openai import AsyncOpenAI

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")


class EventFrameExtractionService:
    """Extract up to three high-confidence event frames from one article."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        markdown_service: ArticleMarkdownService | None = None,
        rate_limiter: LlmRateLimiter | None = None,
    ) -> None:
        self._client = client
        self._markdown_service = markdown_service or ArticleMarkdownService()
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    async def extract_frames(
        self,
        session: Session,
        article: Article,
        *,
        claimed_updated_at: datetime | None = None,
    ) -> tuple[ArticleEventFrame, ...]:
        """Extract sparse event frames and persist them for one parsed article."""
        if article.parse_status != "done":
            raise ValueError(f"parse must be done before frame extraction: {article.article_id}")
        if article.event_frame_attempts >= 3:
            article.event_frame_status = "abandoned"
            article.event_frame_updated_at = _utcnow_naive()
            session.flush()
            return ()

        try:
            payload = await self._infer_frames(article)
            frames = tuple(self._build_frame(article, frame) for frame in payload.frames[:3])
            session.execute(
                delete(ArticleEventFrame).where(ArticleEventFrame.article_id == article.article_id)
            )
            session.add_all(frames)
            finalized = _finalize_event_frame_success(
                session=session,
                article=article,
                claimed_updated_at=claimed_updated_at,
            )
            if not finalized:
                raise RuntimeError(
                    f"event frame ownership lost before finalize: article_id={article.article_id}"
                )
            session.flush()
        except Exception as exc:
            self._persist_failure_state(
                session,
                article,
                exc,
                claimed_updated_at=claimed_updated_at,
            )
            return ()

        return frames

    async def _infer_frames(self, article: Article) -> EventFrameExtractionSchema:
        """Run one structured LLM extraction over parsed article markdown."""
        if not article.markdown_rel_path:
            raise ValueError(f"markdown_rel_path is required for frame extraction: {article.article_id}")

        markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
        if not markdown.strip():
            raise ValueError(f"markdown is empty for frame extraction: {article.article_id}")

        client = self._get_client()
        with self._rate_limiter.lease("event_frame_extraction"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=STORY_SUMMARIZATION_MODEL_CONFIG.temperature,
                messages=[
                    {
                        "role": "system",
                        "content": build_event_frame_extraction_prompt(),
                    },
                    {
                        "role": "user",
                        "content": self._build_user_message(article=article, markdown=markdown),
                    },
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return EventFrameExtractionSchema.model_validate_json(raw_content)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
            if not api_key:
                raise ValueError(f"missing API key for {STORY_SUMMARIZATION_MODEL_CONFIG.model_name}")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client

    def _build_user_message(self, *, article: Article, markdown: str) -> str:
        payload = {
            "article": {
                "article_id": article.article_id,
                "source_name": article.source_name,
                "source_lang": article.source_lang,
                "category": article.category,
                "canonical_url": article.canonical_url,
                "title_raw": article.title_raw,
                "summary_raw": article.summary_raw,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "metadata_json": article.metadata_json,
            },
            "markdown": markdown,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _build_frame(
        self,
        article: Article,
        frame: ExtractedEventFrame,
    ) -> ArticleEventFrame:
        return ArticleEventFrame(
            article_id=article.article_id,
            business_date=self._resolve_business_date(article),
            event_type=frame.event_type.strip(),
            subject_json=dict(frame.subject_json),
            action_text=frame.action_text.strip(),
            object_text=frame.object_text.strip(),
            place_text=self._normalize_optional_text(frame.place_text),
            collection_text=self._normalize_optional_text(frame.collection_text),
            season_text=self._normalize_optional_text(frame.season_text),
            show_context_text=self._normalize_optional_text(frame.show_context_text),
            evidence_json=[dict(item) for item in frame.evidence_json],
            signature_json=dict(frame.signature_json),
            extraction_confidence=float(frame.extraction_confidence),
            extraction_status="done",
            extraction_error=None,
        )

    def _normalize_optional_text(self, value: str | None) -> str:
        if value is None:
            return ""
        normalized = value.strip()
        return normalized

    def _resolve_business_date(self, article: Article) -> date:
        ingested_at = article.ingested_at
        if ingested_at.tzinfo is None:
            ingested_at = ingested_at.replace(tzinfo=UTC)
        return ingested_at.astimezone(ASIA_SHANGHAI).date()

    def _persist_failure_state(
        self,
        session: Session,
        article: Article,
        exc: Exception,
        *,
        claimed_updated_at: datetime | None = None,
    ) -> Article:
        session.rollback()
        stored_article = session.get(Article, article.article_id)
        if stored_article is None:
            raise RuntimeError(f"article disappeared during frame extraction failure handling: {article.article_id}")

        finalized = _finalize_event_frame_failure(
            session=session,
            article=stored_article,
            exc=exc,
            claimed_updated_at=claimed_updated_at,
        )
        if not finalized:
            raise RuntimeError(
                f"event frame ownership lost before finalize: article_id={article.article_id}"
            )
        session.flush()
        return stored_article


def run_extract_event_frames(*, article_id: str) -> tuple[ArticleEventFrame, ...]:
    """Run event-frame extraction for one article and fail fast on non-done status."""
    service = EventFrameExtractionService()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        claim_now = _utcnow_naive()
        claim_result = session.execute(
            update(Article)
            .where(
                Article.article_id == article_id,
                Article.parse_status == "done",
                Article.event_frame_status.in_(("pending", "failed", "queued")),
                Article.event_frame_attempts < 3,
            )
            .values(
                event_frame_status="running",
                event_frame_error=None,
                event_frame_updated_at=claim_now,
            )
        )
        if claim_result.rowcount == 1:
            session.commit()

        article = session.get(Article, article_id)
        if article is None:
            raise RuntimeError(f"article not found for frame extraction: {article_id}")
        if article.parse_status != "done":
            raise RuntimeError(f"parse must be done before frame extraction: {article_id}")
        if article.event_frame_attempts >= 3:
            raise RuntimeError(f"article already exhausted frame extraction retries: {article_id}")
        if article.event_frame_status != "running" or article.event_frame_updated_at != claim_now:
            raise RuntimeError(
                f"article is not runnable for frame extraction: {article_id} ({article.event_frame_status})"
            )

        frames = asyncio.run(
            service.extract_frames(
                session,
                article,
                claimed_updated_at=claim_now,
            )
        )
        session.commit()

        refreshed = session.get(Article, article_id)
        if refreshed is None:
            raise RuntimeError(f"article disappeared after frame extraction: {article_id}")
        if refreshed.event_frame_status != "done":
            raise RuntimeError(
                f"frame extraction did not complete for article {article_id}: {refreshed.event_frame_status}"
            )
        return frames


def _finalize_event_frame_success(
    *,
    session: Session,
    article: Article,
    claimed_updated_at: datetime | None,
) -> bool:
    if claimed_updated_at is None:
        article.event_frame_status = "done"
        article.event_frame_error = None
        article.event_frame_updated_at = _utcnow_naive()
        return True

    finalize_result = session.execute(
        update(Article)
        .where(
            Article.article_id == article.article_id,
            Article.event_frame_status == "running",
            Article.event_frame_updated_at == claimed_updated_at,
        )
        .values(
            event_frame_status="done",
            event_frame_error=None,
            event_frame_updated_at=_utcnow_naive(),
        )
    )
    return finalize_result.rowcount == 1


def _finalize_event_frame_failure(
    *,
    session: Session,
    article: Article,
    exc: Exception,
    claimed_updated_at: datetime | None,
) -> bool:
    if claimed_updated_at is None:
        article.event_frame_attempts += 1
        article.event_frame_status = "abandoned" if article.event_frame_attempts >= 3 else "failed"
        article.event_frame_error = f"{exc.__class__.__name__}: {exc}"
        article.event_frame_updated_at = _utcnow_naive()
        return True

    next_attempts = article.event_frame_attempts + 1
    finalize_result = session.execute(
        update(Article)
        .where(
            Article.article_id == article.article_id,
            Article.event_frame_status == "running",
            Article.event_frame_updated_at == claimed_updated_at,
        )
        .values(
            event_frame_attempts=next_attempts,
            event_frame_status="abandoned" if next_attempts >= 3 else "failed",
            event_frame_error=f"{exc.__class__.__name__}: {exc}",
            event_frame_updated_at=_utcnow_naive(),
        )
    )
    return finalize_result.rowcount == 1
