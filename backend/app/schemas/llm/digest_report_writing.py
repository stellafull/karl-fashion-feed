"""Structured output schema for digest report writing."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DigestReportWritingSchema(BaseModel):
    """Top-level structured output for digest report writing."""

    title_zh: str = Field(min_length=1)
    dek_zh: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    source_article_ids: list[str] = Field(min_length=1)
