"""Structured output schema for strict-story tie-break decisions."""

from __future__ import annotations

import re

from pydantic import BaseModel
from pydantic import field_validator


def _is_unreadable_mixed_alnum_noise(token: str) -> bool:
    if len(token) < 12:
        return False
    if not (any(char.isalpha() for char in token) and any(char.isdigit() for char in token)):
        return False

    transitions = 0
    previous_is_alpha = token[0].isalpha()
    for char in token[1:]:
        current_is_alpha = char.isalpha()
        if current_is_alpha != previous_is_alpha:
            transitions += 1
        previous_is_alpha = current_is_alpha
    return transitions >= 4


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

    for token in re.findall(r"[A-Za-z0-9]{12,}", normalized):
        if _is_unreadable_mixed_alnum_noise(token):
            raise ValueError("synopsis_zh contains unreadable alnum noise")

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
