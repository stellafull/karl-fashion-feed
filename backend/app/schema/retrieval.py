"""Small Pydantic models for retrieval payloads."""

from __future__ import annotations

import time

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

TEXT_CHUNK_UNIT_TYPE = "text_chunk"

DEFAULT_TEXT_OUTPUT_FIELDS = (
    "unit_id",
    "article_id",
    "source_id",
    "unit_type",
    "chunk_index",
    "title",
    "text_content",
    "source_url",
    "author",
    "domain",
    "language",
    "published_at_ts",
    "is_active",
    "tags",
    "metadata",
)


class RetrievalSchemaModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class TextRetrievalUnit(RetrievalSchemaModel):
    unit_id: str = Field(min_length=1)
    article_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    chunk_index: int
    text: str = Field(
        min_length=1,
        validation_alias=AliasChoices("text", "text_content", "content_text"),
        serialization_alias="text_content",
    )
    source_url: str = Field(min_length=1)
    title: str | None = None
    content_version_hash: str | None = None
    unit_type: str = TEXT_CHUNK_UNIT_TYPE
    author: str | None = None
    domain: str | None = None
    language: str | None = None
    published_at_ts: int | None = None
    is_active: bool = True
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at_ts: int = Field(default_factory=lambda: int(time.time()))
    updated_at_ts: int = Field(default_factory=lambda: int(time.time()))


class RetrievalIngestionStats(RetrievalSchemaModel):
    document_count: int
    skipped_count: int
    chunk_count: int
    existing_count: int
    inserted_count: int


class SearchResultItem(RetrievalSchemaModel):
    """Canonical API shape for retrieval search results."""

    unit_id: str = Field(min_length=1)
    article_id: str = Field(min_length=1)
    source_id: str | None = None
    unit_type: str = TEXT_CHUNK_UNIT_TYPE
    chunk_index: int | None = None
    title: str | None = None
    text_content: str = Field(
        min_length=1,
        validation_alias=AliasChoices("text_content", "text", "content_text"),
    )
    source_url: str | None = None
    score: float
    metadata: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "DEFAULT_TEXT_OUTPUT_FIELDS",
    "RetrievalIngestionStats",
    "SearchResultItem",
    "TEXT_CHUNK_UNIT_TYPE",
    "TextRetrievalUnit",
]
