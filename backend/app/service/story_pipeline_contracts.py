"""Shared contracts for daily story pipeline services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class EnrichedArticleRecord:
    article_id: str
    title_zh: str
    summary_zh: str
    tags: tuple[str, ...]
    brands: tuple[str, ...]
    category_candidates: tuple[str, ...]
    cluster_text: str
    published_at: datetime | None
    ingested_at: datetime
    hero_image_url: str | None
    source_name: str


@dataclass(frozen=True)
class EmbeddedArticle:
    article: EnrichedArticleRecord
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class StoryDraft:
    title_zh: str
    summary_zh: str
    key_points: tuple[str, ...]
    tags: tuple[str, ...]
    category: str
    article_ids: tuple[str, ...]
    hero_image_url: str | None
    source_article_count: int


@dataclass(frozen=True)
class DailyPipelineResult:
    run_id: str
    candidates: int
    enriched: int
    published: int
    stories_created: int
    watermark_ingested_at: datetime | None
    story_date: date | None = None
    story_grouping_mode: str = "incremental_ingested_at"
    stages_completed: tuple[str, ...] = field(default_factory=tuple)
    stages_skipped: tuple[str, ...] = field(default_factory=tuple)
    skipped_existing_enrichment: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
