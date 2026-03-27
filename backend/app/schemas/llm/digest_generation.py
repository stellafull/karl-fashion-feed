"""Structured output schema for digest generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DigestPlan(BaseModel):
    facet: str = Field(min_length=1)
    strict_story_keys: list[str] = Field(min_length=1)
    title_zh: str = Field(min_length=1)
    dek_zh: str = Field(default="")
    body_markdown: str = Field(min_length=1)


class DigestGenerationSchema(BaseModel):
    digests: list[DigestPlan] = Field(default_factory=list)
