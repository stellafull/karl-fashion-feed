"""Persist durable Chinese article materials without publish gating."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG, ModelConfig
from backend.app.models import Article
from backend.app.prompts.article_normalization_prompt import build_article_normalization_prompt
from backend.app.schemas.llm.article_normalization import ArticleNormalizationSchema
from backend.app.service.article_parse_service import ArticleMarkdownService
from backend.app.service.business_day_service import business_day_for_ingested_at


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ArticleNormalizationService:
    """Generate and persist normalized Chinese article materials."""

    def __init__(
        self,
        *,
        markdown_service: ArticleMarkdownService | None = None,
        client: AsyncOpenAI | None = None,
        model_config: ModelConfig = STORY_SUMMARIZATION_MODEL_CONFIG,
    ) -> None:
        self._markdown_service = markdown_service or ArticleMarkdownService()
        self._model_config = model_config
        self._client = client or AsyncOpenAI(
            api_key=model_config.api_key,
            base_url=model_config.base_url,
            timeout=model_config.timeout_seconds,
        )

    async def normalize_article(self, session: Session, article: Article) -> bool:
        """Normalize one parsed article into durable Chinese materials."""
        if article.parse_status != "done":
            raise ValueError(f"parse must be done before normalization: {article.article_id}")
        if article.normalization_attempts >= 3:
            article.normalization_status = "abandoned"
            article.normalization_updated_at = _utcnow_naive()
            session.flush()
            return False

        try:
            result = await self._infer_normalized_material(article)
            article.title_zh = _require_text(result.title_zh, field_name="title_zh")
            article.summary_zh = _require_text(result.summary_zh, field_name="summary_zh")
            article.body_zh_rel_path = self._write_body_markdown(article, result.body_zh)
            article.normalization_status = "done"
            article.normalization_error = None
        except Exception as exc:
            article.normalization_attempts += 1
            article.normalization_status = "abandoned" if article.normalization_attempts >= 3 else "failed"
            article.normalization_error = f"{exc.__class__.__name__}: {exc}"
            article.normalization_updated_at = _utcnow_naive()
            session.flush()
            return False

        article.normalization_updated_at = _utcnow_naive()
        session.flush()
        return True

    async def _infer_normalized_material(self, article: Article) -> ArticleNormalizationSchema:
        if not article.markdown_rel_path:
            raise ValueError(f"markdown_rel_path is required for normalization: {article.article_id}")

        markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
        prompt = build_article_normalization_prompt(
            source_name=article.source_name,
            source_lang=article.source_lang,
            canonical_url=article.canonical_url,
            business_day=business_day_for_ingested_at(article.ingested_at),
            markdown=markdown,
        )
        response = await self._client.chat.completions.create(
            model=self._model_config.model_name,
            temperature=self._model_config.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
        content = _extract_message_content(response.choices[0].message.content)
        if not content:
            raise ValueError(f"normalization response was empty for article: {article.article_id}")
        return ArticleNormalizationSchema.model_validate_json(content)

    def _write_body_markdown(self, article: Article, body_zh: str) -> str:
        relative_path = self._build_body_relative_path(article)
        content = _normalize_markdown_body(body_zh)
        self._markdown_service.write_markdown(relative_path=relative_path, content=content)
        return relative_path

    def _build_body_relative_path(self, article: Article) -> str:
        business_day = business_day_for_ingested_at(article.ingested_at).isoformat()
        return str(Path("normalized") / business_day / f"{article.article_id}.md")


def _require_text(value: str, *, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be blank")
    return text


def _normalize_markdown_body(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("body_zh must not be blank")
    return f"{text}\n"


def _extract_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if getattr(item, "type", None) == "text":
                text = getattr(item, "text", "")
                if text:
                    text_parts.append(text)
        return "".join(text_parts)
    return ""
