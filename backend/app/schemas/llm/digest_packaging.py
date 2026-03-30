"""Structured output schema for digest packaging."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DigestPackagingPlan(BaseModel):
    """Plan for a single digest package."""

    facet: str = Field(min_length=1)
    story_keys: list[str] = Field(min_length=1)
    article_ids: list[str] = Field(min_length=1)
    editorial_angle: str = Field(min_length=1)
    title_zh: str = Field(min_length=1)
    dek_zh: str = Field(min_length=1)


class DigestPackagingSchema(BaseModel):
    """Top-level structured output for digest packaging."""

    digests: list[DigestPackagingPlan] = Field(default_factory=list)
