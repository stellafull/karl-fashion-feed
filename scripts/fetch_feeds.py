#!/usr/bin/env python3
"""
Fashion Feed Aggregator v6 — Multi-Source Acquisition Pipeline
==============================================================
Pipeline:
  1. Load source definitions from sources.yaml
  2. Discover articles via RSS or configuration-driven web crawl
  3. Normalize article pages and canonical URLs
  4. Deduplicate by canonical URL first, then conservative title matching
  5. OpenRouter semantic embedding → Agglomerative Clustering (cosine distance)
  6. LLM verification: for multi-article clusters only, confirm or split
  7. LLM batch summary: process clusters in batches of 5 concurrently
  8. Output feed-data.json for the frontend (up to 500 topics)
"""

import os, sys, json, hashlib, re, datetime, time, logging
from urllib.parse import parse_qsl, urlencode, urlparse, urljoin, urlunparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml, requests, feedparser, numpy as np
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import AgglomerativeClustering

# ─── Configuration ───────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_EMBEDDING_URL = "https://openrouter.ai/api/v1/embeddings"
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.0-flash-001")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-large")
REQUIRE_SEMANTIC_EMBEDDING = os.environ.get("REQUIRE_SEMANTIC_EMBEDDING", "0").strip().lower() in ("1", "true", "yes")

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "client" / "public"
OUTPUT_FILE = OUTPUT_DIR / "feed-data.json"
SOURCES_FILE = SCRIPT_DIR / "sources.yaml"

MAX_ARTICLES_PER_FEED = 30
MAX_TOPICS = 500
LLM_CONCURRENCY = 5  # concurrent LLM calls
IMAGE_FETCH_CONCURRENCY = 12
MAX_PAGE_IMAGE_FETCH = 200
EMBEDDING_INPUT_CHARS = 1800
EMBEDDING_BATCH_SIZE = 64
ARTICLE_ANALYSIS_INPUT_CHARS = 2200

# Clustering distance threshold: lower is stricter; higher merges more aggressively.
SEMANTIC_CLUSTER_DISTANCE_THRESHOLD = 0.32
TFIDF_CLUSTER_DISTANCE_THRESHOLD = 0.78
TITLE_DEDUP_THRESHOLD = 0.6
DEFAULT_CRAWL_PAGES = 2
DEFAULT_FETCH_TIMEOUT = 20
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
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

CATEGORIES = [
    {"id": "all", "name": "全部", "icon": "Newspaper"},
    {"id": "runway-collection", "name": "秀场/系列", "icon": "Sparkles"},
    {"id": "street-style", "name": "街拍/造型", "icon": "Camera"},
    {"id": "trend-summary", "name": "趋势总结", "icon": "TrendingUp"},
    {"id": "brand-market", "name": "品牌/市场", "icon": "Building2"},
]

CATEGORY_MAP = {
    "秀场/系列": "runway-collection", "街拍/造型": "street-style",
    "趋势总结": "trend-summary", "品牌/市场": "brand-market",
    "高端时装": "runway-collection", "潮流街头": "street-style",
    "行业动态": "brand-market", "男装风尚": "street-style", "先锋文化": "trend-summary",
}
CATEGORY_NAME_MAP = {
    "runway-collection": "秀场/系列", "street-style": "街拍/造型",
    "trend-summary": "趋势总结", "brand-market": "品牌/市场",
}
DEFAULT_ARTICLE_CONTENT_TYPE = "general-fashion"


# ─── Utilities ───────────────────────────────────────────────────────────────

def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value] if value != "" else []

def slugify(text):
    value = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower())
    return value.strip("-") or "source"

def fetch_html(url, timeout=DEFAULT_FETCH_TIMEOUT):
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "").lower()
    if ctype and "html" not in ctype and "xml" not in ctype:
        raise ValueError(f"Unsupported content type: {ctype}")
    current_encoding = (resp.encoding or "").lower()
    apparent_encoding = (getattr(resp, "apparent_encoding", None) or "").lower()
    if apparent_encoding and current_encoding in ("", "iso-8859-1", "ascii", "gb2312"):
        resp.encoding = apparent_encoding
    return resp

def clean_html(html_content):
    if not html_content: return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["script","style","img","video","audio","iframe","input","noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()[:3000]

def normalize_url(url, extra_strip_params=None):
    if not url:
        return ""

    extra_strip = {str(p).lower() for p in ensure_list(extra_strip_params)}
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

def normalize_title(title):
    text = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", text).strip()

def content_digest(text):
    normalized = normalize_title((text or "")[:1200])
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]

def compute_article_id(*parts):
    raw = "||".join((part or "").strip() for part in parts if part)
    if not raw:
        raw = str(time.time())
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

def within_days(date_a, date_b, days=2):
    if not date_a or not date_b:
        return False
    try:
        a = datetime.datetime.fromisoformat(date_a.replace("Z", "+00:00"))
        b = datetime.datetime.fromisoformat(date_b.replace("Z", "+00:00"))
        return abs((a - b).total_seconds()) <= days * 86400
    except Exception:
        return False

