"""Structured output schema for durable article normalization."""

from __future__ import annotations

from pydantic import BaseModel


class ArticleNormalizationSchema(BaseModel):
    """Durable Chinese materials generated from one parsed article."""

    title_zh: str
    summary_zh: str
    body_zh: str
