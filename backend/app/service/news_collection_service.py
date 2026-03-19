"""Collect article seeds and parse article detail into text markdown + images."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from io import BytesIO
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import aiohttp
import feedparser
import numpy as np
import trafilatura
from bs4 import BeautifulSoup, NavigableString, Tag
from PIL import Image

try:
    from readability import Document as ReadabilityDocument
except ImportError:  # pragma: no cover - dependency enforced at runtime
    ReadabilityDocument = None

from backend.app.config.source_config import (
    DetailConfig,
    SourceConfig,
    load_source_configs,
)
from backend.app.service.article_contracts import (
    CollectedArticle,
    CollectedImage,
    MarkdownBlock,
    ParsedArticle,
    SourceCollectionResult,
)


QUERY_PARAM_BLOCKLIST = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "spm",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
SKIP_TAGS = {"script", "style", "noscript", "iframe", "svg"}
TEXT_BLOCK_KINDS = {"heading", "paragraph", "list_item", "blockquote"}

FetchText = Callable[[str], Awaitable[str]]
FetchBytes = Callable[[str], Awaitable[bytes]]


class NewsCollectionService:
    def __init__(
        self,
        *,
        source_configs: list[SourceConfig] | None = None,
        fetch_text: FetchText | None = None,
        request_timeout_seconds: int = 20,
        source_concurrency: int = 4,
        global_http_concurrency: int = 16,
        continue_on_source_error: bool = True,
        render_html: FetchText | None = None,
    ) -> None:
        self._source_configs = source_configs
        self._fetch_text_override = fetch_text
        self._request_timeout_seconds = request_timeout_seconds
        self._source_concurrency = max(source_concurrency, 1)
        self._global_http_concurrency = max(global_http_concurrency, 1)
        self._continue_on_source_error = continue_on_source_error
        self._render_html_override = render_html

    async def collect_articles(
        self,
        *,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
        published_after: datetime | None = None,
        max_articles_per_source: int | None = None,
        max_pages_per_source: int | None = None,
        include_undated: bool = False,
    ) -> list[CollectedArticle]:
        results = await self.collect_source_results(
            source_names=source_names,
            limit_sources=limit_sources,
            published_after=published_after,
            max_articles_per_source=max_articles_per_source,
            max_pages_per_source=max_pages_per_source,
            include_undated=include_undated,
        )
        articles: list[CollectedArticle] = []
        for result in results:
            articles.extend(result.articles)
        return articles

    async def collect_source_results(
        self,
        *,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
        published_after: datetime | None = None,
        max_articles_per_source: int | None = None,
        max_pages_per_source: int | None = None,
        include_undated: bool = False,
    ) -> list[SourceCollectionResult]:
        sources = self._select_sources(
            source_names=source_names,
            limit_sources=limit_sources,
        )
        if not sources:
            return []

        if self._fetch_text_override is not None:
            return await self._collect_source_results_with_fetch(
                sources=sources,
                fetch_text=self._fetch_text_override,
                published_after=published_after,
                max_articles_per_source=max_articles_per_source,
                max_pages_per_source=max_pages_per_source,
                include_undated=include_undated,
            )

        timeout = aiohttp.ClientTimeout(total=self._request_timeout_seconds)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; KarlFashionFeedBot/0.1; +https://example.com/bot)"
            )
        }
        semaphore = asyncio.Semaphore(self._global_http_concurrency)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:

            async def fetch_text(url: str) -> str:
                async with semaphore:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        return await response.text()

            return await self._collect_source_results_with_fetch(
                sources=sources,
                fetch_text=fetch_text,
                published_after=published_after,
                max_articles_per_source=max_articles_per_source,
                max_pages_per_source=max_pages_per_source,
                include_undated=include_undated,
            )

    def _select_sources(
        self,
        *,
        source_names: list[str] | None,
        limit_sources: int | None,
    ) -> list[SourceConfig]:
        sources = self._source_configs or load_source_configs()
        if source_names:
            allowed = {name.strip().lower() for name in source_names}
            sources = [source for source in sources if source.name.lower() in allowed]
        if limit_sources is not None:
            sources = sources[:limit_sources]
        return sources

    def source_config_for_name(self, source_name: str) -> SourceConfig:
        normalized_name = source_name.strip().lower()
        for source in self._source_configs or load_source_configs():
            if source.name.lower() == normalized_name:
                return source
        raise ValueError(f"source config not found: {source_name}")

    def parse_article_html(
        self,
        *,
        source_name: str,
        url: str,
        html_text: str,
    ) -> ParsedArticle:
        source = self.source_config_for_name(source_name)
        detail = self._extract_article_detail(
            html_text=html_text,
            url=url,
            detail_config=source.detail,
        )
        blocks = detail["blocks"]
        title = detail["title"]
        if not blocks:
            fallback_text = detail["summary"] or title or url
            blocks = [MarkdownBlock(kind="paragraph", text=fallback_text)]
        return ParsedArticle(
            title=title or detail["summary"][:120] or url,
            summary=detail["summary"] or _excerpt_from_blocks(blocks),
            markdown_blocks=tuple(blocks),
            images=tuple(detail["images"]),
            published_at=detail["published_at"],
            metadata={
                "image_count": len(detail["images"]),
                "block_count": len(blocks),
            },
        )

    async def fetch_html(
        self,
        *,
        source_name: str,
        url: str,
        fetch_text: FetchText,
    ) -> str:
        source = self.source_config_for_name(source_name)
        return await self._fetch_source_html(
            source=source,
            url=url,
            fetch_text=fetch_text,
        )

    async def attach_image_hashes(
        self,
        *,
        images: tuple[CollectedImage, ...],
        fetch_bytes: FetchBytes,
    ) -> tuple[CollectedImage, ...]:
        if not images:
            return images

        cache: dict[str, str] = {}
        enriched: list[CollectedImage] = []
        for image in images:
            enriched.append(
                await self._attach_single_image_hash(
                    image=image,
                    fetch_bytes=fetch_bytes,
                    cache=cache,
                )
            )
        return tuple(enriched)

    async def _collect_source_results_with_fetch(
        self,
        *,
        sources: list[SourceConfig],
        fetch_text: FetchText,
        published_after: datetime | None,
        max_articles_per_source: int | None,
        max_pages_per_source: int | None,
        include_undated: bool,
    ) -> list[SourceCollectionResult]:
        queue: asyncio.Queue[tuple[int, SourceConfig] | None] = asyncio.Queue()
        for index, source in enumerate(sources):
            queue.put_nowait((index, source))

        worker_count = min(self._source_concurrency, len(sources))
        for _ in range(worker_count):
            queue.put_nowait(None)

        results: list[SourceCollectionResult | None] = [None] * len(sources)

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    index, source = item
                    try:
                        articles = await self._collect_for_source(
                            source,
                            fetch_text=fetch_text,
                            published_after=published_after,
                            max_articles_per_source=max_articles_per_source,
                            max_pages_per_source=max_pages_per_source,
                            include_undated=include_undated,
                        )
                        results[index] = SourceCollectionResult(
                            source_name=source.name,
                            source_type=source.type,
                            articles=articles,
                        )
                    except Exception as exc:
                        if not self._continue_on_source_error:
                            raise
                        results[index] = SourceCollectionResult(
                            source_name=source.name,
                            source_type=source.type,
                            error=exc,
                        )
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
        try:
            await queue.join()
        finally:
            await asyncio.gather(*workers, return_exceptions=True)

        return [result for result in results if result is not None]

    async def _collect_for_source(
        self,
        source: SourceConfig,
        *,
        fetch_text: FetchText,
        published_after: datetime | None,
        max_articles_per_source: int | None,
        max_pages_per_source: int | None,
        include_undated: bool,
    ) -> list[CollectedArticle]:
        if source.type == "rss":
            return await self._collect_rss_articles(
                source,
                fetch_text=fetch_text,
                published_after=published_after,
                max_articles_per_source=max_articles_per_source,
                include_undated=include_undated,
            )
        return await self._collect_web_articles(
            source,
            fetch_text=fetch_text,
            published_after=published_after,
            max_articles_per_source=max_articles_per_source,
            max_pages_per_source=max_pages_per_source,
            include_undated=include_undated,
        )

    async def _collect_rss_articles(
        self,
        source: SourceConfig,
        *,
        fetch_text: FetchText,
        published_after: datetime | None,
        max_articles_per_source: int | None,
        include_undated: bool,
    ) -> list[CollectedArticle]:
        if not source.feed_url:
            return []

        max_items = max_articles_per_source or source.max_articles
        xml_text = await fetch_text(source.feed_url)
        feed = feedparser.parse(xml_text)
        detail_limit = max(source.detail_concurrency, 1)
        detail_semaphore = asyncio.Semaphore(detail_limit)

        async def build_article(entry: Any) -> CollectedArticle | None:
            link = str(entry.get("link") or "").strip()
            if not link:
                return None

            feed_title = _clean_text(str(entry.get("title") or ""))
            feed_published_at = _parse_datetime(
                entry.get("published")
                or entry.get("updated")
                or entry.get("pubDate")
                or entry.get("created")
            )

            feed_summary_raw = str(entry.get("summary") or entry.get("description") or "")
            feed_summary = _clean_text(feed_summary_raw)
            feed_blocks, excerpt = _parse_html_fragment_to_document(
                html_fragment=_extract_feed_content_html(entry) or feed_summary_raw,
                base_url=link,
                source_selector="rss:entry",
            )
            summary = feed_summary or excerpt
            if not summary:
                summary = _excerpt_from_blocks(feed_blocks)

            async with detail_semaphore:
                try:
                    detail_html = await self._fetch_source_html(
                        source=source,
                        url=link,
                        fetch_text=fetch_text,
                    )
                except Exception:
                    return None

            detail = self._extract_seed_detail(
                html_text=detail_html,
                url=link,
                detail_config=source.detail,
            )
            title = detail["title"] or feed_title
            published_at = detail["published_at"] or feed_published_at
            canonical_url = self.normalize_url(detail["canonical_url"] or link)
            summary = detail["summary"] or summary
            article = CollectedArticle(
                source_name=source.name,
                source_type=source.type,
                lang=source.lang,
                category=source.category,
                url=link,
                canonical_url=canonical_url,
                title=title or summary[:120] or link,
                summary=summary or link,
                published_at=published_at,
                metadata={
                    "entry_id": str(entry.get("id") or ""),
                    "source_hash": _stable_hash(link, title),
                },
            )
            if _passes_published_window(
                article.published_at,
                published_after=published_after,
                include_undated=include_undated,
            ):
                return article
            return None

        tasks = [build_article(entry) for entry in feed.entries[:max_items]]
        results = await asyncio.gather(*tasks)
        return [article for article in results if article is not None]

    async def _collect_web_articles(
        self,
        source: SourceConfig,
        *,
        fetch_text: FetchText,
        published_after: datetime | None,
        max_articles_per_source: int | None,
        max_pages_per_source: int | None,
        include_undated: bool,
    ) -> list[CollectedArticle]:
        max_items = max_articles_per_source or source.max_articles
        article_urls = await self._discover_web_article_urls(
            source,
            fetch_text=fetch_text,
            max_articles=max_items,
            max_pages=max_pages_per_source,
        )
        detail_limit = max(source.detail_concurrency, 1)
        detail_semaphore = asyncio.Semaphore(detail_limit)

        async def build_article(url: str) -> CollectedArticle | None:
            async with detail_semaphore:
                try:
                    html_text = await self._fetch_source_html(
                        source=source,
                        url=url,
                        fetch_text=fetch_text,
                    )
                except Exception:
                    return None

            detail = self._extract_seed_detail(
                html_text=html_text,
                url=url,
                detail_config=source.detail,
            )
            title = detail["title"]
            if not title:
                return None

            article = CollectedArticle(
                source_name=source.name,
                source_type=source.type,
                lang=source.lang,
                category=source.category,
                url=url,
                canonical_url=self.normalize_url(detail["canonical_url"] or url),
                title=title,
                summary=detail["summary"] or title,
                published_at=detail["published_at"],
                metadata={
                    "source_hash": _stable_hash(url, title),
                },
            )
            if _passes_published_window(
                article.published_at,
                published_after=published_after,
                include_undated=include_undated,
            ):
                return article
            return None

        tasks = [build_article(url) for url in article_urls[:max_items]]
        results = await asyncio.gather(*tasks)
        return [article for article in results if article is not None]

    async def _discover_web_article_urls(
        self,
        source: SourceConfig,
        *,
        fetch_text: FetchText,
        max_articles: int,
        max_pages: int | None,
    ) -> list[str]:
        discovered: list[str] = []
        seen: set[str] = set()
        pending_pages = list(source.start_urls)
        page_limit = max_pages or source.discovery.max_pages

        for page_index in range(page_limit):
            if page_index >= len(pending_pages):
                break
            page_url = pending_pages[page_index]
            try:
                html_text = await self._fetch_source_html(
                    source=source,
                    url=page_url,
                    fetch_text=fetch_text,
                )
            except Exception:
                continue

            soup = BeautifulSoup(html_text, "html.parser")
            for selector in source.discovery.link_selectors:
                for node in soup.select(selector):
                    href = node.get("href")
                    if not href:
                        continue
                    article_url = self.normalize_url(urljoin(page_url, href))
                    if not _is_allowed_domain(article_url, source.allowed_domains):
                        continue
                    if not _matches_patterns(
                        article_url,
                        source.discovery.article_url_patterns,
                        source.discovery.exclude_patterns,
                    ):
                        continue
                    if article_url in seen:
                        continue
                    seen.add(article_url)
                    discovered.append(article_url)
                    if len(discovered) >= max_articles:
                        return discovered

            for selector in source.discovery.pagination_selectors:
                for node in soup.select(selector):
                    href = node.get("href")
                    if not href:
                        continue
                    next_page = self.normalize_url(urljoin(page_url, href))
                    if next_page not in pending_pages and _is_allowed_domain(
                        next_page, source.allowed_domains
                    ):
                        pending_pages.append(next_page)

        return discovered

    async def _fetch_source_html(
        self,
        *,
        source: SourceConfig,
        url: str,
        fetch_text: FetchText,
    ) -> str:
        if not source.requires_js:
            return await fetch_text(url)
        return await self._render_html(url)

    async def _render_html(self, url: str) -> str:
        if self._render_html_override is not None:
            return await self._render_html_override(url)

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RuntimeError(
                "playwright is required for sources with requires_js=true"
            ) from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle")
                return await page.content()
            finally:
                await browser.close()

    async def _attach_single_image_hash(
        self,
        *,
        image: CollectedImage,
        fetch_bytes: FetchBytes,
        cache: dict[str, str],
    ) -> CollectedImage:
        image_hash = cache.get(image.normalized_url)
        if image_hash is None:
            payload = await fetch_bytes(image.source_url)
            image_hash = _compute_perceptual_hash(payload)
            cache[image.normalized_url] = image_hash

        image.metadata["image_hash"] = image_hash
        return image

    def _extract_seed_detail(
        self,
        *,
        html_text: str,
        url: str,
        detail_config: DetailConfig,
    ) -> dict[str, Any]:
        soup = BeautifulSoup(html_text, "html.parser")
        for selector in detail_config.remove_selectors:
            for node in soup.select(selector):
                node.decompose()

        canonical_url = _extract_canonical_url(soup=soup)
        title = _extract_text_by_selectors(soup, detail_config.title_selectors)
        if not title:
            title_node = soup.select_one("meta[property='og:title']") or soup.select_one("title")
            title = _extract_node_text(title_node)

        summary = _extract_meta_content(soup, "meta[name='description']")
        if not summary:
            content_nodes = []
            for selector in detail_config.content_selectors:
                nodes = [node for node in soup.select(selector) if _node_has_content(node)]
                if nodes:
                    content_nodes = nodes
                    break
            if not content_nodes and soup.body is not None:
                content_nodes = [soup.body]
            if content_nodes:
                blocks, _, _ = _build_blocks_and_images(
                    nodes=content_nodes,
                    base_url=url,
                    source_selector="seed",
                    normalize_url=self.normalize_url,
                )
                summary = _excerpt_from_blocks(blocks)
        published_text = _extract_selector_value(soup, detail_config.published_selectors)
        published_at = _parse_datetime(published_text)
        return {
            "canonical_url": canonical_url or self.normalize_url(url),
            "title": title,
            "summary": summary,
            "published_at": published_at,
        }

    def _extract_article_detail(
        self,
        *,
        html_text: str,
        url: str,
        detail_config: DetailConfig,
    ) -> dict[str, Any]:
        soup = BeautifulSoup(html_text, "html.parser")
        for selector in detail_config.remove_selectors:
            for node in soup.select(selector):
                node.decompose()

        canonical_url = _extract_canonical_url(soup=soup)

        title = _extract_text_by_selectors(soup, detail_config.title_selectors)
        if not title:
            title_node = soup.select_one("meta[property='og:title']") or soup.select_one(
                "title"
            )
            title = _extract_node_text(title_node)

        content_scope = _resolve_content_scope(
            soup=soup,
            selectors=detail_config.content_selectors,
        )
        content_nodes = content_scope["nodes"]
        scope_html = content_scope["html"]

        blocks = _extract_main_text_blocks(
            html_text=scope_html,
            base_url=url,
            title=title,
            fallback_html=html_text,
        )
        dom_blocks, images, image_anchor_positions = _build_blocks_and_images(
            nodes=content_nodes,
            base_url=url,
            source_selector=content_scope["selector"],
            normalize_url=self.normalize_url,
        )

        summary = _extract_meta_content(soup, "meta[name='description']")
        if not summary:
            summary = _excerpt_from_blocks(blocks)

        published_text = _extract_selector_value(soup, detail_config.published_selectors)
        published_at = _parse_datetime(published_text)

        hero_images = _extract_selector_image_candidates(
            soup=soup,
            selectors=detail_config.image_selectors,
            base_url=url,
            normalize_url=self.normalize_url,
        )
        images = _merge_meta_images(hero_images, images)
        images = _with_context_snippets(
            blocks=dom_blocks,
            images=images,
            image_anchor_positions=image_anchor_positions,
        )

        return {
            "canonical_url": canonical_url,
            "title": title,
            "summary": summary,
            "published_at": published_at,
            "blocks": blocks,
            "images": images,
        }

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url.strip())
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in QUERY_PARAM_BLOCKLIST
        ]
        normalized = parsed._replace(
            scheme=(parsed.scheme or "https").lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
            query=urlencode(filtered_query, doseq=True),
        )
        path = normalized.path or "/"
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        normalized = normalized._replace(path=path)
        return urlunparse(normalized)


def _build_blocks_and_images(
    *,
    nodes: Iterable[Tag],
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> tuple[list[MarkdownBlock], list[CollectedImage], dict[int, int]]:
    blocks: list[MarkdownBlock] = []
    images: list[CollectedImage] = []
    image_index_by_url: dict[str, int] = {}
    image_anchor_positions: dict[int, int] = {}

    for node in nodes:
        _consume_node(
            node=node,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )

    return _normalize_blocks(blocks), images, image_anchor_positions


def _resolve_content_scope(
    *,
    soup: BeautifulSoup,
    selectors: Iterable[str],
) -> dict[str, Any]:
    for selector in selectors:
        nodes = [node for node in soup.select(selector) if _node_has_content(node)]
        if nodes:
            return {
                "nodes": nodes,
                "selector": selector,
                "html": "\n".join(str(node) for node in nodes),
            }

    if soup.body is not None:
        return {
            "nodes": [soup.body],
            "selector": "body",
            "html": str(soup.body),
        }

    return {
        "nodes": [],
        "selector": "document",
        "html": str(soup),
    }


def _extract_main_text_blocks(
    *,
    html_text: str,
    base_url: str,
    title: str,
    fallback_html: str,
) -> list[MarkdownBlock]:
    blocks = _extract_blocks_with_trafilatura(html_text=html_text, base_url=base_url, title=title)
    if blocks:
        return blocks

    blocks = _extract_blocks_with_readability(html_text=html_text, base_url=base_url)
    if blocks:
        return blocks

    if html_text != fallback_html:
        blocks = _extract_blocks_with_trafilatura(
            html_text=fallback_html,
            base_url=base_url,
            title=title,
        )
        if blocks:
            return blocks
        blocks = _extract_blocks_with_readability(html_text=fallback_html, base_url=base_url)
        if blocks:
            return blocks

    fallback_text = _clean_text(BeautifulSoup(html_text, "html.parser").get_text("\n", strip=True))
    if fallback_text:
        return _text_to_blocks(fallback_text, title=title)
    return []


def _extract_blocks_with_trafilatura(
    *,
    html_text: str,
    base_url: str,
    title: str,
) -> list[MarkdownBlock]:
    extracted = trafilatura.extract(
        html_text,
        url=base_url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        no_fallback=False,
        output_format="txt",
    )
    if not extracted:
        return []
    return _text_to_blocks(extracted, title=title)


def _extract_blocks_with_readability(
    *,
    html_text: str,
    base_url: str,
) -> list[MarkdownBlock]:
    if ReadabilityDocument is None:
        raise RuntimeError("readability-lxml is required for fallback content extraction")

    document = ReadabilityDocument(html_text, url=base_url)
    summary_html = document.summary(html_partial=True)
    summary_soup = BeautifulSoup(summary_html, "html.parser")
    blocks, _, _ = _build_blocks_and_images(
        nodes=summary_soup.children,
        base_url=base_url,
        source_selector="readability",
        normalize_url=lambda value: NewsCollectionService.normalize_url(value),
    )
    return blocks


def _text_to_blocks(text: str, *, title: str) -> list[MarkdownBlock]:
    normalized = text.replace("\r", "\n")
    parts = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if len(parts) <= 1:
        line_parts = [_clean_text(line) for line in normalized.splitlines() if _clean_text(line)]
        if len(line_parts) > len(parts):
            parts = line_parts
    blocks: list[MarkdownBlock] = []
    for index, part in enumerate(parts):
        clean_part = _clean_text(part)
        if not clean_part:
            continue
        kind = "heading" if index == 0 and title and clean_part == title.strip() else "paragraph"
        blocks.append(MarkdownBlock(kind=kind, text=clean_part))
    return _normalize_blocks(blocks)


def _extract_scope_images(
    *,
    nodes: Iterable[Tag],
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> tuple[list[CollectedImage], dict[int, int]]:
    blocks: list[MarkdownBlock] = []
    images: list[CollectedImage] = []
    image_index_by_url: dict[str, int] = {}
    image_anchor_positions: dict[int, int] = {}

    for node in nodes:
        _consume_image_nodes(
            node=node,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )

    return images, image_anchor_positions


def _consume_node(
    *,
    node: Tag | NavigableString,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
    image_anchor_positions: dict[int, int],
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> None:
    if isinstance(node, NavigableString):
        text = _clean_text(str(node))
        if text:
            blocks.append(MarkdownBlock(kind="paragraph", text=text))
        return

    if not isinstance(node, Tag):
        return
    if node.name in SKIP_TAGS:
        return
    if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        _consume_mixed_container(
            node=node,
            kind="heading",
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return
    if node.name in {"p", "blockquote"}:
        _consume_mixed_container(
            node=node,
            kind="blockquote" if node.name == "blockquote" else "paragraph",
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return
    if node.name in {"ul", "ol"}:
        for child in node.find_all("li", recursive=False):
            _consume_mixed_container(
                node=child,
                kind="list_item",
                blocks=blocks,
                images=images,
                image_index_by_url=image_index_by_url,
                image_anchor_positions=image_anchor_positions,
                base_url=base_url,
                source_selector=source_selector,
                normalize_url=normalize_url,
            )
        return
    if node.name == "figure":
        _consume_figure(
            node=node,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return
    if node.name in {"img", "picture"} or _background_image_url(node):
        _append_image_block(
            image_tag=node,
            role="inline",
            caption_raw="",
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            anchor_index=len(blocks),
            base_url=base_url,
            source_kind="inline",
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return

    child_block_tags = {
        child.name
        for child in node.find_all(recursive=False)
        if isinstance(child, Tag)
    }
    if child_block_tags.intersection(
        {"p", "img", "picture", "figure", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
    ):
        for child in node.children:
            _consume_node(
                node=child,
                blocks=blocks,
                images=images,
                image_index_by_url=image_index_by_url,
                image_anchor_positions=image_anchor_positions,
                base_url=base_url,
                source_selector=source_selector,
                normalize_url=normalize_url,
            )
        return

    text = _clean_text(node.get_text(" ", strip=True))
    if text:
        blocks.append(MarkdownBlock(kind="paragraph", text=text))


def _consume_mixed_container(
    *,
    node: Tag,
    kind: str,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
    image_anchor_positions: dict[int, int],
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> None:
    parts: list[str] = []

    def flush_text() -> None:
        text = _clean_text(" ".join(parts))
        parts.clear()
        if text:
            blocks.append(MarkdownBlock(kind=kind, text=text))

    for child in node.children:
        if isinstance(child, NavigableString):
            text = _clean_text(str(child))
            if text:
                parts.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in {"img", "picture"} or _background_image_url(child):
            flush_text()
            _append_image_block(
                image_tag=child,
                role="inline",
                caption_raw="",
                images=images,
                image_index_by_url=image_index_by_url,
                image_anchor_positions=image_anchor_positions,
                anchor_index=len(blocks),
                base_url=base_url,
                source_kind="inline",
                source_selector=source_selector,
                normalize_url=normalize_url,
            )
            continue
        if child.name == "figure":
            flush_text()
            _consume_figure(
                node=child,
                blocks=blocks,
                images=images,
                image_index_by_url=image_index_by_url,
                image_anchor_positions=image_anchor_positions,
                base_url=base_url,
                source_selector=source_selector,
                normalize_url=normalize_url,
            )
            continue
        text = _clean_text(child.get_text(" ", strip=True))
        if text:
            parts.append(text)

    flush_text()


def _consume_figure(
    *,
    node: Tag,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
    image_anchor_positions: dict[int, int],
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> None:
    caption = _clean_text(node.find("figcaption").get_text(" ", strip=True)) if node.find("figcaption") else ""
    media_tags = _collect_media_tags(node)
    for image_tag in media_tags:
        _append_image_block(
            image_tag=image_tag,
            role="gallery" if len(media_tags) > 1 else "inline",
            caption_raw=caption,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            anchor_index=len(blocks),
            base_url=base_url,
            source_kind="figure",
            source_selector=source_selector,
            normalize_url=normalize_url,
        )


def _consume_image_nodes(
    *,
    node: Tag | NavigableString,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
    image_anchor_positions: dict[int, int],
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> None:
    if isinstance(node, NavigableString) or not isinstance(node, Tag):
        return
    if node.name in SKIP_TAGS:
        return
    if node.name == "figure":
        _consume_figure(
            node=node,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return
    if node.name in {"img", "picture"} or _background_image_url(node):
        _append_image_block(
            image_tag=node,
            role="inline",
            caption_raw="",
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            anchor_index=len(blocks),
            base_url=base_url,
            source_kind="inline",
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return
    for child in node.children:
        _consume_image_nodes(
            node=child,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            image_anchor_positions=image_anchor_positions,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )


def _append_image_block(
    *,
    image_tag: Tag,
    role: str,
    caption_raw: str,
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
    image_anchor_positions: dict[int, int],
    anchor_index: int,
    base_url: str,
    source_kind: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> None:
    candidate = _image_from_tag(
        image_tag=image_tag,
        base_url=base_url,
        role=role,
        caption_raw=caption_raw,
        source_kind=source_kind,
        source_selector=source_selector,
        normalize_url=normalize_url,
    )
    if candidate is None:
        return

    image_index = _upsert_image_candidate(
        images=images,
        image_index_by_url=image_index_by_url,
        candidate=candidate,
    )
    image_anchor_positions.setdefault(image_index, anchor_index)


def _image_from_tag(
    *,
    image_tag: Tag,
    base_url: str,
    role: str,
    caption_raw: str,
    source_kind: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> CollectedImage | None:
    raw_url = _resolve_image_url(image_tag)
    if not raw_url:
        return None
    source_url = urljoin(base_url, raw_url)
    normalized_url = normalize_url(source_url)
    return CollectedImage(
        source_url=source_url,
        normalized_url=normalized_url,
        role=role,
        alt_text=_extract_image_alt_text(image_tag),
        caption_raw=caption_raw,
        credit_raw=_extract_credit_text(image_tag),
        source_kind=source_kind,
        source_selector=source_selector,
    )


def _extract_feed_content_html(entry: Any) -> str:
    contents = entry.get("content") or []
    if contents and isinstance(contents, Iterable):
        first = next(iter(contents), None)
        if isinstance(first, dict):
            return str(first.get("value") or "")
    return str(entry.get("summary") or entry.get("description") or "")


def _extract_feed_image_candidates(
    entry: Any,
    *,
    normalize_url: Callable[[str], str],
) -> list[CollectedImage]:
    images: list[CollectedImage] = []
    seen: set[str] = set()

    def add(url: str, role: str) -> None:
        normalized_url = normalize_url(url)
        if normalized_url in seen:
            return
        seen.add(normalized_url)
        images.append(
            CollectedImage(
                source_url=url,
                normalized_url=normalized_url,
                role=role,
                source_kind="feed",
                source_selector="media",
            )
        )

    for item in entry.get("media_content") or []:
        if isinstance(item, dict) and item.get("url"):
            add(str(item["url"]), "hero")
    for item in entry.get("media_thumbnail") or []:
        if isinstance(item, dict) and item.get("url"):
            add(str(item["url"]), "hero")
    for link in entry.get("links") or []:
        if isinstance(link, dict) and str(link.get("type", "")).startswith("image/") and link.get("href"):
            add(str(link["href"]), "hero")
    return images


def _extract_selector_image_candidates(
    *,
    soup: BeautifulSoup,
    selectors: Iterable[str],
    base_url: str,
    normalize_url: Callable[[str], str],
) -> list[CollectedImage]:
    for selector in selectors:
        images: list[CollectedImage] = []
        seen: set[str] = set()
        for node in soup.select(selector):
            if node.name == "meta":
                raw_url = node.get("content", "").strip()
            else:
                raw_url = _resolve_image_url(node)

            if not raw_url:
                continue
            source_url = urljoin(base_url, raw_url)
            normalized_url = normalize_url(source_url)
            if normalized_url in seen:
                continue
            seen.add(normalized_url)
            images.append(
                CollectedImage(
                    source_url=source_url,
                    normalized_url=normalized_url,
                    role="hero",
                    alt_text=_extract_image_alt_text(node),
                    caption_raw=_extract_caption_text(node),
                    credit_raw=_extract_credit_text(node),
                    source_kind="meta" if node.name == "meta" else "selector",
                    source_selector=selector,
                )
            )
        if images:
            return images
    return []


def _parse_html_fragment_to_document(
    *,
    html_fragment: str,
    base_url: str,
    source_selector: str,
) -> tuple[list[MarkdownBlock], str]:
    if not html_fragment.strip():
        return [], ""

    fragment_soup = BeautifulSoup(f"<div>{html_fragment}</div>", "html.parser")
    root = fragment_soup.find("div")
    if root is None:
        return [], ""

    blocks, _, _ = _build_blocks_and_images(
        nodes=root.children,
        base_url=base_url,
        source_selector=source_selector,
        normalize_url=lambda value: NewsCollectionService.normalize_url(value),
    )
    return blocks, _excerpt_from_blocks(blocks)


def _upsert_image_candidate(
    *,
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
    candidate: CollectedImage,
) -> int:
    existing_index = image_index_by_url.get(candidate.normalized_url)
    if existing_index is None:
        candidate.position = len(images)
        images.append(candidate)
        image_index_by_url[candidate.normalized_url] = candidate.position
        return candidate.position

    existing = images[existing_index]
    if _role_priority(candidate.role) < _role_priority(existing.role):
        existing.role = candidate.role
    if not existing.alt_text:
        existing.alt_text = candidate.alt_text
    if not existing.caption_raw:
        existing.caption_raw = candidate.caption_raw
    if not existing.credit_raw:
        existing.credit_raw = candidate.credit_raw
    if not existing.source_kind:
        existing.source_kind = candidate.source_kind
    if not existing.source_selector:
        existing.source_selector = candidate.source_selector
    return existing_index


def _merge_meta_images(
    meta_images: list[CollectedImage],
    content_images: list[CollectedImage],
) -> list[CollectedImage]:
    merged = list(content_images)
    image_index_by_url = {
        image.normalized_url: index for index, image in enumerate(merged)
    }
    for candidate in meta_images:
        existing_index = image_index_by_url.get(candidate.normalized_url)
        if existing_index is None:
            _upsert_image_candidate(
                images=merged,
                image_index_by_url=image_index_by_url,
                candidate=candidate,
            )
        else:
            existing = merged[existing_index]
            if _role_priority(candidate.role) < _role_priority(existing.role):
                existing.role = candidate.role

    for position, image in enumerate(merged):
        image.position = position
    return merged


def _with_context_snippets(
    *,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_anchor_positions: dict[int, int],
) -> list[CollectedImage]:
    for image_index, image in enumerate(images):
        block_position = image_anchor_positions.get(image_index, 0)
        previous_text = next(
            (
                block.text
                for block in reversed(blocks[:block_position])
                if block.kind in TEXT_BLOCK_KINDS and block.text.strip()
            ),
            "",
        )
        next_text = next(
            (
                block.text
                for block in blocks[block_position:]
                if block.kind in TEXT_BLOCK_KINDS and block.text.strip()
            ),
            "",
        )
        image.context_snippet = _clean_text(
            f"{previous_text[-160:]} {next_text[:160]}"
        )
    return images


def _normalize_blocks(blocks: list[MarkdownBlock]) -> list[MarkdownBlock]:
    normalized: list[MarkdownBlock] = []
    last_signature: tuple[str, str] | None = None
    for block in blocks:
        text = _clean_text(block.text)
        if not text:
            continue
        signature = (block.kind, text)
        if signature != last_signature:
            normalized.append(MarkdownBlock(kind=block.kind, text=text))
        last_signature = signature
    return normalized


def _excerpt_from_blocks(blocks: Iterable[MarkdownBlock]) -> str:
    for block in blocks:
        if block.kind in TEXT_BLOCK_KINDS and block.text.strip():
            return block.text.strip()[:280]
    return ""


def _plain_text_from_blocks(blocks: Iterable[MarkdownBlock]) -> str:
    return " ".join(
        block.text.strip() for block in blocks if block.kind in TEXT_BLOCK_KINDS and block.text.strip()
    ).strip()


def _collect_media_tags(node: Tag) -> list[Tag]:
    tags: list[Tag] = []
    for candidate in node.find_all(["picture", "img"], recursive=True):
        if candidate.name == "img" and candidate.find_parent("picture") is not None:
            continue
        tags.append(candidate)
    if _background_image_url(node):
        tags.append(node)
    return tags


def _resolve_image_url(node: Tag) -> str:
    if node.name == "meta":
        return node.get("content", "").strip()

    if node.name == "picture":
        for source in node.find_all("source", recursive=False):
            image_url = _resolve_image_url(source)
            if image_url:
                return image_url
        picture_img = node.find("img")
        if picture_img is not None:
            image_url = _resolve_image_url(picture_img)
            if image_url:
                return image_url

    candidates = (
        node.get("src"),
        node.get("data-src"),
        node.get("data-original"),
        node.get("data-lazy-src"),
        node.get("data-lazy"),
        node.get("data-url"),
        node.get("data-image"),
        node.get("data-fallback-src"),
        _best_srcset_url(node.get("data-srcset", "")),
        _best_srcset_url(node.get("srcset", "")),
        _background_image_url(node),
    )
    for candidate in candidates:
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _extract_image_alt_text(node: Tag) -> str:
    if node.name == "picture":
        image_tag = node.find("img")
        if image_tag is not None:
            return _clean_text(image_tag.get("alt", ""))
    return _clean_text(node.get("alt", ""))


def _extract_caption_text(node: Tag) -> str:
    figure = node if node.name == "figure" else node.find_parent("figure")
    if figure is None:
        return ""
    caption = figure.find("figcaption")
    if caption is None:
        return ""
    return _clean_text(caption.get_text(" ", strip=True))


def _extract_credit_text(node: Tag) -> str:
    figure = node if node.name == "figure" else node.find_parent("figure")
    if figure is None:
        return ""
    credit_node = figure.select_one("[class*='credit'], [rel='author'], .byline, .copyright")
    if credit_node is None:
        return ""
    return _clean_text(credit_node.get_text(" ", strip=True))


def _background_image_url(node: Tag) -> str:
    style = str(node.get("style", "")).strip()
    if not style:
        return ""
    match = re.search(r"background-image\s*:\s*url\((['\"]?)(.*?)\1\)", style, flags=re.IGNORECASE)
    if match is None:
        return ""
    return match.group(2).strip()


def _extract_text_by_selectors(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        text = _extract_node_text(node)
        if text:
            return text
    return ""


def _extract_selector_value(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        if node.name == "meta":
            content = node.get("content", "").strip()
            if content:
                return content
        if node.name == "time":
            datetime_value = node.get("datetime", "").strip()
            if datetime_value:
                return datetime_value
        text = _extract_node_text(node)
        if text:
            return text
    return ""


def _extract_meta_content(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    if node:
        return _clean_text(node.get("content", ""))
    return ""


def _extract_canonical_url(*, soup: BeautifulSoup) -> str:
    canonical_node = soup.select_one("link[rel='canonical']")
    canonical_url = canonical_node.get("href", "") if canonical_node else ""
    if canonical_url:
        return canonical_url

    og_url = soup.select_one("meta[property='og:url']")
    if og_url:
        return og_url.get("content", "")
    return ""


def _extract_node_text(node: Any) -> str:
    if node is None:
        return ""
    return _clean_text(node.get_text(" ", strip=True))


def _node_has_content(node: Tag) -> bool:
    return bool(node.get_text(" ", strip=True)) or bool(node.find("img"))


def _clean_text(value: str) -> str:
    value = BeautifulSoup(unescape(value or ""), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc_naive(value)
    text = str(value).strip()
    if not text:
        return None

    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return _to_utc_naive(datetime.fromisoformat(candidate))
        except ValueError:
            pass

    try:
        return _to_utc_naive(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        return None


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _is_allowed_domain(url: str, allowed_domains: Iterable[str]) -> bool:
    hostname = urlparse(url).hostname or ""
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in allowed_domains
    )


def _matches_patterns(
    url: str,
    include_patterns: Iterable[str],
    exclude_patterns: Iterable[str],
) -> bool:
    if include_patterns and not any(re.search(pattern, url) for pattern in include_patterns):
        return False
    if exclude_patterns and any(re.search(pattern, url) for pattern in exclude_patterns):
        return False
    return True


def _stable_hash(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}\n{title}".encode("utf-8")).hexdigest()


def _passes_published_window(
    published_at: datetime | None,
    *,
    published_after: datetime | None,
    include_undated: bool,
) -> bool:
    if published_after is None:
        return True
    if published_at is None:
        return include_undated
    return published_at >= published_after


def _best_srcset_url(srcset: str) -> str:
    if not srcset.strip():
        return ""

    best_url = ""
    best_score = -1.0
    for part in srcset.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        pieces = candidate.split()
        url = pieces[0].strip()
        descriptor = pieces[1].strip().lower() if len(pieces) > 1 else ""
        if descriptor.endswith("w"):
            score = float(descriptor[:-1] or 0)
        elif descriptor.endswith("x"):
            score = float(descriptor[:-1] or 0) * 1000.0
        else:
            score = 0.0
        if score >= best_score:
            best_url = url
            best_score = score
    return best_url


def _compute_perceptual_hash(payload: bytes) -> str:
    with Image.open(BytesIO(payload)) as image:
        grayscale = image.convert("L").resize((32, 32))
        matrix = np.asarray(grayscale, dtype=float)

    dct_matrix = _dct_transform_matrix(32)
    coefficients = dct_matrix @ matrix @ dct_matrix.T
    low_frequency = coefficients[:8, :8]
    median = float(np.median(low_frequency[1:, :]))
    bits = "".join("1" if value > median else "0" for value in low_frequency.flatten())
    return f"{int(bits, 2):016x}"


def _dct_transform_matrix(size: int) -> np.ndarray:
    indices = np.arange(size, dtype=float)
    matrix = np.empty((size, size), dtype=float)
    matrix[0, :] = 1.0 / np.sqrt(size)
    for row in range(1, size):
        matrix[row, :] = np.sqrt(2.0 / size) * np.cos(
            ((2.0 * indices + 1.0) * row * np.pi) / (2.0 * size)
        )
    return matrix


def _role_priority(role: str) -> int:
    priorities = {
        "hero": 0,
        "og": 1,
        "twitter": 2,
        "gallery": 3,
        "inline": 4,
    }
    return priorities.get(role, 10)
