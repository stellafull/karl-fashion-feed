"""LLM enrichment for collected articles."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article
from backend.app.prompts.article_enrichment_prompt import ARTICLE_ENRICHMENT_PROMPT
from backend.app.schemas.llm.article_enrichment import ArticleEnrichmentSchema
from backend.app.service.article_markdown_service import ArticleMarkdownService
from backend.app.service.story_pipeline_contracts import EnrichedArticleRecord


@dataclass(frozen=True)
class ArticleEnrichmentInput:
    article_id: str
    source_name: str
    source_lang: str
    category: str
    canonical_url: str
    title_raw: str
    summary_raw: str
    markdown: str


class ArticleEnrichmentService:
    def __init__(
        self,
        *,
        client: Any | None = None,
        markdown_service: ArticleMarkdownService | None = None,
    ) -> None:
        api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
        if client is None and not api_key:
            raise ValueError(f"missing API key for {STORY_SUMMARIZATION_MODEL_CONFIG.model_name}")
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
            timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
        )
        self._markdown_service = markdown_service or ArticleMarkdownService()

    async def enrich_article(self, session: Session, article: Article) -> bool:
        if self.is_complete(article):
            return False

        payload = self.build_input(article)
        try:
            result = await self.infer_payload(payload)
        except Exception as exc:
            self.apply_failure(article=article, error=exc)
            raise

        self.apply_result(article=article, result=result)
        session.flush()
        return True

    def build_input(self, article: Article) -> ArticleEnrichmentInput:
        markdown = ""
        if article.markdown_rel_path:
            markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)

        return ArticleEnrichmentInput(
            article_id=article.article_id,
            source_name=article.source_name,
            source_lang=article.source_lang,
            category=article.category,
            canonical_url=article.canonical_url,
            title_raw=article.title_raw,
            summary_raw=article.summary_raw,
            markdown=markdown,
        )

    def build_messages(self, payload: ArticleEnrichmentInput) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": ARTICLE_ENRICHMENT_PROMPT},
            {"role": "user", "content": _render_json_payload(asdict(payload))},
        ]

    async def infer_payload(self, payload: ArticleEnrichmentInput) -> ArticleEnrichmentSchema:
        response = await self._client.beta.chat.completions.parse(
            model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
            temperature=STORY_SUMMARIZATION_MODEL_CONFIG.temperature,
            response_format=ArticleEnrichmentSchema,
            messages=self.build_messages(payload),
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError("article enrichment response missing parsed payload")
        return parsed

    async def infer_many(
        self,
        payloads: list[ArticleEnrichmentInput],
    ) -> list[ArticleEnrichmentSchema | Exception]:
        if not payloads:
            return []
        return await asyncio.gather(
            *(self.infer_payload(payload) for payload in payloads),
            return_exceptions=True,
        )

    def apply_result(
        self,
        *,
        article: Article,
        result: ArticleEnrichmentSchema,
    ) -> None:
        tags = _dedupe_strings(result.tags)
        brands = _dedupe_strings(result.brands)
        category_candidates = _dedupe_strings(result.category_candidates) or [article.category]
        should_publish, reject_reason = self.normalize_publish_decision(article=article, result=result)

        article.should_publish = should_publish
        article.reject_reason = reject_reason
        article.title_zh = result.title_zh.strip()
        article.summary_zh = result.summary_zh.strip()
        article.tags_json = tags
        article.brands_json = brands
        article.category_candidates_json = category_candidates
        article.cluster_text = self.build_cluster_text(
            title_zh=article.title_zh,
            summary_zh=article.summary_zh,
            tags=tags,
            brands=brands,
            category_candidates=category_candidates,
            source_name=article.source_name,
        )
        article.enrichment_status = "done"
        article.enriched_at = _utcnow_naive()
        article.enrichment_error = None

    @staticmethod
    def apply_failure(*, article: Article, error: Exception) -> None:
        article.enrichment_status = "failed"
        article.enriched_at = _utcnow_naive()
        article.enrichment_error = f"{error.__class__.__name__}: {error}"

    @staticmethod
    def build_cluster_text(
        *,
        title_zh: str | None,
        summary_zh: str | None,
        tags: list[str] | tuple[str, ...],
        brands: list[str] | tuple[str, ...],
        category_candidates: list[str] | tuple[str, ...],
        source_name: str,
    ) -> str:
        parts = [
            (title_zh or "").strip(),
            (summary_zh or "").strip(),
            " ".join(tag.strip() for tag in tags if tag.strip()),
            " ".join(brand.strip() for brand in brands if brand.strip()),
            " ".join(category.strip() for category in category_candidates if category.strip()),
            source_name.strip(),
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def to_record(article: Article) -> EnrichedArticleRecord:
        return EnrichedArticleRecord(
            article_id=article.article_id,
            title_zh=(article.title_zh or article.title_raw).strip(),
            summary_zh=(article.summary_zh or article.summary_raw or article.title_raw).strip(),
            tags=tuple(_coerce_string_list(article.tags_json)),
            brands=tuple(_coerce_string_list(article.brands_json)),
            category_candidates=tuple(_coerce_string_list(article.category_candidates_json)),
            cluster_text=(article.cluster_text or "").strip(),
            published_at=article.published_at,
            ingested_at=article.ingested_at,
            hero_image_url=article.image_url,
            source_name=article.source_name,
        )

    @staticmethod
    def is_complete(article: Article) -> bool:
        return _is_enrichment_complete(article)

    @staticmethod
    def normalize_publish_decision(
        *,
        article: Article,
        result: ArticleEnrichmentSchema,
    ) -> tuple[bool, str]:
        del article
        if result.should_publish:
            return True, ""

        reject_reason = (result.reject_reason or "").strip()
        if not reject_reason:
            return False, ""

        lowered_reject_reason = reject_reason.casefold()
        if any(marker in lowered_reject_reason for marker in _HARD_REJECT_REASON_MARKERS):
            return False, reject_reason

        if _looks_like_legacy_over_rejection(reject_reason=reject_reason):
            return True, ""

        return False, reject_reason


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(value)
    return deduped


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_enrichment_complete(article: Article) -> bool:
    return (
        article.enrichment_status == "done"
        and bool((article.title_zh or "").strip())
        and bool((article.summary_zh or "").strip())
        and bool((article.cluster_text or "").strip())
    )


_HARD_REJECT_REASON_MARKERS = (
    "虚构",
    "不符合事实",
    "广告",
    "软文",
    "低质",
    "seo",
    "博彩",
    "成人",
    "违法",
    "诈骗",
    "仇恨",
    "暴力",
    "无法形成有效摘要",
    "信息极少",
)

_LEGACY_OVER_REJECT_REASON_MARKERS = (
    "中国区同事",
    "中国市场",
    "中国读者",
    "中国区读者",
    "阅读兴趣",
    "市场关联度",
    "关联度较低",
    "缺乏与中国相关",
    "海外平台",
    "亚马逊",
    "购物推荐",
    "购物指南",
    "不适合作为时尚资讯",
    "英国王室",
    "供应链危机",
    "敏感政治经济",
    "生活方式吸引力",
)


def _looks_like_legacy_over_rejection(*, reject_reason: str) -> bool:
    return any(marker in reject_reason for marker in _LEGACY_OVER_REJECT_REASON_MARKERS)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _render_json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
