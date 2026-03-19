"""Source configuration loader for RSS and web collectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


SOURCE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "sources.yaml"

DEFAULT_TITLE_SELECTORS = (
    "h1",
)
DEFAULT_CONTENT_SELECTORS = (
    "article",
    "[itemprop='articleBody']",
    "main",
    ".article-body",
    ".entry-content",
    ".post-content",
    ".content-body",
)
DEFAULT_PUBLISHED_SELECTORS = (
    "time[datetime]",
    "meta[property='article:published_time']",
    "meta[name='pubdate']",
    "meta[name='parsely-pub-date']",
)
DEFAULT_IMAGE_SELECTORS = (
    "meta[property='og:image']",
    "meta[name='twitter:image']",
    "article img",
)


@dataclass(frozen=True)
class DiscoveryConfig:
    link_selectors: tuple[str, ...] = ("a[href]",)
    article_url_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    pagination_selectors: tuple[str, ...] = ()
    max_pages: int = 1


@dataclass(frozen=True)
class DetailConfig:
    title_selectors: tuple[str, ...] = DEFAULT_TITLE_SELECTORS
    content_selectors: tuple[str, ...] = DEFAULT_CONTENT_SELECTORS
    published_selectors: tuple[str, ...] = DEFAULT_PUBLISHED_SELECTORS
    image_selectors: tuple[str, ...] = DEFAULT_IMAGE_SELECTORS
    remove_selectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: Literal["rss", "web"]
    lang: str
    category: str
    enabled: bool = True
    requires_js: bool = False
    max_articles: int = 30
    feed_url: str | None = None
    start_urls: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    detail: DetailConfig = field(default_factory=DetailConfig)
    detail_concurrency: int = 4


def load_source_configs(
    path: str | Path = SOURCE_CONFIG_PATH,
    *,
    include_disabled: bool = False,
) -> list[SourceConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ValueError("sources.yaml must contain a top-level list of sources")

    configs = [_parse_source_config(item) for item in raw]
    if include_disabled:
        return configs
    return [config for config in configs if config.enabled]


def _parse_source_config(item: Any) -> SourceConfig:
    if not isinstance(item, dict):
        raise ValueError("each source entry must be a mapping")

    source_type = str(item.get("type", "rss")).strip().lower()
    if source_type == "crawl":
        source_type = "web"
    if source_type not in {"rss", "web"}:
        raise ValueError(f"unsupported source type: {source_type}")

    name = _required_str(item, "name")
    lang = _required_str(item, "lang")
    category = _required_str(item, "category")
    enabled = bool(item.get("enabled", True))
    requires_js = bool(item.get("requires_js", False))
    max_articles = int(item.get("max_articles", 30))
    detail_concurrency = int(item.get("detail_concurrency", 4))

    if source_type == "rss":
        feed_url = _required_str(item, "feed_url", fallback_key="url")
        return SourceConfig(
            name=name,
            type="rss",
            lang=lang,
            category=category,
            enabled=enabled,
            requires_js=requires_js,
            max_articles=max_articles,
            feed_url=feed_url,
            detail=_parse_detail_config(item.get("detail") or {}),
            detail_concurrency=detail_concurrency,
        )

    start_urls = tuple(_string_list(item.get("start_urls")))
    allowed_domains = tuple(_string_list(item.get("allowed_domains")))
    if not start_urls:
        raise ValueError(f"web source {name} must define start_urls")
    if not allowed_domains:
        raise ValueError(f"web source {name} must define allowed_domains")

    return SourceConfig(
        name=name,
        type="web",
        lang=lang,
        category=category,
        enabled=enabled,
        requires_js=requires_js,
        max_articles=max_articles,
        start_urls=start_urls,
        allowed_domains=allowed_domains,
        discovery=_parse_discovery_config(item.get("discovery") or {}),
        detail=_parse_detail_config(item.get("detail") or {}),
        detail_concurrency=detail_concurrency,
    )


def _parse_discovery_config(raw: dict[str, Any]) -> DiscoveryConfig:
    return DiscoveryConfig(
        link_selectors=tuple(_string_list(raw.get("link_selectors")) or ("a[href]",)),
        article_url_patterns=tuple(_string_list(raw.get("article_url_patterns"))),
        exclude_patterns=tuple(_string_list(raw.get("exclude_patterns"))),
        pagination_selectors=tuple(_string_list(raw.get("pagination_selectors"))),
        max_pages=max(int(raw.get("max_pages", 1)), 1),
    )


def _parse_detail_config(raw: dict[str, Any]) -> DetailConfig:
    return DetailConfig(
        title_selectors=tuple(
            _string_list(raw.get("title_selectors")) or DEFAULT_TITLE_SELECTORS
        ),
        content_selectors=tuple(
            _string_list(raw.get("content_selectors")) or DEFAULT_CONTENT_SELECTORS
        ),
        published_selectors=tuple(
            _string_list(raw.get("published_selectors")) or DEFAULT_PUBLISHED_SELECTORS
        ),
        image_selectors=tuple(
            _string_list(raw.get("image_selectors")) or DEFAULT_IMAGE_SELECTORS
        ),
        remove_selectors=tuple(_string_list(raw.get("remove_selectors"))),
    )


def _required_str(
    item: dict[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
) -> str:
    value = item.get(key)
    if value is None and fallback_key is not None:
        value = item.get(fallback_key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"source entry must define non-empty {key}")
    return value.strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]
