"""Structured output schema for story facet assignment."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StoryFacetAssignment(BaseModel):
    """Facet assignment for a single story."""

    story_key: str = Field(min_length=1)
    facets: list[str]


class FacetAssignmentSchema(BaseModel):
    """Top-level structured output for facet assignment."""

    stories: list[StoryFacetAssignment] = Field(default_factory=list)
