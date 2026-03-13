"""Collect fashion news into markdown blocks and image asset candidates."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import aiohttp
import feedparser
from bs4 import BeautifulSoup, NavigableString, Tag

from backend.app.config.source_config import (
    DetailConfig,
    SourceConfig,
    load_source_configs,
)
from backend.app.service.article_contracts import (
    CollectedArticle,
    CollectedImage,
    MarkdownBlock,
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
    ) -> None:
        self._source_configs = source_configs
        self._fetch_text_override = fetch_text
        self._request_timeout_seconds = request_timeout_seconds
        self._source_concurrency = max(source_concurrency, 1)
        self._global_http_concurrency = max(global_http_concurrency, 1)
        self._continue_on_source_error = continue_on_source_error

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

            title = _clean_text(str(entry.get("title") or ""))
            published_at = _parse_datetime(
                entry.get("published")
                or entry.get("updated")
                or entry.get("pubDate")
                or entry.get("created")
            )

            feed_summary_raw = str(entry.get("summary") or entry.get("description") or "")
            feed_summary = _clean_text(feed_summary_raw)
            blocks, images, excerpt = _parse_html_fragment_to_document(
                html_fragment=_extract_feed_content_html(entry) or feed_summary_raw,
                base_url=link,
                source_selector="rss:entry",
            )
            images = _merge_meta_images(
                _extract_feed_image_candidates(entry, normalize_url=self.normalize_url),
                images,
            )
            summary = feed_summary or excerpt
            markdown_text_length = len(_plain_text_from_blocks(blocks))
            canonical_url = self.normalize_url(link)

            if markdown_text_length < 280:
                async with detail_semaphore:
                    try:
                        detail_html = await fetch_text(link)
                    except Exception:
                        detail_html = ""
                if detail_html:
                    detail = self._extract_article_detail(
                        html_text=detail_html,
                        url=link,
                        detail_config=source.detail,
                    )
                    title = detail["title"] or title
                    summary = detail["summary"] or summary
                    blocks = detail["blocks"] or blocks
                    images = _merge_meta_images(detail["images"], images)
                    canonical_url = self.normalize_url(detail["canonical_url"] or link)

            if not blocks:
                fallback_text = summary or title or link
                blocks = [MarkdownBlock(kind="paragraph", text=fallback_text)]

            blocks = _ensure_hero_placeholders(blocks, images)
            images = _with_context_snippets(blocks, images)
            article = CollectedArticle(
                source_name=source.name,
                source_type=source.type,
                lang=source.lang,
                category=source.category,
                url=link,
                canonical_url=canonical_url,
                title=title or summary[:120] or link,
                summary=summary or _excerpt_from_blocks(blocks),
                markdown_blocks=tuple(blocks),
                images=tuple(images),
                published_at=published_at,
                metadata={
                    "entry_id": str(entry.get("id") or ""),
                    "source_hash": _stable_hash(link, title),
                    "image_count": len(images),
                    "block_count": len(blocks),
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
                    html_text = await fetch_text(url)
                except Exception:
                    return None

            detail = self._extract_article_detail(
                html_text=html_text,
                url=url,
                detail_config=source.detail,
            )
            title = detail["title"]
            blocks = detail["blocks"]
            if not title or not blocks:
                return None

            images = _with_context_snippets(blocks, detail["images"])
            article = CollectedArticle(
                source_name=source.name,
                source_type=source.type,
                lang=source.lang,
                category=source.category,
                url=url,
                canonical_url=self.normalize_url(detail["canonical_url"] or url),
                title=title,
                summary=detail["summary"] or _excerpt_from_blocks(blocks),
                markdown_blocks=tuple(blocks),
                images=tuple(images),
                published_at=detail["published_at"],
                metadata={
                    "source_hash": _stable_hash(url, title),
                    "image_count": len(images),
                    "block_count": len(blocks),
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
                html_text = await fetch_text(page_url)
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

        canonical_node = soup.select_one("link[rel='canonical']")
        canonical_url = canonical_node.get("href", "") if canonical_node else ""
        if not canonical_url:
            og_url = soup.select_one("meta[property='og:url']")
            canonical_url = og_url.get("content", "") if og_url else ""

        title = _extract_text_by_selectors(soup, detail_config.title_selectors)
        if not title:
            title_node = soup.select_one("meta[property='og:title']") or soup.select_one(
                "title"
            )
            title = _extract_node_text(title_node)

        content_nodes = []
        matched_selector = ""
        for selector in detail_config.content_selectors:
            nodes = [node for node in soup.select(selector) if _node_has_content(node)]
            if nodes:
                content_nodes = nodes
                matched_selector = selector
                break
        if not content_nodes and soup.body is not None:
            content_nodes = [soup.body]

        blocks, images = _build_blocks_and_images(
            nodes=content_nodes,
            base_url=url,
            source_selector=matched_selector or "body",
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
        blocks = _ensure_hero_placeholders(blocks, images)

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
) -> tuple[list[MarkdownBlock], list[CollectedImage]]:
    blocks: list[MarkdownBlock] = []
    images: list[CollectedImage] = []
    image_index_by_url: dict[str, int] = {}

    for node in nodes:
        _consume_node(
            node=node,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )

    return _normalize_blocks(blocks), images


def _consume_node(
    *,
    node: Tag | NavigableString,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
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
            base_url=base_url,
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
        return
    if node.name == "img":
        _append_image_block(
            image_tag=node,
            role="inline",
            caption_raw="",
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
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
    if child_block_tags.intersection({"p", "img", "figure", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}):
        for child in node.children:
            _consume_node(
                node=child,
                blocks=blocks,
                images=images,
                image_index_by_url=image_index_by_url,
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
        if child.name == "img":
            flush_text()
            _append_image_block(
                image_tag=child,
                role="inline",
                caption_raw="",
                blocks=blocks,
                images=images,
                image_index_by_url=image_index_by_url,
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
    base_url: str,
    source_selector: str,
    normalize_url: Callable[[str], str],
) -> None:
    caption = _clean_text(node.find("figcaption").get_text(" ", strip=True)) if node.find("figcaption") else ""
    for image_tag in node.find_all("img"):
        _append_image_block(
            image_tag=image_tag,
            role="gallery" if len(node.find_all("img")) > 1 else "inline",
            caption_raw=caption,
            blocks=blocks,
            images=images,
            image_index_by_url=image_index_by_url,
            base_url=base_url,
            source_kind="figure",
            source_selector=source_selector,
            normalize_url=normalize_url,
        )
    if caption:
        blocks.append(MarkdownBlock(kind="paragraph", text=caption))


def _append_image_block(
    *,
    image_tag: Tag,
    role: str,
    caption_raw: str,
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
    image_index_by_url: dict[str, int],
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
    blocks.append(MarkdownBlock(kind="image", image_index=image_index))


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
    raw_url = (
        image_tag.get("src")
        or image_tag.get("data-src")
        or image_tag.get("data-original")
        or _first_srcset_url(image_tag.get("srcset", ""))
    )
    if not raw_url:
        return None
    source_url = urljoin(base_url, raw_url)
    normalized_url = normalize_url(source_url)
    return CollectedImage(
        source_url=source_url,
        normalized_url=normalized_url,
        role=role,
        alt_text=_clean_text(image_tag.get("alt", "")),
        caption_raw=caption_raw,
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
            elif node.name == "img":
                raw_url = (
                    node.get("src")
                    or node.get("data-src")
                    or node.get("data-original")
                    or _first_srcset_url(node.get("srcset", ""))
                )
            else:
                raw_url = ""

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
                    alt_text=_clean_text(node.get("alt", "")) if node.name == "img" else "",
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
) -> tuple[list[MarkdownBlock], list[CollectedImage], str]:
    if not html_fragment.strip():
        return [], [], ""

    fragment_soup = BeautifulSoup(f"<div>{html_fragment}</div>", "html.parser")
    root = fragment_soup.find("div")
    if root is None:
        return [], [], ""

    blocks, images = _build_blocks_and_images(
        nodes=root.children,
        base_url=base_url,
        source_selector=source_selector,
        normalize_url=lambda value: NewsCollectionService.normalize_url(value),
    )
    return blocks, images, _excerpt_from_blocks(blocks)


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


def _ensure_hero_placeholders(
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
) -> list[MarkdownBlock]:
    referenced_indexes = {
        block.image_index
        for block in blocks
        if block.kind == "image" and block.image_index is not None
    }
    hero_indexes = [
        index for index, image in enumerate(images) if image.role == "hero" and index not in referenced_indexes
    ]
    if not hero_indexes:
        return blocks

    leading_blocks = [MarkdownBlock(kind="image", image_index=index) for index in hero_indexes]
    return leading_blocks + blocks


def _with_context_snippets(
    blocks: list[MarkdownBlock],
    images: list[CollectedImage],
) -> list[CollectedImage]:
    for image_index, image in enumerate(images):
        block_positions = [
            position
            for position, block in enumerate(blocks)
            if block.kind == "image" and block.image_index == image_index
        ]
        if not block_positions:
            continue

        block_position = block_positions[0]
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
                for block in blocks[block_position + 1 :]
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
    last_signature: tuple[str, str, int | None] | None = None
    for block in blocks:
        text = _clean_text(block.text)
        if block.kind == "image":
            signature = (block.kind, "", block.image_index)
            if signature != last_signature:
                normalized.append(block)
            last_signature = signature
            continue
        if not text:
            continue
        signature = (block.kind, text, None)
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


def _first_srcset_url(srcset: str) -> str:
    if not srcset.strip():
        return ""
    first = srcset.split(",")[0].strip()
    return first.split(" ")[0].strip()


def _role_priority(role: str) -> int:
    priorities = {
        "hero": 0,
        "og": 1,
        "twitter": 2,
        "gallery": 3,
        "inline": 4,
    }
    return priorities.get(role, 10)
