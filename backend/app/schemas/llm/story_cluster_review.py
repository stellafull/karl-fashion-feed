"""Structured output schema for cluster review."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StoryClusterGroupSchema(BaseModel):
    article_ids: list[str] = Field(min_length=1)
    rationale: str = ""


class StoryClusterReviewSchema(BaseModel):
    groups: list[StoryClusterGroupSchema] = Field(default_factory=list)
