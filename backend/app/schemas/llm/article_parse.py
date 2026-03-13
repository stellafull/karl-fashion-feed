"""Structured output schema for article parsing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ParsedMarkdownBlock(BaseModel):
    kind: Literal["heading", "paragraph", "list_item", "blockquote", "image"]
    text: str = ""
    image_key: str | None = None


class ParsedImageReference(BaseModel):
    image_key: str
    source_url: str
    role: Literal["hero", "inline", "gallery", "og", "twitter"] = "inline"
    alt_text: str = ""
    caption_raw: str = ""
    credit_raw: str = ""
    context_snippet: str = ""


class ArticleParseSchema(BaseModel):
    title_raw: str = Field(min_length=1)
    summary_raw: str = ""
    markdown_blocks: list[ParsedMarkdownBlock] = Field(default_factory=list)
    images: list[ParsedImageReference] = Field(default_factory=list)
    hero_image_key: str | None = None
