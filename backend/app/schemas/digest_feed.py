"""Schemas for public digest feed APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DigestFeedItem(BaseModel):
    id: str
    facet: str
    title: str
    dek: str
    image: str
    published: str
    article_count: int
    source_count: int
    source_names: list[str] = Field(default_factory=list)


class DigestFeedResponse(BaseModel):
    digests: list[DigestFeedItem] = Field(default_factory=list)


class DigestDetailSource(BaseModel):
    name: str
    title: str
    link: str
    lang: str


class DigestDetailResponse(BaseModel):
    id: str
    facet: str
    title: str
    dek: str
    body_markdown: str
    hero_image: str
    published: str
    sources: list[DigestDetailSource] = Field(default_factory=list)
