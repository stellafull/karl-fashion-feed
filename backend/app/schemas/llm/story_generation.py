"""Structured output schema for story generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StoryGenerationSchema(BaseModel):
    title_zh: str = Field(min_length=1)
    summary_zh: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
