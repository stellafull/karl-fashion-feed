"""LLM enrichment for collected articles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article
from backend.app.prompts.article_enrichment_prompt import ARTICLE_ENRICHMENT_PROMPT
from backend.app.schemas.llm.article_enrichment import ArticleEnrichmentSchema
from backend.app.schemas.llm.story_taxonomy import (
    MAX_ARTICLE_CATEGORIES,
    StoryCategory,
    sort_story_categories,
)
from backend.app.service.article_parse_service import ArticleMarkdownService

MAX_ENRICHMENT_ATTEMPTS = 3


@dataclass(frozen=True)
class EnrichedArticle:
    article_id: str
    title_zh: str
    summary_zh: str
    tags: tuple[str, ...]
    brands: tuple[str, ...]
    categories: tuple[StoryCategory, ...]
    cluster_text: str
    published_at: datetime | None
    ingested_at: datetime
    hero_image_url: str | None
    source_name: str


class ArticleEnrichmentService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=STORY_SUMMARIZATION_MODEL_CONFIG.api_key,
            base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
            timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
        )
        self._markdown_service = ArticleMarkdownService()

    async def enrich_article(self, session: Session, article: Article) -> bool:
        if (
            article.enrichment_status == "done"
            and bool((article.title_zh or "").strip())
            and bool((article.summary_zh or "").strip())
            and bool((article.cluster_text or "").strip())
            and self.has_valid_categories(article)
        ):
            return False
        if article.enrichment_status == "abandoned":
            return False
        if article.enrichment_attempts >= MAX_ENRICHMENT_ATTEMPTS:
            article.enrichment_status = "abandoned"
            session.flush()
            return False

        markdown = ""
        if article.markdown_rel_path:
            markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)

        try:
            response = await self._client.beta.chat.completions.parse(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=STORY_SUMMARIZATION_MODEL_CONFIG.temperature,
                response_format=ArticleEnrichmentSchema,
                messages=[
                    {"role": "system", "content": ARTICLE_ENRICHMENT_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "article_id": article.article_id,
                                "source_name": article.source_name,
                                "source_lang": article.source_lang,
                                "category": article.category,
                                "canonical_url": article.canonical_url,
                                "title_raw": article.title_raw,
                                "summary_raw": article.summary_raw,
                                "markdown": markdown,
                            },
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        ),
                    },
                ],
            )
            result = response.choices[0].message.parsed
            if result is None:
                raise ValueError("article enrichment response missing parsed payload")

            title_zh = result.title_zh.strip()
            summary_zh = result.summary_zh.strip()
            if not title_zh:
                raise ValueError(f"article enrichment title_zh is empty: {article.article_id}")
            if not summary_zh:
                raise ValueError(f"article enrichment summary_zh is empty: {article.article_id}")

            tags: list[str] = []
            seen_tags: set[str] = set()
            for raw in result.tags:
                value = raw.strip()
                if not value:
                    continue
                lowered = value.casefold()
                if lowered in seen_tags:
                    continue
                seen_tags.add(lowered)
                tags.append(value)

            brands: list[str] = []
            seen_brands: set[str] = set()
            for raw in result.brands:
                value = raw.strip()
                if not value:
                    continue
                lowered = value.casefold()
                if lowered in seen_brands:
                    continue
                seen_brands.add(lowered)
                brands.append(value)

            categories = sort_story_categories(result.categories)
            if not categories:
                raise ValueError(
                    f"article enrichment categories are empty: {article.article_id}"
                )
            reject_reason = (result.reject_reason or "").strip()

            article.should_publish = result.should_publish
            article.reject_reason = "" if result.should_publish else reject_reason
            article.title_zh = title_zh
            article.summary_zh = summary_zh
            article.tags_json = tags
            article.brands_json = brands
            article.categories_json = categories
            article.cluster_text = "\n".join(
                part
                for part in (
                    title_zh,
                    summary_zh,
                    " ".join(tags),
                    " ".join(brands),
                    " ".join(categories),
                    article.source_name.strip(),
                )
                if part
            )
            article.enrichment_status = "done"
            article.enrichment_attempts = 0
            article.enriched_at = datetime.now(UTC).replace(tzinfo=None)
            article.enrichment_error = None
            session.flush()
        except Exception as exc:
            article.enrichment_attempts += 1
            if article.enrichment_attempts >= MAX_ENRICHMENT_ATTEMPTS:
                article.enrichment_status = "abandoned"
            else:
                article.enrichment_status = "failed"
            article.enriched_at = datetime.now(UTC).replace(tzinfo=None)
            article.enrichment_error = f"{exc.__class__.__name__}: {exc}"
            session.flush()
            return False

        return True

    @staticmethod
    def has_valid_categories(article: Article) -> bool:
        """Return whether the persisted article categories are valid."""
        if not isinstance(article.categories_json, list):
            return False

        raw_categories = [
            str(item).strip() for item in article.categories_json if str(item).strip()
        ]
        if not raw_categories:
            return False

        normalized_categories = sort_story_categories(raw_categories)
        return (
            len(normalized_categories) == len(raw_categories)
            and len(normalized_categories) <= MAX_ARTICLE_CATEGORIES
        )

    @staticmethod
    def to_record(article: Article) -> EnrichedArticle:
        title_zh = (article.title_zh or "").strip()
        summary_zh = (article.summary_zh or "").strip()
        cluster_text = (article.cluster_text or "").strip()
        if not title_zh:
            raise ValueError(f"missing title_zh for enriched article: {article.article_id}")
        if not summary_zh:
            raise ValueError(f"missing summary_zh for enriched article: {article.article_id}")
        if not cluster_text:
            raise ValueError(f"missing cluster_text for enriched article: {article.article_id}")

        tags = ()
        if isinstance(article.tags_json, list):
            tags = tuple(str(item).strip() for item in article.tags_json if str(item).strip())

        brands = ()
        if isinstance(article.brands_json, list):
            brands = tuple(str(item).strip() for item in article.brands_json if str(item).strip())

        if not ArticleEnrichmentService.has_valid_categories(article):
            raise ValueError(f"missing categories for enriched article: {article.article_id}")
        categories = tuple(
            sort_story_categories(
                str(item).strip()
                for item in article.categories_json
                if str(item).strip()
            )
        )

        return EnrichedArticle(
            article_id=article.article_id,
            title_zh=title_zh,
            summary_zh=summary_zh,
            tags=tags,
            brands=brands,
            categories=categories,
            cluster_text=cluster_text,
            published_at=article.published_at,
            ingested_at=article.ingested_at,
            hero_image_url=article.image_url,
            source_name=article.source_name,
        )