def parse_isoish_date(value):
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

def _matches_patterns(text, patterns):
    pats = ensure_list(patterns)
    if not pats:
        return True
    return any(re.search(pattern, text, re.I) for pattern in pats)

def _is_excluded_by_patterns(text, patterns):
    return any(re.search(pattern, text, re.I) for pattern in ensure_list(patterns))

def _normalize_allowed_domains(domains, urls):
    values = [urlparse(url).netloc.lower() for url in ensure_list(urls) if urlparse(url).netloc]
    values.extend([str(domain).lower() for domain in ensure_list(domains)])
    return sorted(set(filter(None, values)))

def _selector_text(node):
    if not node:
        return ""
    for attr in ("content", "datetime", "title", "alt", "href", "src"):
        value = node.get(attr, "")
        if value:
            return str(value).strip()
    return " ".join(node.stripped_strings).strip()

def _select_first_text(soup, selectors):
    for selector in ensure_list(selectors):
        node = soup.select_one(selector)
        value = _selector_text(node)
        if value:
            return value
    return ""

def _select_first_image(soup, selectors, base_url=""):
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

def extract_canonical_url(soup, page_url, extra_strip_params=None):
    candidates = [
        normalize_url(tag.get("href", ""), extra_strip_params)
        for tag in soup.find_all("link", attrs={"rel": re.compile("canonical", re.I)})
    ]
    candidates.extend([
        normalize_url(tag.get("content", ""), extra_strip_params)
        for tag in soup.find_all("meta", attrs={"property": re.compile("og:url", re.I)})
    ])
    for candidate in candidates:
        if candidate:
            return candidate
    return normalize_url(page_url, extra_strip_params)

def _extract_published_from_soup(soup, selectors=None):
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

def _extract_content_text(soup, selectors=None, remove_selectors=None):
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
        for p in container.find_all(["p", "h2", "h3"]):
            text = " ".join(p.stripped_strings).strip()
            if len(text) >= 30:
                paragraphs.append(text)
        joined = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()
        if len(joined) >= 120:
            return joined[:3000]
    return ""

def parse_article_page(html, page_url, detail_cfg=None, fallback=None):
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

def normalize_image_url(url, base_url=""):
    if not url: return ""
    raw = str(url).strip()
    if not raw or raw.startswith(("data:", "javascript:")): return ""
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

def is_valid_image_url(url):
    if not url: return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    if not parsed.scheme.startswith("http"): return False
    if any(bad in host for bad in [
        "doubleclick.net", "googlesyndication.com", "googleadservices.com", "adnxs.com"
    ]):
        return False
    if any(bad in path for bad in ["/ads/", "/ad/", "pixel", "tracking", "spacer", "blank"]):
        return False
    if any(bad in query for bad in ["gampad", "adunit", "iu=", "sz="]):
        return False
    if path.endswith((".svg", ".ico")):
        return False
    return True

def _pick_from_srcset(srcset, base_url=""):
    if not srcset: return ""
    best_url, best_w = "", -1
    for part in srcset.split(","):
        seg = part.strip()
        if not seg: continue
        bits = seg.split()
        candidate = normalize_image_url(bits[0], base_url)
        if not candidate: continue
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try: width = int(bits[1][:-1])
            except: width = 0
        if width >= best_w:
            best_url, best_w = candidate, width
    return best_url if is_valid_image_url(best_url) else ""

def _extract_img_from_tag(img, base_url=""):
    if not img: return ""
    for attr in ["src", "data-src", "data-original", "data-lazy-src", "data-url"]:
        candidate = normalize_image_url(img.get(attr, ""), base_url)
        if is_valid_image_url(candidate):
            return candidate
    for attr in ["srcset", "data-srcset"]:
        candidate = _pick_from_srcset(img.get(attr, ""), base_url)
        if candidate:
            return candidate
    return ""

def _image_seems_decorative(img):
    attrs_blob = " ".join([
        str(img.get("class", "")),
        str(img.get("id", "")),
        str(img.get("alt", "")),
        str(img.get("src", "")),
    ]).lower()
    if any(token in attrs_blob for token in ["logo", "icon", "avatar", "sprite", "pixel", "emoji"]):
        return True
    w = img.get("width", "")
    h = img.get("height", "")
    try:
        wi = int(re.search(r"\d+", str(w)).group()) if re.search(r"\d+", str(w)) else 0
        hi = int(re.search(r"\d+", str(h)).group()) if re.search(r"\d+", str(h)) else 0
    except:
        wi, hi = 0, 0
    if (wi and wi < 120) or (hi and hi < 120):
        return True
    return False

def _extract_image_from_html_fragment(html, base_url=""):
    if not html: return ""
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        if _image_seems_decorative(img):
            continue
        candidate = _extract_img_from_tag(img, base_url)
        if candidate:
            return candidate
    return ""

def _collect_jsonld_images(node, out):
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

def _extract_image_from_article_page_fields(soup, page_url):
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

    jsonld_candidates = []
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

