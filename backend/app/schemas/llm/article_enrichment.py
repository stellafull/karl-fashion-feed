"""Structured output schema for article enrichment."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from backend.app.schemas.llm.story_taxonomy import (
    MAX_ARTICLE_CATEGORIES,
    StoryCategory,
    sort_story_categories,
)


class ArticleEnrichmentSchema(BaseModel):
    should_publish: bool
    reject_reason: str | None = ""
    title_zh: str = Field(min_length=1)
    summary_zh: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    categories: list[StoryCategory] = Field(
        min_length=1,
        max_length=MAX_ARTICLE_CATEGORIES,
    )

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, value: list[StoryCategory]) -> list[StoryCategory]:
        normalized_categories = sort_story_categories(value)
        if len(normalized_categories) != len(value):
            raise ValueError("categories must not contain duplicates")
        if len(normalized_categories) > MAX_ARTICLE_CATEGORIES:
            raise ValueError(
                f"categories must contain at most {MAX_ARTICLE_CATEGORIES} items"
            )
        return normalized_categories
