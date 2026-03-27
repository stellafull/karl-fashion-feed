"""Sparse event frame extraction for one parsed article."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article, ArticleEventFrame
from backend.app.models.article import _utcnow_naive
from backend.app.prompts.event_frame_extraction_prompt import build_event_frame_extraction_prompt
from backend.app.schemas.llm.event_frame_extraction import (
    EventFrameExtractionSchema,
    ExtractedEventFrame,
)
from backend.app.service.article_parse_service import ArticleMarkdownService

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class EventFrameExtractionService:
    """Extract up to three high-confidence event frames from one article."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        markdown_service: ArticleMarkdownService | None = None,
    ) -> None:
        self._client = client
        self._markdown_service = markdown_service or ArticleMarkdownService()

    async def extract_frames(self, session: Session, article: Article) -> tuple[ArticleEventFrame, ...]:
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
            article.event_frame_status = "done"
            article.event_frame_error = None
        except Exception as exc:
            article.event_frame_attempts += 1
            article.event_frame_status = "abandoned" if article.event_frame_attempts >= 3 else "failed"
            article.event_frame_error = f"{exc.__class__.__name__}: {exc}"
            article.event_frame_updated_at = _utcnow_naive()
            session.flush()
            return ()

        article.event_frame_updated_at = _utcnow_naive()
        session.flush()
        return frames

    async def _infer_frames(self, article: Article) -> EventFrameExtractionSchema:
        """Run one structured LLM extraction over parsed article markdown."""
        if not article.markdown_rel_path:
            raise ValueError(f"markdown_rel_path is required for frame extraction: {article.article_id}")

        markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
        if not markdown.strip():
            raise ValueError(f"markdown is empty for frame extraction: {article.article_id}")

        client = self._get_client()
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
        business_time = article.published_at or article.discovered_at
        return ArticleEventFrame(
            article_id=article.article_id,
            business_date=business_time.date(),
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
