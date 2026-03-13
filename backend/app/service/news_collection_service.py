"""News collection service for the refactored backend."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import importlib
import json
import logging
import re
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import aiohttp
import requests
import yaml
from bs4 import BeautifulSoup

from backend.app.config.llm_config import get_news_collection_llm_config


logger = logging.getLogger(__name__)

SERVICE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_FILE = SERVICE_DIR / "sources.yaml"

MAX_ARTICLES_PER_FEED = 30
LLM_CONCURRENCY = 5
IMAGE_FETCH_CONCURRENCY = 12
MAX_PAGE_IMAGE_FETCH = 200
DEFAULT_CRAWL_PAGES = 2
DEFAULT_FETCH_TIMEOUT = 20
TITLE_DEDUP_THRESHOLD = 0.6

COMMON_TRACKING_QUERY_PREFIXES = (
    "utm_",
    "mc_",
    "mkt_",
    "oly_",
)
COMMON_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "ref",
    "ref_",
    "spm",
}
DEFAULT_LINK_SELECTORS = ["a[href]"]
DEFAULT_TITLE_SELECTORS = ["h1"]
DEFAULT_CONTENT_SELECTORS = [
    "article",
    "main article",
    "main",
    "[role='main']",
    ".article-body",
    ".entry-content",
    ".post-content",
    ".article-content",
]
DEFAULT_PUBLISHED_SELECTORS = ["time[datetime]", "time", "[itemprop='datePublished']"]
DEFAULT_IMAGE_SELECTORS = ["meta[property='og:image']", "meta[name='twitter:image']", "article img", "main img"]
DEFAULT_REMOVE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "form",
    "nav",
    "aside",
    ".ad",
    ".ads",
    ".advertisement",
    ".newsletter",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

CATEGORY_MAP = {
    "秀场/系列": "runway-collection",
    "街拍/造型": "street-style",
    "趋势总结": "trend-summary",
    "品牌/市场": "brand-market",
    "高端时装": "runway-collection",
    "潮流街头": "street-style",
    "行业动态": "brand-market",
    "男装风尚": "street-style",
    "先锋文化": "trend-summary",
}
DEFAULT_ARTICLE_CONTENT_TYPE = "general-fashion"


class MissingCollectionDependencyError(RuntimeError):
    """Raised when an optional collection dependency is required at runtime."""


@dataclass(frozen=True)
class FetchedResponse:
    url: str
    text: str
    headers: dict[str, str]


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value] if value != "" else []


def slugify(text: Any) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower())
    return value.strip("-") or "source"


def _validate_content_type(headers: dict[str, str]) -> None:
    ctype = headers.get("content-type", "").lower()
    if ctype and "html" not in ctype and "xml" not in ctype:
        raise ValueError(f"Unsupported content type: {ctype}")


async def fetch_html_async(
    url: str,
    *,
    session: aiohttp.ClientSession | None = None,
    timeout: int = DEFAULT_FETCH_TIMEOUT,
) -> FetchedResponse:
    owns_session = session is None
    timeout_config = aiohttp.ClientTimeout(total=timeout)
    client = session or aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config)
    try:
        async with client.get(url) as response:
            response.raise_for_status()
            headers = dict(response.headers)
            _validate_content_type(headers)
            return FetchedResponse(
                url=str(response.url),
                text=await response.text(),
                headers=headers,
            )
    finally:
        if owns_session:
            await client.close()


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Synchronous news collection helpers cannot be called from an active event loop.")


def fetch_html(url: str, timeout: int = DEFAULT_FETCH_TIMEOUT) -> FetchedResponse:
    return _run_async(fetch_html_async(url, timeout=timeout))


def clean_html(html_content: str) -> str:
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["script", "style", "img", "video", "audio", "iframe", "input", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()[:3000]


def _require_feedparser() -> Any:
    try:
        return importlib.import_module("feedparser")
    except ModuleNotFoundError as exc:
        raise MissingCollectionDependencyError(
            "feedparser is required to fetch RSS sources. Install the RSS collection dependency to use RSS sources."
        ) from exc


def normalize_url(url: str, extra_strip_params: list[str] | None = None) -> str:
    if not url:
        return ""

    extra_strip = {str(param).lower() for param in ensure_list(extra_strip_params)}
    parsed = urlparse(str(url).strip())
    if not parsed.scheme or not parsed.netloc:
        return ""

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered in extra_strip:
            continue
        if lowered in COMMON_TRACKING_QUERY_KEYS:
            continue
        if any(lowered.startswith(prefix) for prefix in COMMON_TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))

    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")

    query = urlencode(query_items, doseq=True)
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        "",
        query,
        "",
    ))


def normalize_title(title: str) -> str:
    text = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def content_digest(text: str) -> str:
    normalized = normalize_title((text or "")[:1200])
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def compute_article_id(*parts: str) -> str:
    raw = "||".join((part or "").strip() for part in parts if part)
    if not raw:
        raw = str(time.time())
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def within_days(date_a: str, date_b: str, days: int = 2) -> bool:
    if not date_a or not date_b:
        return False
    try:
        a = datetime.datetime.fromisoformat(date_a.replace("Z", "+00:00"))
        b = datetime.datetime.fromisoformat(date_b.replace("Z", "+00:00"))
        return abs((a - b).total_seconds()) <= days * 86400
    except Exception:
        return False


def parse_isoish_date(value: Any) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    for fmt in (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            if fmt is None:
                return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
            return datetime.datetime.strptime(raw, fmt).isoformat()
        except Exception:
            continue
    return raw


def _matches_patterns(text: str, patterns: list[str]) -> bool:
    pats = ensure_list(patterns)
    if not pats:
        return True
    return any(re.search(pattern, text, re.I) for pattern in pats)


def _is_excluded_by_patterns(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in ensure_list(patterns))


def _normalize_allowed_domains(domains: list[str], urls: list[str]) -> list[str]:
    values = [urlparse(url).netloc.lower() for url in ensure_list(urls) if urlparse(url).netloc]
    values.extend([str(domain).lower() for domain in ensure_list(domains)])
    return sorted(set(filter(None, values)))


def _selector_text(node: Any) -> str:
    if not node:
        return ""
    for attr in ("content", "datetime", "title", "alt", "href", "src"):
        value = node.get(attr, "")
        if value:
            return str(value).strip()
    return " ".join(node.stripped_strings).strip()


def _select_first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in ensure_list(selectors):
        node = soup.select_one(selector)
        value = _selector_text(node)
        if value:
            return value
    return ""


def _select_first_image(soup: BeautifulSoup, selectors: list[str], base_url: str = "") -> str:
    for selector in ensure_list(selectors):
        node = soup.select_one(selector)
        if not node:
            continue
        if node.name == "img":
            candidate = _extract_img_from_tag(node, base_url)
            if candidate:
                return candidate
        else:
            candidate = normalize_image_url(_selector_text(node), base_url)
            if is_valid_image_url(candidate):
                return candidate
    return ""


def _normalize_candidate_url(
    raw_url: str,
    page_url: str,
    extra_strip_params: list[str] | None = None,
) -> str:
    raw_value = str(raw_url or "").strip()
    if not raw_value:
        return ""
    return normalize_url(urljoin(page_url, raw_value), extra_strip_params)


def extract_canonical_url(
    soup: BeautifulSoup,
    page_url: str,
    extra_strip_params: list[str] | None = None,
) -> str:
    candidates = [
        _normalize_candidate_url(tag.get("href", ""), page_url, extra_strip_params)
        for tag in soup.find_all("link", attrs={"rel": re.compile("canonical", re.I)})
    ]
    candidates.extend([
        _normalize_candidate_url(tag.get("content", ""), page_url, extra_strip_params)
        for tag in soup.find_all("meta", attrs={"property": re.compile("og:url", re.I)})
    ])
    for candidate in candidates:
        if candidate:
            return candidate
    return normalize_url(page_url, extra_strip_params)


def _extract_published_from_soup(soup: BeautifulSoup, selectors: list[str] | None = None) -> str:
    configured = parse_isoish_date(_select_first_text(soup, selectors or []))
    if configured:
        return configured

    for attrs in (
        {"property": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "pubdate"},
        {"name": "publish-date"},
        {"itemprop": "datePublished"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return parse_isoish_date(tag.get("content"))

    time_tag = soup.find("time")
    if time_tag:
        return parse_isoish_date(time_tag.get("datetime") or time_tag.get_text(" ", strip=True))

    return ""


def _extract_content_text(
    soup: BeautifulSoup,
    selectors: list[str] | None = None,
    remove_selectors: list[str] | None = None,
) -> str:
    selector_list = ensure_list(selectors) or list(DEFAULT_CONTENT_SELECTORS)
    for selector in selector_list:
        node = soup.select_one(selector)
        if not node:
            continue
        node_soup = BeautifulSoup(str(node), "html.parser")
        for remove_selector in ensure_list(remove_selectors) or list(DEFAULT_REMOVE_SELECTORS):
            for tag in node_soup.select(remove_selector):
                tag.decompose()
        text = clean_html(str(node_soup))
        if len(text) >= 120:
            return text

    for container in (soup.find("article"), soup.find("main"), soup.body):
        if not container:
            continue
        paragraphs = []
        for paragraph in container.find_all(["p", "h2", "h3"]):
            text = " ".join(paragraph.stripped_strings).strip()
            if len(text) >= 30:
                paragraphs.append(text)
        joined = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()
        if len(joined) >= 120:
            return joined[:3000]
    return ""


def parse_article_page(
    html: str,
    page_url: str,
    detail_cfg: dict[str, Any] | None = None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, str]:
    fallback = fallback or {}
    detail_cfg = detail_cfg or {}
    soup = BeautifulSoup(html, "html.parser")

    title = _select_first_text(soup, detail_cfg.get("title_selectors")) or ""
    if not title:
        title = (
            _select_first_text(soup, ["meta[property='og:title']", "meta[name='twitter:title']"])
            or " ".join((soup.find("h1") or soup.find("title") or soup.new_tag("span")).stripped_strings).strip()
        )

    content_text = _extract_content_text(
        soup,
        detail_cfg.get("content_selectors"),
        detail_cfg.get("remove_selectors"),
    )
    published = _extract_published_from_soup(soup, detail_cfg.get("published_selectors"))
    image = _select_first_image(soup, detail_cfg.get("image_selectors"), page_url)
    if not image:
        image = _extract_image_from_article_page_fields(soup, page_url)

    return {
        "canonical_url": extract_canonical_url(soup, page_url, detail_cfg.get("strip_query_params")),
        "title": title or fallback.get("title", ""),
        "published": published or fallback.get("published", ""),
        "content_text": content_text or fallback.get("content_text", ""),
        "image": image or fallback.get("image", ""),
    }


def normalize_image_url(url: str, base_url: str = "") -> str:
    if not url:
        return ""
    raw = str(url).strip()
    if not raw or raw.startswith(("data:", "javascript:")):
        return ""
    if raw.startswith("//"):
        scheme = urlparse(base_url).scheme or "https"
        raw = f"{scheme}:{raw}"
    elif raw.startswith("/"):
        raw = urljoin(base_url, raw)
    elif not raw.startswith(("http://", "https://")):
        raw = urljoin(base_url, raw)
    raw = raw.split("#", 1)[0].strip()
    if raw.startswith(("http://", "https://")):
        return raw
    return ""


def is_valid_image_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    if not parsed.scheme.startswith("http"):
        return False
    if any(bad in host for bad in [
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "adnxs.com",
    ]):
        return False
    if any(bad in path for bad in ["/ads/", "/ad/", "pixel", "tracking", "spacer", "blank"]):
        return False
    if any(bad in query for bad in ["gampad", "adunit", "iu=", "sz="]):
        return False
    if path.endswith((".svg", ".ico")):
        return False
    return True


def _pick_from_srcset(srcset: str, base_url: str = "") -> str:
    if not srcset:
        return ""
    best_url = ""
    best_width = -1
    for part in srcset.split(","):
        segment = part.strip()
        if not segment:
            continue
        bits = segment.split()
        candidate = normalize_image_url(bits[0], base_url)
        if not candidate:
            continue
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                width = int(bits[1][:-1])
            except Exception:
                width = 0
        if width >= best_width:
            best_url = candidate
            best_width = width
    return best_url if is_valid_image_url(best_url) else ""


def _extract_img_from_tag(img: Any, base_url: str = "") -> str:
    if not img:
        return ""
    for attr in ["src", "data-src", "data-original", "data-lazy-src", "data-url"]:
        candidate = normalize_image_url(img.get(attr, ""), base_url)
        if is_valid_image_url(candidate):
            return candidate
    for attr in ["srcset", "data-srcset"]:
        candidate = _pick_from_srcset(img.get(attr, ""), base_url)
        if candidate:
            return candidate
    return ""


def _image_seems_decorative(img: Any) -> bool:
    attrs_blob = " ".join([
        str(img.get("class", "")),
        str(img.get("id", "")),
        str(img.get("alt", "")),
        str(img.get("src", "")),
    ]).lower()
    if any(token in attrs_blob for token in ["logo", "icon", "avatar", "sprite", "pixel", "emoji"]):
        return True
    width = img.get("width", "")
    height = img.get("height", "")
    try:
        width_i = int(re.search(r"\d+", str(width)).group()) if re.search(r"\d+", str(width)) else 0
        height_i = int(re.search(r"\d+", str(height)).group()) if re.search(r"\d+", str(height)) else 0
    except Exception:
        width_i = 0
        height_i = 0
    if (width_i and width_i < 120) or (height_i and height_i < 120):
        return True
    return False


def _extract_image_from_html_fragment(html: str, base_url: str = "") -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        if _image_seems_decorative(img):
            continue
        candidate = _extract_img_from_tag(img, base_url)
        if candidate:
            return candidate
    return ""


def _collect_jsonld_images(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        image_val = node.get("image")
        if isinstance(image_val, str):
            out.append(image_val)
        elif isinstance(image_val, list):
            for item in image_val:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    out.extend([item.get("url", ""), item.get("contentUrl", "")])
        elif isinstance(image_val, dict):
            out.extend([image_val.get("url", ""), image_val.get("contentUrl", "")])
        for value in node.values():
            _collect_jsonld_images(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_jsonld_images(item, out)


def _extract_image_from_article_page_fields(soup: BeautifulSoup, page_url: str) -> str:
    for attrs in [
        {"property": "og:image"},
        {"property": "og:image:secure_url"},
        {"name": "og:image"},
        {"name": "twitter:image"},
        {"property": "twitter:image"},
        {"itemprop": "image"},
    ]:
        for tag in soup.find_all("meta", attrs=attrs):
            candidate = normalize_image_url(tag.get("content", ""), page_url)
            if is_valid_image_url(candidate):
                return candidate

    jsonld_candidates: list[str] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        text = script.string or script.get_text(" ", strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        _collect_jsonld_images(data, jsonld_candidates)
    for raw in jsonld_candidates:
        candidate = normalize_image_url(raw, page_url)
        if is_valid_image_url(candidate):
            return candidate

    for container in [soup.find("article"), soup.find("main"), soup.body, soup]:
        if not container:
            continue
        for img in container.find_all("img"):
            if _image_seems_decorative(img):
                continue
            candidate = _extract_img_from_tag(img, page_url)
            if candidate:
                return candidate
    return ""


async def _extract_image_from_article_page_async(
    link: str,
    session: aiohttp.ClientSession,
) -> str:
    try:
        resp = await fetch_html_async(link, session=session)
        page_url = resp.url or link
        soup = BeautifulSoup(resp.text, "html.parser")
        return _extract_image_from_article_page_fields(soup, page_url)
    except Exception:
        return ""


def extract_image(entry: Any) -> str:
    link = getattr(entry, "link", "")
    for attr in ["media_content", "media_thumbnail"]:
        items = getattr(entry, attr, None)
        if items:
            for item in (items if isinstance(items, list) else [items]):
                url = item.get("url", "") if isinstance(item, dict) else ""
                candidate = normalize_image_url(url, link)
                if is_valid_image_url(candidate):
                    return candidate
    if hasattr(entry, "enclosures"):
        for enclosure in entry.enclosures:
            if enclosure.get("type", "").startswith("image"):
                candidate = normalize_image_url(enclosure.get("href", ""), link)
                if is_valid_image_url(candidate):
                    return candidate
    for attr in ["content", "description", "summary"]:
        value = getattr(entry, attr, None)
        if value:
            content = value[0].get("value", "") if isinstance(value, list) else (value or "")
            if content:
                candidate = _extract_image_from_html_fragment(content, link)
                if candidate:
                    return candidate
    return ""


async def fill_missing_images_from_web_async(
    articles: list[dict[str, Any]],
    *,
    session: aiohttp.ClientSession | None = None,
) -> list[dict[str, Any]]:
    missing_indices = []
    for index, article in enumerate(articles):
        current = normalize_image_url(article.get("image", ""), article.get("link", ""))
        if is_valid_image_url(current):
            article["image"] = current
            continue
        article["image"] = ""
        missing_indices.append(index)

    if not missing_indices:
        logger.info("Image coverage: all articles already have RSS images")
        return articles

    target_indices = missing_indices[:MAX_PAGE_IMAGE_FETCH]
    skipped = len(missing_indices) - len(target_indices)
    message = f"Trying webpage image extraction for {len(target_indices)} missing-image articles"
    if skipped > 0:
        message += f" (skipped {skipped} older articles)"
    logger.info(message)

    resolved = 0
    owns_session = session is None
    timeout_config = aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT)
    client = session or aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config)
    semaphore = asyncio.Semaphore(IMAGE_FETCH_CONCURRENCY)

    async def resolve_image(index: int) -> tuple[int, str]:
        async with semaphore:
            image_url = await _extract_image_from_article_page_async(articles[index]["link"], client)
            return index, image_url or ""

    try:
        tasks = [asyncio.create_task(resolve_image(index)) for index in target_indices]
        total = len(tasks)
        for done, task in enumerate(asyncio.as_completed(tasks), 1):
            index, image_url = await task
            if image_url:
                articles[index]["image"] = image_url
                resolved += 1
            if done % 25 == 0 or done == total:
                logger.info("  Image extraction progress: %s/%s", done, total)
    finally:
        if owns_session:
            await client.close()

    logger.info("Image enrichment done: +%s article images from webpage content", resolved)
    return articles


def fill_missing_images_from_web(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _run_async(fill_missing_images_from_web_async(articles))


def get_published_date(entry: Any) -> str:
    for attr in ["published_parsed", "updated_parsed", "created_parsed"]:
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc).isoformat()
            except Exception:
                pass
    for attr in ["published", "updated", "created"]:
        value = getattr(entry, attr, None)
        if value:
            return parse_isoish_date(value)
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def title_bigrams(title: str) -> set[str]:
    text = re.sub(r"[^\w\s]", "", title.lower().strip())
    text = re.sub(r"\s+", " ", text)
    return {text[index:index + 2] for index in range(len(text) - 1)} if len(text) >= 2 else set()


def jaccard_sim(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize_detail_config(raw: dict[str, Any] | None, default_fetch_detail: bool) -> dict[str, Any]:
    raw = raw or {}
    return {
        "fetch_detail": bool(raw.get("fetch_detail", default_fetch_detail)),
        "title_selectors": ensure_list(raw.get("title_selectors") or raw.get("title_selector"))
        or list(DEFAULT_TITLE_SELECTORS),
        "content_selectors": ensure_list(raw.get("content_selectors") or raw.get("content_selector"))
        or list(DEFAULT_CONTENT_SELECTORS),
        "published_selectors": ensure_list(raw.get("published_selectors") or raw.get("published_selector"))
        or list(DEFAULT_PUBLISHED_SELECTORS),
        "image_selectors": ensure_list(raw.get("image_selectors") or raw.get("image_selector"))
        or list(DEFAULT_IMAGE_SELECTORS),
        "remove_selectors": ensure_list(raw.get("remove_selectors") or raw.get("remove_selector"))
        or list(DEFAULT_REMOVE_SELECTORS),
    }


def _normalize_rss_source(raw: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    feed_url = raw.get("feed_url") or raw.get("url")
    if not feed_url:
        raise ValueError(f"RSS source {source['name']} is missing feed_url/url")
    source.update({
        "feed_url": feed_url,
        "max_items": int(raw.get("max_items") or raw.get("max_articles") or MAX_ARTICLES_PER_FEED),
        "detail": _normalize_detail_config(raw.get("detail") or raw.get("extract"), False),
    })
    return source


def _normalize_crawl_source(raw: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    discovery_raw = raw.get("discovery") or {}
    detail_raw = raw.get("detail") or raw.get("extract") or {}
    start_urls = ensure_list(raw.get("start_urls") or discovery_raw.get("start_urls"))
    if not start_urls:
        raise ValueError(f"Crawl source {source['name']} is missing start_urls")
    source.update({
        "start_urls": start_urls,
        "max_items": int(raw.get("max_items") or raw.get("max_articles") or MAX_ARTICLES_PER_FEED),
        "detail_concurrency": int(raw.get("detail_concurrency") or 4),
        "allowed_domains": _normalize_allowed_domains(
            raw.get("allowed_domains") or discovery_raw.get("allowed_domains"),
            start_urls,
        ),
        "discovery": {
            "link_selectors": ensure_list(discovery_raw.get("link_selectors") or discovery_raw.get("link_selector"))
            or list(DEFAULT_LINK_SELECTORS),
            "article_url_patterns": ensure_list(
                discovery_raw.get("article_url_patterns")
                or discovery_raw.get("link_patterns")
                or raw.get("article_url_patterns")
                or raw.get("link_patterns")
            ),
            "exclude_patterns": ensure_list(discovery_raw.get("exclude_patterns") or raw.get("exclude_patterns")),
            "pagination_selectors": ensure_list(
                discovery_raw.get("pagination_selectors")
                or discovery_raw.get("pagination_selector")
                or discovery_raw.get("next_page_selectors")
            ),
            "max_pages": int(discovery_raw.get("max_pages") or raw.get("max_pages") or DEFAULT_CRAWL_PAGES),
        },
        "detail": _normalize_detail_config(detail_raw, True),
    })
    return source


def normalize_source_config(raw: dict[str, Any], index: int = 0) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid source entry at index {index}: expected mapping")

    source_type = str(raw.get("type") or "").strip().lower()
    if not source_type:
        source_type = "crawl" if raw.get("start_urls") else "rss"

    source = {
        "id": raw.get("id") or slugify(raw.get("name") or f"source-{index + 1}"),
        "name": raw.get("name") or f"Source {index + 1}",
        "type": source_type,
        "lang": raw.get("lang", "en"),
        "category": raw.get("category", "品牌/市场"),
        "enabled": bool(raw.get("enabled", True)),
        "priority": int(raw.get("priority", 100)),
        "dedup": {
            "strip_query_params": ensure_list((raw.get("dedup") or {}).get("strip_query_params")),
        },
    }

    if source_type == "rss":
        return _normalize_rss_source(raw, source)
    if source_type == "crawl":
        return _normalize_crawl_source(raw, source)
    raise ValueError(f"Unsupported source type for {source['name']}: {source_type}")


def _resolve_sources_file(sources_file: str | Path | None = None) -> Path:
    return Path(sources_file) if sources_file is not None else DEFAULT_SOURCES_FILE


def load_sources(*, sources_file: str | Path | None = None) -> list[dict[str, Any]]:
    source_path = _resolve_sources_file(sources_file)
    with source_path.open("r", encoding="utf-8") as file:
        raw_sources = yaml.safe_load(file) or []

    sources = [normalize_source_config(raw, index) for index, raw in enumerate(raw_sources)]
    enabled = [source for source in sources if source.get("enabled", True)]
    type_counts: dict[str, int] = {}
    for source in enabled:
        type_counts[source["type"]] = type_counts.get(source["type"], 0) + 1
    suffix = ""
    if type_counts:
        suffix = " (" + ", ".join(f"{kind}={count}" for kind, count in sorted(type_counts.items())) + ")"
    logger.info("Loaded %s enabled sources%s from %s", len(enabled), suffix, source_path)
    return enabled


def build_article_record(
    source: dict[str, Any],
    *,
    link: str,
    title: str,
    published: str = "",
    content_text: str = "",
    image: str = "",
    canonical_url: str = "",
    fallback_snippet: str = "",
) -> dict[str, Any]:
    normalized_link = normalize_url(link, source["dedup"]["strip_query_params"]) or str(link).strip()
    canonical = normalize_url(canonical_url, source["dedup"]["strip_query_params"]) or normalized_link
    cleaned_text = clean_html(content_text)
    snippet = cleaned_text[:800] if cleaned_text else clean_html(fallback_snippet)[:800]
    published_at = parse_isoish_date(published) or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    source_host = urlparse(canonical or normalized_link).netloc.lower()
    return {
        "id": compute_article_id(canonical or normalized_link, title, source["id"]),
        "title": (title or "").strip(),
        "link": normalized_link,
        "canonical_url": canonical,
        "source": source["name"],
        "source_id": source["id"],
        "source_type": source["type"],
        "source_host": source_host,
        "source_lang": source["lang"],
        "category_hint": source["category"],
        "category_id": CATEGORY_MAP.get(source["category"], "brand-market"),
        "image": normalize_image_url(image, normalized_link),
        "published": published_at,
        "content_text": cleaned_text,
        "content_snippet": snippet,
        "article_summary": "",
        "article_tags": [],
        "relevance_score": None,
        "relevance_reason": "",
        "content_type": DEFAULT_ARTICLE_CONTENT_TYPE,
        "is_relevant": True,
        "is_sensitive": False,
        "content_hash": content_digest(cleaned_text or snippet),
        "dedup_key": canonical or normalized_link,
    }


def _url_is_allowed(url: str, source: dict[str, Any]) -> bool:
    host = urlparse(url).netloc.lower()
    allowed_domains = source.get("allowed_domains", [])
    if not host:
        return False
    if not allowed_domains:
        return True
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def extract_discovery_links(html: str, page_url: str, source: dict[str, Any]) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    discovery = source["discovery"]
    discovered = []
    seen = set()
    for selector in discovery["link_selectors"]:
        for node in soup.select(selector):
            href = normalize_url(urljoin(page_url, node.get("href", "")), source["dedup"]["strip_query_params"])
            if not href or href in seen:
                continue
            if not _url_is_allowed(href, source):
                continue
            if _is_excluded_by_patterns(href, discovery["exclude_patterns"]):
                continue
            if not _matches_patterns(href, discovery["article_url_patterns"]):
                continue
            title = " ".join(node.stripped_strings).strip()
            seen.add(href)
            discovered.append({"url": href, "title": title})
    return discovered


def extract_pagination_links(html: str, page_url: str, source: dict[str, Any]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for selector in source["discovery"]["pagination_selectors"]:
        for node in soup.select(selector):
            href = normalize_url(urljoin(page_url, node.get("href", "")), source["dedup"]["strip_query_params"])
            if not href or href in seen or not _url_is_allowed(href, source):
                continue
            seen.add(href)
            links.append(href)
    return links


async def fetch_article_detail_async(
    source: dict[str, Any],
    link: str,
    session: aiohttp.ClientSession,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = fallback or {}
    try:
        resp = await fetch_html_async(link, session=session)
        page_url = resp.url or link
        detail_cfg = dict(source["detail"])
        detail_cfg["strip_query_params"] = source["dedup"]["strip_query_params"]
        detail = parse_article_page(resp.text, page_url, detail_cfg, fallback=fallback)
        return build_article_record(
            source,
            link=page_url,
            title=detail["title"] or fallback.get("title", ""),
            published=detail["published"] or fallback.get("published", ""),
            content_text=detail["content_text"] or fallback.get("content_text", ""),
            image=detail["image"] or fallback.get("image", ""),
            canonical_url=detail["canonical_url"] or fallback.get("canonical_url", ""),
            fallback_snippet=fallback.get("content_text", "") or fallback.get("fallback_snippet", ""),
        )
    except Exception as exc:
        logger.warning("  [%s] detail fetch failed for %s: %s", source["name"], link, exc)
        return build_article_record(
            source,
            link=link,
            title=fallback.get("title", ""),
            published=fallback.get("published", ""),
            content_text=fallback.get("content_text", ""),
            image=fallback.get("image", ""),
            canonical_url=fallback.get("canonical_url", ""),
            fallback_snippet=fallback.get("fallback_snippet", ""),
        )


def fetch_article_detail(
    source: dict[str, Any],
    link: str,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async def _fetch() -> dict[str, Any]:
        timeout_config = aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config) as session:
            return await fetch_article_detail_async(source, link, session, fallback)

    return _run_async(_fetch())


def _build_rss_fallback(source: dict[str, Any], entry: Any) -> dict[str, str]:
    link = getattr(entry, "link", "")
    title = getattr(entry, "title", "")

    content = ""
    if hasattr(entry, "content") and entry.content:
        content = entry.content[0].get("value", "")
    elif hasattr(entry, "description"):
        content = entry.description or ""
    elif hasattr(entry, "summary"):
        content = entry.summary or ""

    cleaned_content = clean_html(content)
    return {
        "title": title.strip(),
        "published": get_published_date(entry),
        "content_text": cleaned_content,
        "image": extract_image(entry),
        "canonical_url": normalize_url(link, source["dedup"]["strip_query_params"]),
        "fallback_snippet": cleaned_content[:800],
    }


async def _fetch_rss_detail_articles(
    source: dict[str, Any],
    entries: list[Any],
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    limit = max(1, source.get("detail_concurrency", 4))
    semaphore = asyncio.Semaphore(limit)

    async def fetch_entry(entry: Any) -> dict[str, Any] | None:
        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "")
        if not link or not title:
            return None

        fallback = _build_rss_fallback(source, entry)
        async with semaphore:
            article = await fetch_article_detail_async(source, link, session, fallback)
        if article["title"] and article["link"]:
            return article
        return None

    tasks = [asyncio.create_task(fetch_entry(entry)) for entry in entries]
    articles: list[dict[str, Any]] = []
    for task in asyncio.as_completed(tasks):
        article = await task
        if article:
            articles.append(article)
    return articles


async def fetch_rss_source_async(
    source: dict[str, Any],
    *,
    session: aiohttp.ClientSession | None = None,
) -> list[dict[str, Any]]:
    parser = _require_feedparser()
    articles: list[dict[str, Any]] = []
    owns_session = session is None
    timeout_config = aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT)
    client = session or aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config)
    try:
        resp = await fetch_html_async(source["feed_url"], session=client)
        feed = parser.parse(resp.text)
        entries = list(feed.entries[:source["max_items"]])
        if source["detail"]["fetch_detail"]:
            articles = await _fetch_rss_detail_articles(source, entries, client)
        else:
            for entry in entries:
                link = getattr(entry, "link", "")
                title = getattr(entry, "title", "")
                if not link or not title:
                    continue
                fallback = _build_rss_fallback(source, entry)
                article = build_article_record(
                    source,
                    link=link,
                    title=fallback["title"],
                    published=fallback["published"],
                    content_text=fallback["content_text"],
                    image=fallback["image"],
                    canonical_url=fallback["canonical_url"],
                    fallback_snippet=fallback["fallback_snippet"],
                )
                if article["title"] and article["link"]:
                    articles.append(article)
        logger.info("  [%s] %s articles via RSS", source["name"], len(articles))
    except Exception as exc:
        logger.error("  [%s] RSS error: %s", source["name"], exc)
    finally:
        if owns_session:
            await client.close()
    return articles


def fetch_rss_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    return _run_async(fetch_rss_source_async(source))


async def _fetch_crawl_detail_articles(
    source: dict[str, Any],
    discovered: list[dict[str, str]],
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    limit = min(source.get("detail_concurrency", 4), max(1, len(discovered)))
    semaphore = asyncio.Semaphore(max(1, limit))

    async def fetch_item(item: dict[str, str]) -> dict[str, Any]:
        async with semaphore:
            return await fetch_article_detail_async(
                source,
                item["url"],
                session,
                {
                    "title": item.get("title", ""),
                    "canonical_url": item["url"],
                },
            )

    tasks = [asyncio.create_task(fetch_item(item)) for item in discovered]
    articles: list[dict[str, Any]] = []
    for task in asyncio.as_completed(tasks):
        article = await task
        if article["title"] and article["link"]:
            articles.append(article)
    return articles


async def fetch_crawl_source_async(
    source: dict[str, Any],
    *,
    session: aiohttp.ClientSession | None = None,
) -> list[dict[str, Any]]:
    queue = list(source["start_urls"])
    seen_pages: set[str] = set()
    seen_articles: set[str] = set()
    discovered: list[dict[str, str]] = []
    owns_session = session is None
    timeout_config = aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT)
    client = session or aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config)

    try:
        while queue and len(seen_pages) < source["discovery"]["max_pages"] and len(discovered) < source["max_items"]:
            page = queue.pop(0)
            normalized_page = normalize_url(page, source["dedup"]["strip_query_params"])
            if not normalized_page or normalized_page in seen_pages:
                continue
            seen_pages.add(normalized_page)
            try:
                resp = await fetch_html_async(page, session=client)
            except Exception as exc:
                logger.warning("  [%s] crawl discovery failed for %s: %s", source["name"], page, exc)
                continue

            page_url = resp.url or page
            for item in extract_discovery_links(resp.text, page_url, source):
                if item["url"] in seen_articles:
                    continue
                seen_articles.add(item["url"])
                discovered.append(item)
                if len(discovered) >= source["max_items"]:
                    break

            if len(seen_pages) < source["discovery"]["max_pages"]:
                for next_page in extract_pagination_links(resp.text, page_url, source):
                    if next_page not in seen_pages and next_page not in queue:
                        queue.append(next_page)
    finally:
        if owns_session:
            await client.close()

    if not discovered:
        logger.warning("  [%s] no article URLs discovered", source["name"])
        return []

    logger.info("  [%s] discovered %s candidate article links", source["name"], len(discovered))
    if owns_session:
        async with aiohttp.ClientSession(
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT),
        ) as detail_session:
            articles = await _fetch_crawl_detail_articles(source, discovered, detail_session)
    else:
        articles = await _fetch_crawl_detail_articles(source, discovered, client)

    logger.info("  [%s] %s articles via crawl", source["name"], len(articles))
    return articles


def fetch_crawl_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    return _run_async(fetch_crawl_source_async(source))


async def fetch_source_async(
    source: dict[str, Any],
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    if source["type"] == "crawl":
        return await fetch_crawl_source_async(source, session=session)
    return await fetch_rss_source_async(source, session=session)


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    async def _fetch() -> list[dict[str, Any]]:
        timeout_config = aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config) as session:
            return await fetch_source_async(source, session)

    return _run_async(_fetch())


async def fetch_all_sources_async(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    all_articles: list[dict[str, Any]] = []
    logger.info("Fetching from %s sources...", len(sources))
    if not sources:
        return all_articles

    timeout_config = aiohttp.ClientTimeout(total=DEFAULT_FETCH_TIMEOUT)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout_config) as session:
        tasks = [asyncio.create_task(fetch_source_async(source, session)) for source in sources]
        for task in asyncio.as_completed(tasks):
            all_articles.extend(await task)
    logger.info("Total raw articles: %s", len(all_articles))
    return all_articles


def fetch_all_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _run_async(fetch_all_sources_async(sources))


def _is_probable_same_article(
    article: dict[str, Any],
    title_bg: set[str],
    seen_title_records: list[dict[str, Any]],
) -> bool:
    normalized = normalize_title(article.get("title", ""))
    for other in seen_title_records:
        same_scope = (
            other["source_id"] == article.get("source_id")
            or other["source_host"] == article.get("source_host")
        )
        if not same_scope:
            continue
        if not within_days(article.get("published", ""), other["published"], 3):
            continue
        if article.get("content_hash") and article.get("content_hash") == other["content_hash"]:
            return True
        if normalized and normalized == other["normalized_title"]:
            return True
        if jaccard_sim(title_bg, other["title_bigrams"]) > TITLE_DEDUP_THRESHOLD:
            return True
    return False


def deduplicate_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_primary_keys = set()
    seen_title_records = []
    deduped = []
    removed_primary = 0
    removed_fuzzy = 0

    for article in sorted(articles, key=lambda item: item.get("published", ""), reverse=True):
        primary = article.get("dedup_key") or article.get("canonical_url") or article.get("link", "")
        if primary and primary in seen_primary_keys:
            removed_primary += 1
            continue

        title_bg = title_bigrams(article.get("title", ""))
        if _is_probable_same_article(article, title_bg, seen_title_records):
            removed_fuzzy += 1
            continue

        if primary:
            seen_primary_keys.add(primary)
        seen_title_records.append({
            "source_id": article.get("source_id"),
            "source_host": article.get("source_host"),
            "published": article.get("published", ""),
            "normalized_title": normalize_title(article.get("title", "")),
            "title_bigrams": title_bg,
            "content_hash": article.get("content_hash", ""),
        })
        deduped.append(article)

    logger.info(
        "After dedup: %s articles (removed %s; canonical=%s, fuzzy=%s)",
        len(deduped),
        len(articles) - len(deduped),
        removed_primary,
        removed_fuzzy,
    )
    return deduped


def call_llm(
    messages: list[dict[str, str]],
    *,
    llm_config: Any | None = None,
    model_config: Any | None = None,
) -> str | None:
    llm_config = llm_config or get_news_collection_llm_config()
    model_config = model_config or llm_config.article_analysis.model
    if not llm_config.is_configured:
        return None
    headers = {
        "Authorization": f"Bearer {llm_config.provider.api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": llm_config.provider.http_referer,
    }
    payload = {
        "model": model_config.model,
        "messages": messages,
        "temperature": model_config.temperature,
        "max_tokens": model_config.max_tokens,
    }
    for attempt in range(3):
        try:
            resp = requests.post(llm_config.provider.chat_url, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("LLM attempt %s failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return None


def extract_json(text: str | None) -> Any:
    if not text:
        return None
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()
    for pattern in [r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"]:
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
    return None


def _article_analysis_text(article: dict[str, Any], *, input_chars: int) -> str:
    title = (article.get("title") or "").strip()
    body = (article.get("content_text") or article.get("content_snippet") or "").strip()
    body = body[:input_chars]
    source = (article.get("source") or "").strip()
    lang = (article.get("source_lang") or "").strip()
    category = (article.get("category_hint") or "").strip()
    published = (article.get("published") or "").strip()
    return (
        f"source: {source} ({lang})\n"
        f"published: {published}\n"
        f"category_hint: {category}\n"
        f"title: {title}\n"
        f"content: {body}"
    )


def apply_article_analysis(article: dict[str, Any], analysis: dict[str, Any] | None) -> dict[str, Any]:
    enriched = dict(article)
    if not analysis:
        return enriched

    keep = bool(analysis.get("keep", True))
    is_sensitive = bool(analysis.get("is_sensitive", False))
    if is_sensitive:
        keep = False

    category_name = analysis.get("category") or enriched.get("category_hint") or "品牌/市场"
    category_id = CATEGORY_MAP.get(category_name, enriched.get("category_id", "brand-market"))
    summary = (analysis.get("summary_zh") or "").strip()
    tags = analysis.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    relevance_score = analysis.get("relevance_score")
    try:
        relevance_score = int(relevance_score) if relevance_score is not None else None
    except Exception:
        relevance_score = None

    enriched.update({
        "article_summary": summary,
        "article_tags": [str(tag).strip() for tag in tags if str(tag).strip()][:8],
        "category_hint": category_name,
        "category_id": category_id,
        "relevance_score": relevance_score,
        "relevance_reason": (analysis.get("reason") or "").strip(),
        "content_type": (analysis.get("content_type") or DEFAULT_ARTICLE_CONTENT_TYPE).strip(),
        "is_relevant": keep,
        "is_sensitive": is_sensitive,
    })
    return enriched


def analyze_single_article(article: dict[str, Any], llm_config: Any | None = None) -> dict[str, Any]:
    llm_config = llm_config or get_news_collection_llm_config()
    if not llm_config.is_configured:
        return article

    analysis_config = llm_config.article_analysis
    result = call_llm(
        analysis_config.build_messages(
            article_text=_article_analysis_text(
                article,
                input_chars=analysis_config.model.input_chars,
            )
        ),
        llm_config=llm_config,
        model_config=analysis_config.model,
    )

    return apply_article_analysis(article, extract_json(result))


def enrich_and_filter_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not articles:
        return []
    llm_config = get_news_collection_llm_config()
    if not llm_config.is_configured:
        logger.warning("OPENROUTER_API_KEY is empty; article-level LLM enrichment is skipped.")
        return articles

    logger.info("Analyzing %s articles for relevance, summary, and category...", len(articles))
    enriched = []
    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as executor:
        futures = {
            executor.submit(analyze_single_article, article, llm_config): article
            for article in articles
        }
        total = len(futures)
        for index, future in enumerate(as_completed(futures), 1):
            try:
                enriched.append(future.result())
            except Exception as exc:
                logger.warning("Article analysis failed: %s", exc)
                enriched.append(futures[future])
            if index % 20 == 0 or index == total:
                logger.info("  Article analysis progress: %s/%s", index, total)

    kept = [article for article in enriched if article.get("is_relevant", True)]
    dropped = [article for article in enriched if not article.get("is_relevant", True)]
    logger.info("Article analysis kept %s/%s articles", len(kept), len(enriched))
    if dropped:
        for article in dropped[:10]:
            logger.info(
                "  Dropped: %s | score=%s | reason=%s",
                article.get("title", "")[:80],
                article.get("relevance_score"),
                article.get("relevance_reason", "")[:80],
            )

    kept.sort(key=lambda item: item.get("published", ""), reverse=True)
    return kept


def collect_articles(*, sources_file: str | Path | None = None) -> list[dict[str, Any]]:
    sources = load_sources(sources_file=sources_file)
    if not sources:
        logger.warning("No enabled sources available for collection")
        return []

    raw_articles = fetch_all_sources(sources)
    if not raw_articles:
        logger.warning("No articles fetched from configured sources")
        return []

    articles = deduplicate_articles(raw_articles)
    articles = fill_missing_images_from_web(articles)
    articles = enrich_and_filter_articles(articles)
    logger.info("Collection pipeline produced %s articles", len(articles))
    return articles
