"""Structured output schema for sparse event frame extraction."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

EventType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ExtractedEventFrame(BaseModel):
    """One extracted event frame from a single truth-source article."""

    event_type: EventType
    subject_json: dict = Field(default_factory=dict)
    action_text: str = ""
    object_text: str = ""
    place_text: str | None = None
    collection_text: str | None = None
    season_text: str | None = None
    show_context_text: str | None = None
    evidence_json: list[dict] = Field(default_factory=list)
    signature_json: dict = Field(default_factory=dict)
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class EventFrameExtractionSchema(BaseModel):
    """Top-level structured output for article event frame extraction."""

    frames: list[ExtractedEventFrame] = Field(default_factory=list)