def _extract_image_from_article_page(link):
    try:
        resp = fetch_html(link)
        page_url = resp.url or link
        soup = BeautifulSoup(resp.text, "html.parser")
        return _extract_image_from_article_page_fields(soup, page_url)
    except Exception:
        return ""

def extract_image(entry):
    link = getattr(entry, "link", "")
    for attr in ["media_content", "media_thumbnail"]:
        items = getattr(entry, attr, None)
        if items:
            for item in (items if isinstance(items, list) else [items]):
                url = item.get("url", "") if isinstance(item, dict) else ""
                candidate = normalize_image_url(url, link)
                if is_valid_image_url(candidate): return candidate
    if hasattr(entry, "enclosures"):
        for enc in entry.enclosures:
            if enc.get("type","").startswith("image"):
                candidate = normalize_image_url(enc.get("href",""), link)
                if is_valid_image_url(candidate): return candidate
    for attr in ["content", "description", "summary"]:
        val = getattr(entry, attr, None)
        if val:
            content = val[0].get("value","") if isinstance(val, list) else (val or "")
            if content:
                candidate = _extract_image_from_html_fragment(content, link)
                if candidate: return candidate
    return ""

def fill_missing_images_from_web(articles):
    missing_indices = []
    for i, article in enumerate(articles):
        current = normalize_image_url(article.get("image", ""), article.get("link", ""))
        if is_valid_image_url(current):
            article["image"] = current
            continue
        article["image"] = ""
        missing_indices.append(i)

    if not missing_indices:
        logger.info("Image coverage: all articles already have RSS images")
        return articles

    target_indices = missing_indices[:MAX_PAGE_IMAGE_FETCH]
    skipped = len(missing_indices) - len(target_indices)
    msg = f"Trying webpage image extraction for {len(target_indices)} missing-image articles"
    if skipped > 0:
        msg += f" (skipped {skipped} older articles)"
    logger.info(msg)

    resolved = 0
    with ThreadPoolExecutor(max_workers=IMAGE_FETCH_CONCURRENCY) as ex:
        futures = {
            ex.submit(_extract_image_from_article_page, articles[i]["link"]): i
            for i in target_indices
        }
        total = len(futures)
        for done, future in enumerate(as_completed(futures), 1):
            idx = futures[future]
            image_url = future.result() or ""
            if image_url:
                articles[idx]["image"] = image_url
                resolved += 1
            if done % 25 == 0 or done == total:
                logger.info(f"  Image extraction progress: {done}/{total}")

    logger.info(f"Image enrichment done: +{resolved} article images from webpage content")
    return articles

def get_published_date(entry):
    for attr in ["published_parsed","updated_parsed","created_parsed"]:
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.datetime(*parsed[:6]).isoformat()
            except Exception:
                pass
    for attr in ["published","updated","created"]:
        val = getattr(entry, attr, None)
        if val:
            return parse_isoish_date(val)
    return datetime.datetime.now().isoformat()

def title_bigrams(title):
    t = re.sub(r"[^\w\s]", "", title.lower().strip())
    t = re.sub(r"\s+", " ", t)
    return {t[i:i+2] for i in range(len(t)-1)} if len(t) >= 2 else set()

def jaccard_sim(a, b):
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


# ─── Source Loading / Acquisition ────────────────────────────────────────────

def _normalize_detail_config(raw, default_fetch_detail):
    raw = raw or {}
    return {
        "fetch_detail": bool(raw.get("fetch_detail", default_fetch_detail)),
        "title_selectors": ensure_list(raw.get("title_selectors") or raw.get("title_selector")) or list(DEFAULT_TITLE_SELECTORS),
        "content_selectors": ensure_list(raw.get("content_selectors") or raw.get("content_selector")) or list(DEFAULT_CONTENT_SELECTORS),
        "published_selectors": ensure_list(raw.get("published_selectors") or raw.get("published_selector")) or list(DEFAULT_PUBLISHED_SELECTORS),
        "image_selectors": ensure_list(raw.get("image_selectors") or raw.get("image_selector")) or list(DEFAULT_IMAGE_SELECTORS),
        "remove_selectors": ensure_list(raw.get("remove_selectors") or raw.get("remove_selector")) or list(DEFAULT_REMOVE_SELECTORS),
    }

def _normalize_rss_source(raw, source):
    feed_url = raw.get("feed_url") or raw.get("url")
    if not feed_url:
        raise ValueError(f"RSS source {source['name']} is missing feed_url/url")
    source.update({
        "feed_url": feed_url,
        "max_items": int(raw.get("max_items") or raw.get("max_articles") or MAX_ARTICLES_PER_FEED),
        "detail": _normalize_detail_config(raw.get("detail") or raw.get("extract"), False),
    })
    return source

def _normalize_crawl_source(raw, source):
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
            "link_selectors": ensure_list(discovery_raw.get("link_selectors") or discovery_raw.get("link_selector")) or list(DEFAULT_LINK_SELECTORS),
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

