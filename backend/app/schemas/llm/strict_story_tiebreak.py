"""Structured output schema for strict-story tie-break decisions."""

from __future__ import annotations

import re

from pydantic import BaseModel
from pydantic import field_validator


def normalize_readable_synopsis_zh(value: str) -> str:
    """Normalize and validate readable Chinese short synopsis text."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("synopsis_zh cannot be blank")
    if any(marker in normalized for marker in ("{", "}", "[", "]", "`")):
        raise ValueError("synopsis_zh must be plain Chinese synopsis text")

    chinese_count = sum(1 for char in normalized if "\u4e00" <= char <= "\u9fff")
    if chinese_count == 0:
        raise ValueError("synopsis_zh must contain Chinese characters")
    if chinese_count < 4:
        raise ValueError("synopsis_zh must contain enough readable Chinese text")

    ascii_alnum_count = sum(1 for char in normalized if char.isascii() and char.isalnum())
    if ascii_alnum_count > chinese_count:
        raise ValueError("synopsis_zh contains too much non-Chinese noise")

    compact = re.sub(r"\s+", "", normalized)
    if len(compact) < 6 or len(compact) > 80:
        raise ValueError("synopsis_zh length is outside readable short-synopsis range")

    return normalized


class StrictStoryTieBreakChoice(BaseModel):
    """Model-selected key reuse choice for one strict-story candidate group."""

    reuse_strict_story_key: str | None = None
    synopsis_zh: str

    @field_validator("synopsis_zh")
    @classmethod
    def validate_synopsis_zh(cls, value: str) -> str:
        return normalize_readable_synopsis_zh(value)


class StrictStoryTieBreakSchema(BaseModel):
    """Top-level tie-break schema payload."""

    choice: StrictStoryTieBreakChoice
