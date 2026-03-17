"""Structured output schema for article enrichment."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArticleEnrichmentSchema(BaseModel):
    should_publish: bool
    reject_reason: str | None = ""
    title_zh: str = Field(min_length=1)
    summary_zh: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    category_candidates: list[str] = Field(default_factory=list)