def normalize_source_config(raw, index=0):
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid source entry at index {index}: expected mapping")

    source_type = str(raw.get("type") or "").strip().lower()
    if not source_type:
        source_type = "crawl" if raw.get("start_urls") else "rss"

    source = {
        "id": raw.get("id") or slugify(raw.get("name") or f"source-{index+1}"),
        "name": raw.get("name") or f"Source {index+1}",
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

def load_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        raw_sources = yaml.safe_load(f) or []

    sources = [normalize_source_config(raw, i) for i, raw in enumerate(raw_sources)]
    enabled = [s for s in sources if s.get("enabled", True)]
    type_counts = {}
    for source in enabled:
        type_counts[source["type"]] = type_counts.get(source["type"], 0) + 1
    suffix = ""
    if type_counts:
        suffix = " (" + ", ".join(f"{kind}={count}" for kind, count in sorted(type_counts.items())) + ")"
    logger.info(f"Loaded {len(enabled)} enabled sources{suffix}")
    return enabled

def build_article_record(source, *, link, title, published="", content_text="", image="", canonical_url="", fallback_snippet=""):
    normalized_link = normalize_url(link, source["dedup"]["strip_query_params"]) or str(link).strip()
    canonical = normalize_url(canonical_url, source["dedup"]["strip_query_params"]) or normalized_link
    cleaned_text = clean_html(content_text)
    snippet = cleaned_text[:800] if cleaned_text else clean_html(fallback_snippet)[:800]
    published_at = parse_isoish_date(published) or datetime.datetime.now().isoformat()
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

def _url_is_allowed(url, source):
    host = urlparse(url).netloc.lower()
    allowed_domains = source.get("allowed_domains", [])
    if not host:
        return False
    if not allowed_domains:
        return True
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)

def extract_discovery_links(html, page_url, source):
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

def extract_pagination_links(html, page_url, source):
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

def fetch_article_detail(source, link, fallback=None):
    fallback = fallback or {}
    try:
        resp = fetch_html(link)
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
    except Exception as e:
        logger.warning(f"  [{source['name']}] detail fetch failed for {link}: {e}")
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

def fetch_rss_source(source):
    articles = []
    try:
        resp = fetch_html(source["feed_url"])
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:source["max_items"]]:
            link = getattr(entry, "link", "")
            title = getattr(entry, "title", "")
            if not link or not title:
                continue

            content = ""
            if hasattr(entry, "content") and entry.content:
                content = entry.content[0].get("value", "")
            elif hasattr(entry, "description"):
                content = entry.description or ""
            elif hasattr(entry, "summary"):
                content = entry.summary or ""

            fallback = {
                "title": title.strip(),
                "published": get_published_date(entry),
                "content_text": clean_html(content),
                "image": extract_image(entry),
                "canonical_url": normalize_url(link, source["dedup"]["strip_query_params"]),
                "fallback_snippet": clean_html(content)[:800],
            }
            if source["detail"]["fetch_detail"]:
                article = fetch_article_detail(source, link, fallback)
            else:
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
        logger.info(f"  [{source['name']}] {len(articles)} articles via RSS")
    except Exception as e:
        logger.error(f"  [{source['name']}] RSS error: {e}")
    return articles

def fetch_crawl_source(source):
    queue = list(source["start_urls"])
    seen_pages, seen_articles, discovered = set(), set(), []

    while queue and len(seen_pages) < source["discovery"]["max_pages"] and len(discovered) < source["max_items"]:
        page = queue.pop(0)
        normalized_page = normalize_url(page, source["dedup"]["strip_query_params"])
        if not normalized_page or normalized_page in seen_pages:
            continue
        seen_pages.add(normalized_page)
        try:
            resp = fetch_html(page)
        except Exception as e:
            logger.warning(f"  [{source['name']}] crawl discovery failed for {page}: {e}")
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

    if not discovered:
        logger.warning(f"  [{source['name']}] no article URLs discovered")
        return []

    logger.info(f"  [{source['name']}] discovered {len(discovered)} candidate article links")
    articles = []
    with ThreadPoolExecutor(
        max_workers=min(source.get("detail_concurrency", 4), max(1, len(discovered)))
    ) as ex:
        futures = {
            ex.submit(fetch_article_detail, source, item["url"], {
                "title": item.get("title", ""),
                "canonical_url": item["url"],
            }): item
            for item in discovered
        }
        for future in as_completed(futures):
            article = future.result()
            if article["title"] and article["link"]:
                articles.append(article)

    logger.info(f"  [{source['name']}] {len(articles)} articles via crawl")
    return articles

def fetch_source(source):
    if source["type"] == "crawl":
        return fetch_crawl_source(source)
    return fetch_rss_source(source)

