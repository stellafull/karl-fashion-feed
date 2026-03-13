"""Structured output schema for image analysis."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImageAnalysisSchema(BaseModel):
    image_id: str
    observed_description: str = ""
    ocr_text: str = ""
    visible_entities: list[str] = Field(default_factory=list)
    style_signals: list[str] = Field(default_factory=list)
    contextual_interpretation: str = ""
    context_used: list[str] = Field(default_factory=list)
    confidence: float | None = None
