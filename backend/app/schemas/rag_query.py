"""Pydantic contracts for retrieval-core query planning and results."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


PlanType = Literal["text_only", "image_only", "fusion"]
REQUEST_IMAGE_REF = "request_image"


class TimeRange(BaseModel):
    """Explicit ingestion-time range applied during retrieval recall."""

    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "TimeRange":
        if self.start_at is not None and self.end_at is not None and self.start_at >= self.end_at:
            raise ValueError("time_range.start_at must be earlier than time_range.end_at")
        return self


class QueryFilters(BaseModel):
    """Structured metadata filters for shared-collection retrieval."""

    source_names: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    time_range: TimeRange | None = None


class QueryPlan(BaseModel):
    """Deterministic retrieval plan produced by tool dispatch."""

    plan_type: PlanType
    text_query: str | None = None
    image_query: str | None = None
    filters: QueryFilters = Field(default_factory=QueryFilters)
    output_goal: str = "reference_lookup"
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("text_query", "image_query", mode="before")
    @classmethod
    def normalize_optional_query(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("query values must be strings")
        normalized_value = value.strip()
        return normalized_value or None

    @model_validator(mode="after")
    def validate_required_queries(self) -> "QueryPlan":
        has_text_query = self.text_query is not None
        has_image_query = self.image_query is not None

        if self.plan_type == "text_only":
            if not has_text_query:
                raise ValueError("text_only plan requires non-empty text_query")
            if has_image_query:
                raise ValueError("text_only plan does not accept image_query")

        if self.plan_type == "image_only" and has_text_query == has_image_query:
            raise ValueError("image_only plan requires exactly one of text_query or image_query")

        if self.plan_type == "fusion":
            if not has_text_query:
                raise ValueError("fusion plan requires non-empty text_query")

        return self


class CitationLocator(BaseModel):
    """Stable locator back to the article or article_image truth source."""

    article_id: str
    article_image_id: str | None = None
    chunk_index: int | None = None
    source_name: str
    canonical_url: str


class GroundingText(BaseModel):
    """Grounding text snippet attached to image hits."""

    chunk_index: int
    content: str
    citation_locator: CitationLocator


class RetrievalHit(BaseModel):
    """One retrieval evidence unit returned from a retrieval lane."""

    retrieval_unit_id: str
    modality: Literal["text", "image"]
    article_id: str
    article_image_id: str | None = None
    content: str
    score: float
    citation_locator: CitationLocator
    source_url: str | None = None
    caption_raw: str | None = None
    alt_text: str | None = None
    credit_raw: str | None = None
    context_snippet: str | None = None
    title: str | None = None
    summary: str | None = None
    grounding_texts: list[GroundingText] = Field(default_factory=list)


class ArticlePackage(BaseModel):
    """Article-level aggregation of text and image evidence."""

    article_id: str
    title: str | None = None
    summary: str | None = None
    text_hits: list[RetrievalHit] = Field(default_factory=list)
    image_hits: list[RetrievalHit] = Field(default_factory=list)
    combined_score: float


class QueryResult(BaseModel):
    """Structured retrieval result returned to higher-level tools."""

    query_plan: QueryPlan
    text_results: list[RetrievalHit] = Field(default_factory=list)
    image_results: list[RetrievalHit] = Field(default_factory=list)
    packages: list[ArticlePackage] = Field(default_factory=list)
    citation_locators: list[CitationLocator] = Field(default_factory=list)