def fetch_all_sources(sources):
    all_articles = []
    logger.info(f"Fetching from {len(sources)} sources...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_source, source): source for source in sources}
        for future in as_completed(futures):
            all_articles.extend(future.result())
    logger.info(f"Total raw articles: {len(all_articles)}")
    return all_articles


# ─── Deduplication ───────────────────────────────────────────────────────────

def _is_probable_same_article(article, title_bg, seen_title_records):
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

def deduplicate_articles(articles):
    seen_primary_keys = set()
    seen_title_records = []
    deduped = []
    removed_primary = 0
    removed_fuzzy = 0

    for art in sorted(articles, key=lambda x: x.get("published", ""), reverse=True):
        primary = art.get("dedup_key") or art.get("canonical_url") or art.get("link", "")
        if primary and primary in seen_primary_keys:
            removed_primary += 1
            continue

        title_bg = title_bigrams(art.get("title", ""))
        if _is_probable_same_article(art, title_bg, seen_title_records):
            removed_fuzzy += 1
            continue

        if primary:
            seen_primary_keys.add(primary)
        seen_title_records.append({
            "source_id": art.get("source_id"),
            "source_host": art.get("source_host"),
            "published": art.get("published", ""),
            "normalized_title": normalize_title(art.get("title", "")),
            "title_bigrams": title_bg,
            "content_hash": art.get("content_hash", ""),
        })
        deduped.append(art)

    logger.info(
        f"After dedup: {len(deduped)} articles "
        f"(removed {len(articles) - len(deduped)}; canonical={removed_primary}, fuzzy={removed_fuzzy})"
    )
    return deduped


# ─── Clustering (Semantic Embedding + TF-IDF fallback) ─────────────────────

def _article_embedding_text(article):
    title = (article.get("title") or "").strip()
    snippet = (
        article.get("article_summary")
        or article.get("content_snippet")
        or ""
    ).strip()[:EMBEDDING_INPUT_CHARS]
    source = (article.get("source") or "").strip()
    lang = (article.get("source_lang") or "").strip()
    content_type = (article.get("content_type") or "").strip()
    return f"source: {source} ({lang})\ntype: {content_type}\ntitle: {title}\ncontent: {snippet}"

def _clusters_from_labels(labels):
    grouped = {}
    for idx, lbl in enumerate(labels):
        grouped.setdefault(int(lbl), []).append(idx)
    return list(grouped.values())

def _log_cluster_stats(clusters, articles, label):
    multi = [c for c in clusters if len(c) > 1]
    max_size = max((len(c) for c in multi), default=0)
    logger.info(f"{label}: {len(clusters)} total, {len(multi)} multi-article (max size: {max_size})")
    if multi:
        for c in sorted(multi, key=len, reverse=True)[:10]:
            logger.info(f"  [{len(c)}x] {articles[c[0]]['title'][:60]}...")

def get_semantic_embeddings(texts):
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY is empty; semantic embedding is disabled.")
        return None
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fashion-feed.manus.space",
    }

    vectors = []
    total_batches = (len(texts) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE
    for batch_idx, start in enumerate(range(0, len(texts), EMBEDDING_BATCH_SIZE), 1):
        batch = texts[start:start + EMBEDDING_BATCH_SIZE]
        payload = {"model": EMBEDDING_MODEL, "input": batch}

        batch_vectors = None
        for attempt in range(3):
            try:
                resp = requests.post(OPENROUTER_EMBEDDING_URL, headers=headers, json=payload, timeout=120)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                ordered = sorted(data, key=lambda x: x.get("index", 0))
                extracted = [row.get("embedding") for row in ordered]
                if len(extracted) != len(batch) or any(not isinstance(v, list) or not v for v in extracted):
                    raise ValueError("Invalid embedding response shape")
                batch_vectors = extracted
                break
            except Exception as e:
                logger.warning(f"Embedding batch {batch_idx}/{total_batches} attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        if not batch_vectors:
            logger.warning(f"Embedding batch {batch_idx}/{total_batches} failed after retries.")
            return None
        vectors.extend(batch_vectors)

        if batch_idx % 5 == 0 or batch_idx == total_batches:
            logger.info(f"  Embedding progress: {batch_idx}/{total_batches} batches")

    matrix = np.array(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms

def cluster_with_semantic_embeddings(articles):
    texts = [_article_embedding_text(a) for a in articles]
    logger.info(f"Computing semantic embeddings via OpenRouter ({EMBEDDING_MODEL})...")
    matrix = get_semantic_embeddings(texts)
    if matrix is None:
        return None
    if matrix.shape[0] != len(articles):
        logger.warning(
            f"Embedding matrix row mismatch: got {matrix.shape[0]}, expected {len(articles)}"
        )
        return None

    logger.info(f"Embedding matrix: {matrix.shape}")
    logger.info(f"Agglomerative Clustering on embeddings (threshold={SEMANTIC_CLUSTER_DISTANCE_THRESHOLD})...")
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=SEMANTIC_CLUSTER_DISTANCE_THRESHOLD,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(matrix)
    result = _clusters_from_labels(labels)
    _log_cluster_stats(result, articles, "Clusters (semantic)")
    return result

def cluster_with_tfidf_fallback(articles):
    corpus = [
        f"{a['title']} {a['title']} {a['title']} {(a.get('article_summary') or a['content_snippet'])[:300]}"
        for a in articles
    ]
    logger.info("Computing TF-IDF vectors (fallback)...")
    tfidf = TfidfVectorizer(max_features=10000, ngram_range=(1,2), stop_words="english", min_df=1, max_df=0.95)
    matrix = tfidf.fit_transform(corpus)
    logger.info(f"TF-IDF matrix: {matrix.shape}")

    logger.info(f"Agglomerative Clustering fallback (threshold={TFIDF_CLUSTER_DISTANCE_THRESHOLD})...")
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=TFIDF_CLUSTER_DISTANCE_THRESHOLD,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(matrix.toarray())
    result = _clusters_from_labels(labels)
    _log_cluster_stats(result, articles, "Clusters (tfidf fallback)")
    return result

def cluster_articles(articles):
    if len(articles) <= 1:
        return [[i] for i in range(len(articles))]

    clusters = cluster_with_semantic_embeddings(articles)
    if clusters is not None:
        return clusters

    fallback_msg = "Semantic embedding unavailable; falling back to TF-IDF clustering."
    if REQUIRE_SEMANTIC_EMBEDDING:
        logger.error(f"{fallback_msg} REQUIRE_SEMANTIC_EMBEDDING=1, exiting.")
        sys.exit(2)
    logger.warning(fallback_msg)
    return cluster_with_tfidf_fallback(articles)


# ─── LLM Helpers ────────────────────────────────────────────────────────────

def call_llm(messages, temperature=0.3, max_tokens=4000):
    if not OPENROUTER_API_KEY: return None
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fashion-feed.manus.space",
    }
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    for attempt in range(3):
        try:
            resp = requests.post(OPENROUTER_CHAT_URL, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"LLM attempt {attempt+1} failed: {e}")
            if attempt < 2: time.sleep(2*(attempt+1))
    return None

def extract_json(text):
    if not text: return None
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m: text = m.group(1).strip()
    for pattern in [r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"]:
        m = re.search(pattern, text)
        if m:
            try: return json.loads(m.group(1))
            except: pass
    return None


# ─── Article-Level LLM Enrichment ───────────────────────────────────────────

def _article_analysis_text(article):
    title = (article.get("title") or "").strip()
    body = (article.get("content_text") or article.get("content_snippet") or "").strip()
    body = body[:ARTICLE_ANALYSIS_INPUT_CHARS]
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

def apply_article_analysis(article, analysis):
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

def analyze_single_article(article):
    if not OPENROUTER_API_KEY:
        return article

    prompt = f"""请判断下面这篇报道是否应保留在时尚情报平台中。

保留标准：
1. 与时尚、奢侈品、美妆、生活方式、名人风格、品牌营销、零售、秀场、趋势、文化议题相关。
2. 品牌联名、campaign、代言、跨界合作、时尚科技、Apple 等科技品牌与时尚行业的交叉内容，应视为相关。
3. 纯泛科技、纯汽车、纯财经、纯社会新闻，且与时尚产业/审美/品牌动作无明显关系，才判定为不保留。

请严格输出 JSON：
{{
  "keep": true,
  "relevance_score": 0,
  "reason": "一句中文原因",
  "summary_zh": "100字以内中文摘要",
  "category": "从以下选一个: 秀场/系列, 街拍/造型, 趋势总结, 品牌/市场",
  "tags": ["标签1", "标签2", "标签3"],
  "content_type": "如 brand-collab / fashion-tech / runway / market / celebrity-style / beauty / lifestyle / culture",
  "is_sensitive": false
}}

原始内容：
{_article_analysis_text(article)}"""

    result = call_llm([
        {
            "role": "system",
            "content": "你是轻奢品牌内部情报平台的资深编辑。你负责做文章摘要、分类和相关性判断。只输出 JSON。"
        },
        {"role": "user", "content": prompt},
    ], temperature=0.1, max_tokens=1200)

    return apply_article_analysis(article, extract_json(result))

def enrich_and_filter_articles(articles):
    if not articles:
        return []
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY is empty; article-level LLM enrichment is skipped.")
        return articles

    logger.info(f"Analyzing {len(articles)} articles for relevance, summary, and category...")
    enriched = []
    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as ex:
        futures = {ex.submit(analyze_single_article, article): article for article in articles}
        total = len(futures)
        for idx, future in enumerate(as_completed(futures), 1):
            try:
                enriched.append(future.result())
            except Exception as e:
                logger.warning(f"Article analysis failed: {e}")
                enriched.append(futures[future])
            if idx % 20 == 0 or idx == total:
                logger.info(f"  Article analysis progress: {idx}/{total}")

    kept = [article for article in enriched if article.get("is_relevant", True)]
    dropped = [article for article in enriched if not article.get("is_relevant", True)]
    logger.info(f"Article analysis kept {len(kept)}/{len(enriched)} articles")
    if dropped:
        for article in dropped[:10]:
            logger.info(
                "  Dropped: %s | score=%s | reason=%s",
                article.get("title", "")[:80],
                article.get("relevance_score"),
                article.get("relevance_reason", "")[:80],
            )

    kept.sort(key=lambda x: x.get("published", ""), reverse=True)
    return kept


# ─── LLM Verification (only for multi-article clusters) ─────────────────────

def verify_cluster(articles, cluster_indices):
    if len(cluster_indices) <= 1: return [cluster_indices]
    lines = [f"[{i}] {articles[gi]['source']} | {articles[gi]['title']}" for i, gi in enumerate(cluster_indices)]
    prompt = f"""以下文章被初步判定为同一话题。请验证它们是否确实报道了同一个具体事件或话题。

{chr(10).join(lines)}

规则：
1. 如果所有文章确实报道同一事件，回复: {{"valid": true}}
2. 如果部分文章不属于同一话题，拆分成子组: {{"valid": false, "groups": [[0,2], [1,3], [4]]}}
只输出JSON。"""
    result = call_llm([
        {"role": "system", "content": "你是时尚资讯编辑，验证文章聚合准确性。只输出JSON。"},
        {"role": "user", "content": prompt}
    ], temperature=0.1, max_tokens=1000)
    parsed = extract_json(result)
    if not parsed or parsed.get("valid", True): return [cluster_indices]
    groups = parsed.get("groups", [])
    if not groups: return [cluster_indices]
    sub_clusters, assigned = [], set()
    for group in groups:
        if isinstance(group, list):
            gis = [cluster_indices[li] for li in group if isinstance(li,int) and 0<=li<len(cluster_indices)]
            if gis: sub_clusters.append(gis)
            assigned.update(li for li in group if isinstance(li,int) and 0<=li<len(cluster_indices))
    for li in range(len(cluster_indices)):
        if li not in assigned: sub_clusters.append([cluster_indices[li]])
    return sub_clusters

def verify_all_clusters(articles, clusters):
    if not OPENROUTER_API_KEY: return clusters
    multi = [c for c in clusters if len(c) > 1]
    single = [c for c in clusters if len(c) == 1]
    logger.info(f"Verifying {len(multi)} multi-article clusters...")
    verified = []
    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as ex:
        futures = {ex.submit(verify_cluster, articles, c): c for c in multi}
        for i, future in enumerate(as_completed(futures)):
            subs = future.result()
            verified.extend(subs)
            if (i+1) % 5 == 0: logger.info(f"  Verified {i+1}/{len(multi)}")
    verified.extend(single)
    new_multi = [c for c in verified if len(c) > 1]
    logger.info(f"After verification: {len(verified)} clusters, {len(new_multi)} multi-article")
    return verified


# ─── LLM Summary Generation (concurrent) ────────────────────────────────────

def generate_summary_for_cluster(articles, cluster_indices, cluster_idx, total):
    topic_articles = [articles[i] for i in cluster_indices]
    context_parts = []
    for a in topic_articles:
        article_summary = (a.get("article_summary") or "").strip()
        tags = ", ".join(a.get("article_tags", [])[:6])
        content_for_prompt = article_summary or a["content_snippet"][:500]
        extras = []
        if article_summary:
            extras.append(f"单篇摘要: {article_summary}")
        if tags:
            extras.append(f"标签: {tags}")
        extra_text = "\n".join(extras)
        if extra_text:
            extra_text = "\n" + extra_text
        context_parts.append(
            f"来源: {a['source']} ({a['source_lang']})\n标题: {a['title']}\n内容: {content_for_prompt}{extra_text}"
        )
    context = "\n---\n".join(context_parts)

    n = len(topic_articles)
    if n == 1:
        instr = "请将这篇文章翻译/改写为流畅的中文资讯，150-250字。"
    else:
        instr = f"这{n}篇文章报道了同一话题。请综合所有来源，撰写全面的中文资讯摘要，300-500字。整合各来源独特信息。"

    prompt = f"""{instr}

原始文章：
{context}

严格按JSON格式输出：
{{"title":"中文标题（15字以内）","summary":"中文综合摘要","key_points":["要点1","要点2","要点3"],"tags":["标签1","标签2","标签3"],"category":"从以下选一个: 秀场/系列, 街拍/造型, 趋势总结, 品牌/市场","is_sensitive":false}}

注意：标题像杂志标题要吸引人；摘要整合多源信息；category根据内容判断；涉及中国政治敏感话题is_sensitive设true；tags是中文关键词。"""

    result = call_llm([
        {"role":"system","content":"你是顶级时尚杂志中文编辑，服务于轻奢品牌公司。将多源时尚资讯综合为高质量中文报道。只输出JSON。"},
        {"role":"user","content":prompt}
    ], temperature=0.3, max_tokens=2000)

    summary_data = extract_json(result)
    images = [a["image"] for a in topic_articles if a.get("image")]
    newest_date = max(a["published"] for a in topic_articles)
    sources_list = [{"name":a["source"],"title":a["title"],"link":a["link"],"lang":a["source_lang"]} for a in topic_articles]

    if summary_data and not summary_data.get("is_sensitive", False):
        cat_name = summary_data.get("category", "品牌/市场")
        cat_id = CATEGORY_MAP.get(cat_name, "brand-market")
        return {
            "id": hashlib.md5((summary_data.get("title","")[:30]+newest_date).encode()).hexdigest()[:12],
            "title": summary_data.get("title", topic_articles[0]["title"]),
            "summary": summary_data.get("summary", ""),
            "key_points": summary_data.get("key_points", []),
            "tags": summary_data.get("tags", []),
            "category": cat_id,
            "category_name": CATEGORY_NAME_MAP.get(cat_id, cat_name),
            "image": images[0] if images else "",
            "published": newest_date,
            "sources": sources_list,
            "article_count": n,
        }
    elif summary_data and summary_data.get("is_sensitive", False):
        logger.info(f"  [{cluster_idx+1}/{total}] Skipped (sensitive)")
        return None
    else:
        # Fallback
        a = topic_articles[0]
        cat_id = CATEGORY_MAP.get(a.get("category_hint","品牌/市场"), "brand-market")
        return {
            "id": a["id"], "title": a["title"],
            "summary": (a.get("article_summary") or a["content_snippet"])[:300],
            "key_points": [], "tags": [],
            "category": cat_id,
            "category_name": CATEGORY_NAME_MAP.get(cat_id, "品牌/市场"),
            "image": a.get("image",""), "published": a["published"],
            "sources": sources_list, "article_count": n,
        }


def generate_all_summaries(articles, clusters):
    if not OPENROUTER_API_KEY:
        return _fallback_no_llm(articles, clusters)

    # Sort: multi-article first (desc size), then by recency
    clusters.sort(key=lambda c: (-len(c), max(articles[i]["published"] for i in c)))
    total = min(len(clusters), MAX_TOPICS)
    logger.info(f"Generating summaries for {total} topics with {LLM_CONCURRENCY} concurrent workers...")

    topics = []
    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as ex:
        futures = {}
        for ci, cluster in enumerate(clusters[:total]):
            futures[ex.submit(generate_summary_for_cluster, articles, cluster, ci, total)] = ci

        done_count = 0
        for future in as_completed(futures):
            ci = futures[future]
            try:
                topic = future.result()
                if topic:
                    topics.append(topic)
            except Exception as e:
                logger.error(f"  Topic {ci} error: {e}")
            done_count += 1
            if done_count % 20 == 0:
                logger.info(f"  Progress: {done_count}/{total} topics processed")

    topics.sort(key=lambda t: t.get("published",""), reverse=True)
    logger.info(f"Generated {len(topics)} topics")
    return topics


def _fallback_no_llm(articles, clusters):
    topics = []
    for cluster in clusters[:MAX_TOPICS]:
        arts = [articles[i] for i in cluster]
        a = arts[0]
        cat_id = CATEGORY_MAP.get(a.get("category_hint","品牌/市场"), "brand-market")
        topics.append({
            "id": a["id"], "title": a["title"],
            "summary": (a.get("article_summary") or a["content_snippet"])[:300],
            "key_points": [], "tags": [],
            "category": cat_id,
            "category_name": CATEGORY_NAME_MAP.get(cat_id, "品牌/市场"),
            "image": a.get("image",""), "published": a["published"],
            "sources": [{"name":a2["source"],"title":a2["title"],"link":a2["link"],"lang":a2["source_lang"]} for a2 in arts],
            "article_count": len(arts),
        })
    return topics


# ─── Output ──────────────────────────────────────────────────────────────────

def build_output(topics, all_sources):
    return {
        "meta": {
            "generated_at": datetime.datetime.now().isoformat(),
            "total_topics": len(topics),
            "total_articles": sum(t["article_count"] for t in topics),
            "sources_count": len(all_sources),
            "sources": sorted(all_sources),
        },
        "categories": CATEGORIES,
        "topics": topics,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    logger.info("="*60)
    logger.info("Fashion Feed Aggregator v6 — Multi-Source Acquisition Pipeline")
    logger.info("="*60)

    sources = load_sources()
    raw_articles = fetch_all_sources(sources)
    if not raw_articles:
        logger.error("No articles fetched"); sys.exit(1)

    articles = deduplicate_articles(raw_articles)
    articles = fill_missing_images_from_web(articles)
    articles = enrich_and_filter_articles(articles)
    if not articles:
        logger.error("No articles left after article-level LLM filtering"); sys.exit(1)
    all_source_names = list(set(a["source"] for a in articles))

    clusters = cluster_articles(articles)
    verified = verify_all_clusters(articles, clusters)
    topics = generate_all_summaries(articles, verified)

    output = build_output(topics, all_source_names)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"\nOutput: {OUTPUT_FILE}")
    logger.info(f"Topics: {output['meta']['total_topics']}")
    logger.info(f"Articles covered: {output['meta']['total_articles']}")
    logger.info(f"Sources: {output['meta']['sources_count']}")

    multi_topics = [t for t in topics if t["article_count"] > 1]
    logger.info(f"Multi-source topics: {len(multi_topics)}")
    for t in multi_topics[:10]:
        src_names = list(set(s["name"] for s in t["sources"]))
        logger.info(f"  [{t['article_count']}x] {t['title']} — {', '.join(src_names)}")
    logger.info("Done!")


if __name__ == "__main__":
    main()
