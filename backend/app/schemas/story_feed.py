"""Schemas for story feed read APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StoryFeedSource(BaseModel):
    """One source article shown under a story."""

    name: str
    title: str
    link: str
    lang: str


class StoryFeedTopic(BaseModel):
    """One story item returned to the frontend feed."""

    id: str
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str
    category_name: str
    image: str
    published: str
    sources: list[StoryFeedSource] = Field(default_factory=list)
    article_count: int


class StoryFeedCategory(BaseModel):
    """One discover category option."""

    id: str
    name: str
    icon: str = ""


class StoryFeedMeta(BaseModel):
    """Story feed summary metadata."""

    generated_at: str
    total_topics: int
    total_articles: int
    sources_count: int
    sources: list[str] = Field(default_factory=list)


class StoryFeedResponse(BaseModel):
    """Full discover feed payload backed by story read models."""

    meta: StoryFeedMeta
    categories: list[StoryFeedCategory] = Field(default_factory=list)
    topics: list[StoryFeedTopic] = Field(default_factory=list)
