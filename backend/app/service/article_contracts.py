"""Shared contracts for article collection, parsing, and storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


MarkdownBlockKind = Literal["heading", "paragraph", "list_item", "blockquote"]
ImageRole = Literal["hero", "inline", "gallery", "og", "twitter"]


@dataclass(frozen=True)
class MarkdownBlock:
    kind: MarkdownBlockKind
    text: str = ""


@dataclass
class CollectedImage:
    source_url: str
    normalized_url: str
    role: ImageRole = "inline"
    position: int = 0
    alt_text: str = ""
    caption_raw: str = ""
    credit_raw: str = ""
    source_kind: str = ""
    source_selector: str = ""
    context_snippet: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectedArticle:
    """Lightweight article seed discovered during collection."""

    source_name: str
    source_type: str
    lang: str
    category: str
    url: str
    canonical_url: str
    title: str
    summary: str
    published_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedArticle:
    """Fully parsed article detail used by the parser stage."""

    title: str
    summary: str
    markdown_blocks: tuple[MarkdownBlock, ...]
    images: tuple[CollectedImage, ...]
    published_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceCollectionResult:
    source_name: str
    source_type: str
    articles: list[CollectedArticle] = field(default_factory=list)
    error: Exception | None = None
