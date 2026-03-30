"""Structured output schema for story cluster judgment."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

EventType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StoryClusterGroup(BaseModel):
    """One judged story cluster group."""

    seed_event_frame_id: str = Field(min_length=1)
    member_event_frame_ids: list[str] = Field(min_length=1)
    synopsis_zh: str = Field(min_length=1)
    event_type: EventType
    anchor_json: dict = Field(default_factory=dict)


class StoryClusterJudgmentSchema(BaseModel):
    """Top-level structured output for story cluster judgment."""

    groups: list[StoryClusterGroup] = Field(default_factory=list)
