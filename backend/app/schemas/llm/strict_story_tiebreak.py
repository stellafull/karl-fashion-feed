"""Structured output schema for strict-story tie-break decisions."""

from __future__ import annotations

from pydantic import BaseModel


class StrictStoryTieBreakChoice(BaseModel):
    """Model-selected key reuse choice for one strict-story candidate group."""

    reuse_strict_story_key: str | None = None
    synopsis_zh: str


class StrictStoryTieBreakSchema(BaseModel):
    """Top-level tie-break schema payload."""

    choice: StrictStoryTieBreakChoice

