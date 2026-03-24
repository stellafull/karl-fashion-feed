"""Shared story taxonomy contracts for LLM structured outputs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal


StoryCategory = Literal[
    "品牌/市场",
    "时尚趋势",
    "秀场/系列",
    "街拍/造型",
]

ALLOWED_STORY_CATEGORIES: tuple[StoryCategory, ...] = (
    "品牌/市场",
    "时尚趋势",
    "秀场/系列",
    "街拍/造型",
)

MAX_ARTICLE_CATEGORIES = 2
STORY_CATEGORY_ORDER = {
    category: index for index, category in enumerate(ALLOWED_STORY_CATEGORIES)
}


def sort_story_categories(categories: Iterable[str]) -> list[StoryCategory]:
    """Return unique story categories in canonical order."""
    normalized_categories = {
        category
        for category in categories
        if category in STORY_CATEGORY_ORDER
    }
    return sorted(
        normalized_categories,
        key=lambda category: STORY_CATEGORY_ORDER[category],
    )
