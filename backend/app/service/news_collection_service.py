"""Collect fashion news from RSS feeds and direct web pages."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import aiohttp
import feedparser
from bs4 import BeautifulSoup

from backend.app.config.source_config import (
    DetailConfig,
    SourceConfig,
    load_source_configs,
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


@dataclass(frozen=True)
class CollectedArticle:
    source_name: str
    source_type: str
    lang: str
    category: str
    url: str
    canonical_url: str
    title: str
    summary: str
    content: str
    image_url: str | None
    published_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceCollectionResult:
    source_name: str
    source_type: str
    articles: list[CollectedArticle] = field(default_factory=list)
    error: Exception | None = None


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
            summary = _clean_text(
                str(entry.get("summary") or entry.get("description") or "")
            )
            content = _extract_feed_content(entry) or summary
            published_at = _parse_datetime(
                entry.get("published")
                or entry.get("updated")
                or entry.get("pubDate")
                or entry.get("created")
            )
            image_url = _extract_feed_image(entry)
            canonical_url = self.normalize_url(link)
            metadata = {
                "entry_id": str(entry.get("id") or ""),
                "source_hash": _stable_hash(link, title),
            }

            if len(content) < 280:
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
                    content = detail["content"] or content
                    summary = summary or detail["summary"]
                    image_url = image_url or detail["image_url"]
                    canonical_url = self.normalize_url(detail["canonical_url"] or link)

            article = CollectedArticle(
                source_name=source.name,
                source_type=source.type,
                lang=source.lang,
                category=source.category,
                url=link,
                canonical_url=canonical_url,
                title=title or summary[:120] or link,
                summary=summary or content[:280],
                content=content,
                image_url=image_url,
                published_at=published_at,
                metadata=metadata,
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
            content = detail["content"]
            if not title or not content:
                return None

            article = CollectedArticle(
                source_name=source.name,
                source_type=source.type,
                lang=source.lang,
                category=source.category,
                url=url,
                canonical_url=self.normalize_url(detail["canonical_url"] or url),
                title=title,
                summary=detail["summary"],
                content=content,
                image_url=detail["image_url"],
                published_at=detail["published_at"],
                metadata={"source_hash": _stable_hash(url, title)},
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

        content = _extract_content_by_selectors(soup, detail_config.content_selectors)
        summary = _extract_meta_content(soup, "meta[name='description']")
        if not summary:
            summary = content[:280]

        published_text = _extract_selector_value(soup, detail_config.published_selectors)
        published_at = _parse_datetime(published_text)

        image_url = _extract_selector_value(soup, detail_config.image_selectors)
        if image_url:
            image_url = urljoin(url, image_url)

        return {
            "canonical_url": canonical_url,
            "title": title,
            "content": content,
            "summary": summary,
            "published_at": published_at,
            "image_url": image_url,
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


def _extract_feed_content(entry: Any) -> str:
    contents = entry.get("content") or []
    if contents and isinstance(contents, Iterable):
        first = next(iter(contents), None)
        if isinstance(first, dict):
            return _clean_text(str(first.get("value") or ""))
    return ""


def _extract_feed_image(entry: Any) -> str | None:
    media_content = entry.get("media_content") or []
    if media_content:
        first = media_content[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])

    media_thumbnail = entry.get("media_thumbnail") or []
    if media_thumbnail:
        first = media_thumbnail[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
    return None


def _extract_text_by_selectors(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        text = _extract_node_text(node)
        if text:
            return text
    return ""


def _extract_content_by_selectors(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    best_text = ""
    for selector in selectors:
        for node in soup.select(selector):
            text = _extract_node_text(node)
            if len(text) > len(best_text):
                best_text = text
        if best_text:
            return best_text
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


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


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
