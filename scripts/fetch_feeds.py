#!/usr/bin/env python3
"""
Fashion Feed Aggregator v6 — Multi-Source Event Fusion
======================================================
Pipeline:
  1. Fetch + deduplicate RSS/crawl articles
  2. Stage A candidate recall (embedding + event fingerprint)
  3. Stage B event fusion (low-cost LLM screening + boundary high-quality review)
  4. Incremental update every 2h with historical topic re-fusion
  5. Full rebuild once per UTC day for drift correction
  6. Output feed-data.json + feed-state.json + rss-snapshot.json
"""

import os, sys, json, hashlib, re, datetime, time, logging, base64
from urllib.parse import parse_qsl, urlencode, urlparse, urljoin, urlunparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations

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
LOW_COST_LLM_MODEL = os.environ.get("LOW_COST_LLM_MODEL", LLM_MODEL)
HIGH_QUALITY_LLM_MODEL = os.environ.get("HIGH_QUALITY_LLM_MODEL", LLM_MODEL)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-large")
REQUIRE_SEMANTIC_EMBEDDING = os.environ.get("REQUIRE_SEMANTIC_EMBEDDING", "0").strip().lower() in ("1", "true", "yes")
INCREMENTAL_MODE = os.environ.get("INCREMENTAL_MODE", "1").strip().lower() in ("1", "true", "yes")
FORCE_FULL_REBUILD = os.environ.get("FORCE_FULL_REBUILD", "0").strip().lower() in ("1", "true", "yes")
FULL_REBUILD_UTC_HOUR = int(os.environ.get("FULL_REBUILD_UTC_HOUR", "0"))
STATE_FILE_ENV = os.environ.get("STATE_FILE", "scripts/feed-state.json")
RSS_SNAPSHOT_FILE_ENV = os.environ.get("RSS_SNAPSHOT_FILE", "scripts/rss-snapshot.json")
TOMBSTONE_TTL_DAYS = int(os.environ.get("TOMBSTONE_TTL_DAYS", "30"))
ARTICLE_SUMMARY_ENABLED = os.environ.get("ARTICLE_SUMMARY_ENABLED", "1").strip().lower() in ("1", "true", "yes")
ARTICLE_SUMMARY_LANG = os.environ.get("ARTICLE_SUMMARY_LANG", "zh")
INCREMENTAL_ASSIGN_DISTANCE_THRESHOLD = float(os.environ.get("INCREMENTAL_ASSIGN_DISTANCE_THRESHOLD", "0.37"))
INCREMENTAL_ASSIGN_SCORE_THRESHOLD = float(os.environ.get("INCREMENTAL_ASSIGN_SCORE_THRESHOLD", "0.63"))

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "client" / "public"
OUTPUT_FILE = OUTPUT_DIR / "feed-data.json"
SOURCES_FILE = SCRIPT_DIR / "sources.yaml"
STATE_FILE = (PROJECT_DIR / STATE_FILE_ENV).resolve() if not Path(STATE_FILE_ENV).is_absolute() else Path(STATE_FILE_ENV)
RSS_SNAPSHOT_FILE = (PROJECT_DIR / RSS_SNAPSHOT_FILE_ENV).resolve() if not Path(RSS_SNAPSHOT_FILE_ENV).is_absolute() else Path(RSS_SNAPSHOT_FILE_ENV)

MAX_ARTICLES_PER_FEED = 30
MAX_TOPICS = 500
LLM_CONCURRENCY = 5  # concurrent LLM calls
IMAGE_FETCH_CONCURRENCY = 12
MAX_PAGE_IMAGE_FETCH = 200
EMBEDDING_INPUT_CHARS = 1800
EMBEDDING_BATCH_SIZE = 64
FULLTEXT_FETCH_CONCURRENCY = 10
MAX_PAGE_TEXT_FETCH = int(os.environ.get("MAX_PAGE_TEXT_FETCH", "500"))
ARTICLE_FULLTEXT_CHARS = int(os.environ.get("ARTICLE_FULLTEXT_CHARS", "8000"))
ARTICLE_EMBED_SUMMARY_CHARS = int(os.environ.get("ARTICLE_EMBED_SUMMARY_CHARS", "600"))
ARTICLE_SUMMARY_CONCURRENCY = int(os.environ.get("ARTICLE_SUMMARY_CONCURRENCY", "6"))
ARTICLE_ANALYSIS_INPUT_CHARS = 2200

# Clustering distance threshold: lower is stricter; higher merges more aggressively.
SEMANTIC_CLUSTER_DISTANCE_THRESHOLD = 0.37
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

# Stage A recall + Stage B event fusion
STAGE_A_EMBED_DISTANCE_THRESHOLD = float(os.environ.get("STAGE_A_EMBED_DISTANCE_THRESHOLD", "0.34"))
STAGE_A_CROSS_CLUSTER_SIM = float(os.environ.get("STAGE_A_CROSS_CLUSTER_SIM", "0.82"))
STAGE_A_FINGERPRINT_MERGE_SCORE = float(os.environ.get("STAGE_A_FINGERPRINT_MERGE_SCORE", "0.58"))
STAGE_B_BOUNDARY_CONF_LOW = float(os.environ.get("STAGE_B_BOUNDARY_CONF_LOW", "0.55"))
STAGE_B_BOUNDARY_CONF_HIGH = float(os.environ.get("STAGE_B_BOUNDARY_CONF_HIGH", "0.78"))
STAGE_B_HIGH_QUALITY_REVIEW_MAX = int(os.environ.get("STAGE_B_HIGH_QUALITY_REVIEW_MAX", "24"))
STAGE_B_LOW_COST_REVIEW_MAX = int(os.environ.get("STAGE_B_LOW_COST_REVIEW_MAX", "140"))
HIGH_CONF_TOPIC_THRESHOLD = float(os.environ.get("HIGH_CONF_TOPIC_THRESHOLD", "0.78"))

TOPIC_CROSS_MERGE_STRONG_SCORE = float(os.environ.get("TOPIC_CROSS_MERGE_STRONG_SCORE", "0.66"))
TOPIC_CROSS_MERGE_BOUNDARY_SCORE = float(os.environ.get("TOPIC_CROSS_MERGE_BOUNDARY_SCORE", "0.62"))
TOPIC_CROSS_MERGE_LOW_COST_MAX = int(os.environ.get("TOPIC_CROSS_MERGE_LOW_COST_MAX", "60"))
TOPIC_CROSS_MERGE_HIGH_QUALITY_MAX = int(os.environ.get("TOPIC_CROSS_MERGE_HIGH_QUALITY_MAX", "15"))

ACTION_HINTS = [
    "launch", "drops", "debut", "present", "show", "unveil", "announce", "release", "collaboration",
    "acquire", "buy", "sale", "invest", "raise", "open", "close", "appoint", "resign", "exit",
    "lawsuit", "ban", "strike", "campaign", "partnership", "capsule", "runway", "collection",
    "推出", "发布", "首发", "亮相", "联名", "收购", "任命", "离任", "开店", "关闭", "秀场", "系列",
]

ENTITY_STOPWORDS = {
    "The", "This", "That", "With", "From", "Into", "Over", "Under", "After", "Before",
    "Fashion", "Style", "Week", "News", "Report", "Review",
}

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

def normalize_title(text):
    normalized = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()

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
        a = datetime.datetime.fromisoformat(str(date_a).replace("Z", "+00:00"))
        b = datetime.datetime.fromisoformat(str(date_b).replace("Z", "+00:00"))
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


def _extract_text_from_article_page(link):
    try:
        resp = fetch_html(link)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript", "iframe", "svg", "img", "video", "audio", "form"]):
            tag.decompose()

        candidates = []
        for node in [soup.find("article"), soup.find("main"), soup.body]:
            if node:
                text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                if text:
                    candidates.append(text)
        if not candidates:
            return ""
        best = max(candidates, key=len)
        return best[:ARTICLE_FULLTEXT_CHARS]
    except Exception:
        return ""


def fill_article_fulltext_from_web(articles):
    missing_indices = [idx for idx, article in enumerate(articles) if not (article.get("full_text") or "").strip()]
    targets = missing_indices[:MAX_PAGE_TEXT_FETCH]
    if not targets:
        return articles

    logger.info(f"Fetching full article text for {len(targets)} articles...")
    with ThreadPoolExecutor(max_workers=FULLTEXT_FETCH_CONCURRENCY) as ex:
        futures = {ex.submit(_extract_text_from_article_page, articles[i]["link"]): i for i in targets}
        total = len(futures)
        for done, future in enumerate(as_completed(futures), 1):
            idx = futures[future]
            text = (future.result() or "").strip()
            if text:
                articles[idx]["full_text"] = text
            if done % 25 == 0 or done == total:
                logger.info(f"  Fulltext progress: {done}/{total}")

    for a in articles:
        if not a.get("full_text"):
            a["full_text"] = a.get("content_snippet", "")
    return articles


def _extractive_summary(text, max_chars=ARTICLE_EMBED_SUMMARY_CHARS):
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s+", raw)
    if not parts:
        return raw[:max_chars]
    out = []
    used = 0
    for seg in parts:
        seg = seg.strip()
        if not seg:
            continue
        add = len(seg) + (1 if out else 0)
        if used + add > max_chars:
            break
        out.append(seg)
        used += add
    return (" ".join(out) if out else raw[:max_chars]).strip()


def _llm_article_embedding_summary(article):
    if not OPENROUTER_API_KEY or not ARTICLE_SUMMARY_ENABLED:
        return ""
    lang_hint = "中文" if ARTICLE_SUMMARY_LANG.lower().startswith("zh") else "原文语言"
    prompt = f"""你将收到一篇时尚文章的全文节选。请输出一段{lang_hint}摘要，用于语义聚类。

要求：
1. 仅保留事件与事实，不要营销语气
2. 80-150字（中文）或等价长度（非中文）
3. 只输出摘要正文，不要JSON/markdown

标题：{article.get('title', '')}
来源：{article.get('source', '')} ({article.get('source_lang', '')})
正文：
{(article.get('full_text') or article.get('content_snippet') or '')[:3500]}
"""
    result = call_llm(
        [
            {"role": "system", "content": "你是新闻摘要编辑，擅长为聚类生成客观短摘要。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=250,
    )
    if not result:
        return ""
    cleaned = re.sub(r"```[\s\S]*?```", "", result).strip()
    return re.sub(r"\s+", " ", cleaned)[:ARTICLE_EMBED_SUMMARY_CHARS].strip()


def build_embedding_summaries(articles):
    if not articles:
        return articles

    def worker(article):
        llm_summary = _llm_article_embedding_summary(article)
        if llm_summary:
            return llm_summary
        return _extractive_summary(article.get("full_text") or article.get("content_snippet", ""))

    logger.info(f"Building article embedding summaries for {len(articles)} articles...")
    with ThreadPoolExecutor(max_workers=ARTICLE_SUMMARY_CONCURRENCY) as ex:
        futures = {ex.submit(worker, article): idx for idx, article in enumerate(articles)}
        total = len(futures)
        for done, future in enumerate(as_completed(futures), 1):
            idx = futures[future]
            summary = (future.result() or "").strip()
            articles[idx]["embedding_summary"] = summary or _extractive_summary(
                articles[idx].get("content_snippet", "")
            )
            if done % 25 == 0 or done == total:
                logger.info(f"  Embedding-summary progress: {done}/{total}")
    return articles

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


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def utc_now_iso():
    return utc_now().replace(microsecond=0).isoformat()


def parse_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def _safe_dt(value):
    dt = parse_datetime(value)
    return dt or utc_now()


def _time_bucket(value):
    dt = _safe_dt(value)
    bucket = dt.replace(minute=0, second=0, microsecond=0)
    bucket = bucket.replace(hour=(bucket.hour // 6) * 6)
    return bucket.isoformat()


def _canonical_entity(token):
    tok = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff&'\- ]", "", token or "").strip()
    if not tok:
        return ""
    if tok in ENTITY_STOPWORDS:
        return ""
    if len(tok) < 2:
        return ""
    return tok


def extract_entities_from_text(text, limit=12):
    raw = text or ""
    candidates = []
    candidates.extend(re.findall(r"\b[A-Z][A-Za-z0-9&'/-]{1,}(?:\s+[A-Z][A-Za-z0-9&'/-]{1,}){0,2}", raw))
    candidates.extend(re.findall(r"\b[A-Z]{2,}\b", raw))
    cjk_matches = re.findall(r"[\u4e00-\u9fff]{2,6}", raw)
    candidates.extend(cjk_matches[:20])
    out = []
    seen = set()
    for token in candidates:
        canon = _canonical_entity(token)
        if not canon:
            continue
        key = canon.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(canon)
        if len(out) >= limit:
            break
    return out


def extract_actions_from_text(text, limit=8):
    lowered = (text or "").lower()
    out = []
    for word in ACTION_HINTS:
        if word.lower() in lowered:
            out.append(word)
        if len(out) >= limit:
            break
    return out


def build_article_event_fingerprint(article):
    merged_text = " ".join(
        [
            article.get("title", ""),
            article.get("article_summary", ""),
            article.get("embedding_summary", ""),
            article.get("content_snippet", ""),
        ]
    )
    entities = extract_entities_from_text(merged_text)
    actions = extract_actions_from_text(merged_text)
    bucket = _time_bucket(article.get("published"))
    sources = [article.get("source", "")] if article.get("source") else []
    fp_base = "|".join(
        [
            ",".join(sorted(e.lower() for e in entities[:8])),
            ",".join(sorted(a.lower() for a in actions[:6])),
            bucket[:10],
            ",".join(sorted(sources)),
        ]
    )
    return {
        "hash": hashlib.md5(fp_base.encode()).hexdigest()[:16],
        "entities": entities,
        "actions": actions,
        "time_bucket": bucket,
        "sources": sources,
    }


def merge_event_fingerprints(fingerprints):
    entities = []
    actions = []
    sources = []
    time_buckets = []
    for fp in fingerprints:
        if not isinstance(fp, dict):
            continue
        entities.extend(fp.get("entities", []))
        actions.extend(fp.get("actions", []))
        sources.extend(fp.get("sources", []))
        if fp.get("time_bucket"):
            time_buckets.append(fp.get("time_bucket"))

    entity_set = []
    seen_entity = set()
    for ent in entities:
        key = str(ent).lower().strip()
        if key and key not in seen_entity:
            seen_entity.add(key)
            entity_set.append(ent)
        if len(entity_set) >= 16:
            break

    action_set = []
    seen_action = set()
    for act in actions:
        key = str(act).lower().strip()
        if key and key not in seen_action:
            seen_action.add(key)
            action_set.append(act)
        if len(action_set) >= 10:
            break

    source_set = sorted(set(s for s in sources if s))
    min_bucket = min(time_buckets) if time_buckets else utc_now_iso()
    max_bucket = max(time_buckets) if time_buckets else min_bucket
    fp_base = "|".join(
        [
            ",".join(sorted(e.lower() for e in entity_set[:10])),
            ",".join(sorted(a.lower() for a in action_set[:8])),
            min_bucket[:10],
            max_bucket[:10],
            ",".join(source_set),
        ]
    )
    return {
        "hash": hashlib.md5(fp_base.encode()).hexdigest()[:16],
        "entities": entity_set,
        "actions": action_set,
        "time_bucket": min_bucket,
        "time_window": {"start": min_bucket, "end": max_bucket},
        "sources": source_set,
    }


def entity_overlap_score(a, b):
    aset = set(str(x).lower() for x in (a or []))
    bset = set(str(x).lower() for x in (b or []))
    return jaccard_sim(aset, bset)


def action_overlap_score(a, b):
    aset = set(str(x).lower() for x in (a or []))
    bset = set(str(x).lower() for x in (b or []))
    return jaccard_sim(aset, bset)


def time_bucket_score(a, b):
    da = parse_datetime(a) if a else None
    db = parse_datetime(b) if b else None
    if not da or not db:
        return 0.0
    delta = abs((da - db).total_seconds()) / 3600.0
    if delta <= 12:
        return 1.0
    if delta <= 36:
        return 0.75
    if delta <= 72:
        return 0.45
    return 0.0


def fingerprint_similarity(fp_a, fp_b):
    if not fp_a or not fp_b:
        return 0.0
    ent = entity_overlap_score(fp_a.get("entities"), fp_b.get("entities"))
    act = action_overlap_score(fp_a.get("actions"), fp_b.get("actions"))
    tim = time_bucket_score(fp_a.get("time_bucket"), fp_b.get("time_bucket"))
    return 0.55 * ent + 0.2 * act + 0.25 * tim


def attach_event_fingerprints(articles):
    for article in articles:
        article["event_fingerprint"] = build_article_event_fingerprint(article)
    return articles


def _json_default_state():
    return {
        "version": 2,
        "updated_at": "",
        "last_full_rebuild_at": "",
        "seen_article_keys": [],
        "tombstones": [],
        "topics_state": [],
    }


def _json_default_snapshot():
    return {"generated_at": "", "sources": [], "articles_index": []}


def load_json_or_default(path, default_factory):
    if not path.exists():
        return default_factory()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default_factory()
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return default_factory()


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def normalize_title_for_hash(title):
    t = re.sub(r"\s+", " ", (title or "").strip().lower())
    return re.sub(r"[^\w\s]", "", t)


def article_key_for_article(article):
    norm = normalize_url(article.get("canonical_url") or article.get("link", ""))
    source = article.get("source", "")
    title_hash = hashlib.md5(normalize_title_for_hash(article.get("title", "")).encode()).hexdigest()[:12]
    return hashlib.md5(f"{source}|{norm}|{title_hash}".encode()).hexdigest()[:20], title_hash, norm


def attach_article_keys(articles):
    for art in articles:
        key, title_hash, norm_url = article_key_for_article(art)
        art["article_key"] = key
        art["title_hash"] = title_hash
        art["normalized_url"] = norm_url
    return articles


def build_rss_snapshot(articles):
    rows = []
    for a in articles:
        rows.append({
            "article_key": a.get("article_key", ""),
            "source": a.get("source", ""),
            "normalized_url": a.get("normalized_url", normalize_url(a.get("link", ""))),
            "title_hash": a.get("title_hash", ""),
            "published": a.get("published", ""),
        })
    return {
        "generated_at": utc_now_iso(),
        "sources": sorted(list(set(a.get("source", "") for a in articles if a.get("source")))),
        "articles_index": rows,
    }


def snapshot_key_set(snapshot):
    return set(row.get("article_key", "") for row in snapshot.get("articles_index", []) if row.get("article_key"))


def prune_tombstones(tombstones):
    now = utc_now()
    result = []
    for row in tombstones:
        key = row.get("article_key", "")
        if not key:
            continue
        expires = parse_datetime(row.get("expired_at"))
        if expires and expires > now:
            result.append({
                "article_key": key,
                "evicted_at": row.get("evicted_at", ""),
                "expired_at": row.get("expired_at", ""),
            })
    return result


def tombstone_key_set(tombstones):
    return set(row["article_key"] for row in tombstones if row.get("article_key"))


def add_tombstones(tombstones, article_keys):
    now = utc_now()
    expires = (now + datetime.timedelta(days=TOMBSTONE_TTL_DAYS)).replace(microsecond=0).isoformat()
    now_iso = now.replace(microsecond=0).isoformat()
    existing = {row.get("article_key"): row for row in tombstones if row.get("article_key")}
    for key in sorted(set(k for k in article_keys if k)):
        existing[key] = {"article_key": key, "evicted_at": now_iso, "expired_at": expires}
    return list(existing.values())


def vector_to_json(vector):
    if vector is None:
        return ""
    if not isinstance(vector, np.ndarray):
        vector = np.array(vector, dtype=np.float32)
    if vector.size == 0:
        return ""
    packed = vector.astype(np.float16).tobytes()
    return base64.b64encode(packed).decode("ascii")


def vector_from_json(raw):
    if not raw:
        return None
    if isinstance(raw, list):
        try:
            vec = np.array(raw, dtype=np.float32)
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            data = base64.b64decode(raw.encode("ascii"))
            if not data:
                return None
            vec = np.frombuffer(data, dtype=np.float16).astype(np.float32)
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
        except Exception:
            return None
    return None


def sort_topics_by_published(topics):
    topics.sort(key=lambda t: t.get("published", ""), reverse=True)
    return topics


def should_run_full_rebuild(state, snapshot):
    if FORCE_FULL_REBUILD:
        return True
    if not INCREMENTAL_MODE:
        return True
    if not state.get("topics_state") or not snapshot.get("articles_index"):
        return True
    now = utc_now()
    if now.hour != FULL_REBUILD_UTC_HOUR:
        return False
    last_full = parse_datetime(state.get("last_full_rebuild_at"))
    if not last_full:
        return True
    return last_full.date() != now.date()


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
        "url": feed_url,
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
    published_at = parse_isoish_date(published) or utc_now_iso()
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

def fetch_all_feeds(sources):
    return fetch_all_sources(sources)


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
        or article.get("embedding_summary")
        or article.get("full_text")
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

def cluster_with_semantic_embeddings(articles, return_matrix=False):
    texts = [_article_embedding_text(a) for a in articles]
    logger.info(f"Computing semantic embeddings via OpenRouter ({EMBEDDING_MODEL})...")
    matrix = get_semantic_embeddings(texts)
    if matrix is None:
        return (None, None) if return_matrix else None
    if matrix.shape[0] != len(articles):
        logger.warning(
            f"Embedding matrix row mismatch: got {matrix.shape[0]}, expected {len(articles)}"
        )
        return (None, None) if return_matrix else None

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
    return (result, matrix) if return_matrix else result

def cluster_with_tfidf_fallback(articles):
    corpus = [f"{a['title']} {a['title']} {a['title']} {a['content_snippet'][:300]}" for a in articles]
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


def cluster_articles_with_matrix(articles):
    if len(articles) <= 1:
        if len(articles) == 1:
            mat = get_semantic_embeddings([_article_embedding_text(articles[0])])
            return [[0]], mat
        return [], np.zeros((0, 0), dtype=np.float32)

    clusters, matrix = cluster_with_semantic_embeddings(articles, return_matrix=True)
    if clusters is not None:
        return clusters, matrix

    fallback_msg = "Semantic embedding unavailable; falling back to TF-IDF clustering."
    if REQUIRE_SEMANTIC_EMBEDDING:
        logger.error(f"{fallback_msg} REQUIRE_SEMANTIC_EMBEDDING=1, exiting.")
        sys.exit(2)
    logger.warning(fallback_msg)
    return cluster_with_tfidf_fallback(articles), None


def _union_find(parents, x):
    while parents[x] != x:
        parents[x] = parents[parents[x]]
        x = parents[x]
    return x


def _union_merge(parents, a, b):
    ra = _union_find(parents, a)
    rb = _union_find(parents, b)
    if ra != rb:
        parents[rb] = ra


def _cluster_centroid(cluster, matrix):
    if matrix is None or len(cluster) == 0:
        return None
    vecs = matrix[cluster]
    centroid = vecs.mean(axis=0)
    norm = np.linalg.norm(centroid)
    return centroid / norm if norm > 0 else centroid


def _cluster_fingerprint(cluster, articles):
    fps = [articles[i].get("event_fingerprint", {}) for i in cluster if 0 <= i < len(articles)]
    return merge_event_fingerprints(fps)


def _cluster_cohesion(cluster, matrix):
    if matrix is None or len(cluster) <= 1:
        return 1.0
    sims = []
    for i, j in combinations(cluster, 2):
        sims.append(float(np.dot(matrix[i], matrix[j])))
    return float(np.mean(sims)) if sims else 1.0


def _heuristic_cluster_confidence(cluster, articles, matrix):
    cohesion = _cluster_cohesion(cluster, matrix)
    sources = set(articles[i].get("source", "") for i in cluster if 0 <= i < len(articles))
    source_bonus = min(len([s for s in sources if s]) / 6.0, 1.0)
    conf = 0.4 + 0.4 * max(min(cohesion, 1.0), 0.0) + 0.2 * source_bonus
    return round(max(0.3, min(0.98, conf)), 3)


def stage_a_candidate_recall(articles, matrix):
    if not articles:
        return [], {"stage_a_base_clusters": 0, "stage_a_candidates": 0}
    if matrix is None or len(articles) <= 1:
        singles = [[i] for i in range(len(articles))]
        return singles, {"stage_a_base_clusters": len(singles), "stage_a_candidates": len(singles)}

    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=STAGE_A_EMBED_DISTANCE_THRESHOLD,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(matrix)
    base_clusters = _clusters_from_labels(labels)
    _log_cluster_stats(base_clusters, articles, "Stage A base clusters")

    centroids = [_cluster_centroid(cluster, matrix) for cluster in base_clusters]
    fingerprints = [_cluster_fingerprint(cluster, articles) for cluster in base_clusters]

    parents = list(range(len(base_clusters)))
    for i, j in combinations(range(len(base_clusters)), 2):
        ci = centroids[i]
        cj = centroids[j]
        sim = float(np.dot(ci, cj)) if ci is not None and cj is not None else 0.0
        fp_score = fingerprint_similarity(fingerprints[i], fingerprints[j])
        should_merge = (
            (sim >= STAGE_A_CROSS_CLUSTER_SIM and fp_score >= STAGE_A_FINGERPRINT_MERGE_SCORE)
            or sim >= (STAGE_A_CROSS_CLUSTER_SIM + 0.08)
            or fp_score >= (STAGE_A_FINGERPRINT_MERGE_SCORE + 0.18)
        )
        if should_merge:
            _union_merge(parents, i, j)

    merged = {}
    for idx, cluster in enumerate(base_clusters):
        root = _union_find(parents, idx)
        merged.setdefault(root, []).extend(cluster)

    candidates = []
    for indices in merged.values():
        unique = sorted(set(indices))
        if unique:
            candidates.append(unique)
    candidates = sorted(candidates, key=lambda c: max(articles[i].get("published", "") for i in c), reverse=True)
    logger.info(
        "Stage A recall: %d base clusters -> %d candidate groups",
        len(base_clusters),
        len(candidates),
    )
    return candidates, {"stage_a_base_clusters": len(base_clusters), "stage_a_candidates": len(candidates)}


def _normalize_llm_group_items(raw_group, cluster_indices):
    if isinstance(raw_group, dict):
        raw_items = raw_group.get("items", [])
        conf = raw_group.get("confidence")
    elif isinstance(raw_group, list):
        raw_items = raw_group
        conf = None
    else:
        return None, None

    indices = []
    seen = set()
    for item in raw_items:
        if isinstance(item, int) and 0 <= item < len(cluster_indices):
            gi = cluster_indices[item]
            if gi not in seen:
                seen.add(gi)
                indices.append(gi)
    if not indices:
        return None, None
    try:
        conf_val = float(conf) if conf is not None else None
    except Exception:
        conf_val = None
    return sorted(indices), conf_val


def _llm_fuse_cluster(articles, cluster_indices, model, strict=False):
    if not OPENROUTER_API_KEY:
        return None
    if len(cluster_indices) <= 1:
        return [{"indices": list(cluster_indices), "confidence": 1.0}]

    lines = []
    for local_idx, gi in enumerate(cluster_indices):
        art = articles[gi]
        fp = art.get("event_fingerprint", {})
        ent = ",".join(fp.get("entities", [])[:4])
        act = ",".join(fp.get("actions", [])[:3])
        lines.append(
            f"[{local_idx}] {art.get('source','')} | {art.get('published','')[:19]} | {art.get('title','')}\n"
            f"实体:{ent or '-'} 动作:{act or '-'}"
        )
    guidance = "严格保守，仅在确定是同一事件时才合并。" if strict else "综合判断是否属于同一事件，可做少量合并。"
    prompt = f"""你是时尚新闻聚合编辑，请对候选文章进行事件级融合分组。

{chr(10).join(lines)}

要求：
1. 以“具体事件”为单位，不同事件必须拆分。
2. 输出 groups，每组包含 items(本地编号数组) 和 confidence(0-1)。
3. 如果不确定，降低 confidence，不要强行合并。
4. {guidance}

输出JSON：
{{"groups":[{{"items":[0,2],"confidence":0.82}},{{"items":[1],"confidence":0.64}}]}}
"""
    result = call_llm(
        [
            {"role": "system", "content": "你是事件融合审稿人，只输出JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.05 if strict else 0.15,
        max_tokens=1200,
        model=model,
    )
    parsed = extract_json(result)
    if not isinstance(parsed, dict):
        return None
    groups = parsed.get("groups")
    if not isinstance(groups, list) or not groups:
        return None

    used = set()
    out = []
    for raw_group in groups:
        indices, conf = _normalize_llm_group_items(raw_group, cluster_indices)
        if not indices:
            continue
        fresh = [gi for gi in indices if gi not in used]
        if not fresh:
            continue
        out.append({"indices": fresh, "confidence": conf})
        used.update(fresh)

    for gi in cluster_indices:
        if gi not in used:
            out.append({"indices": [gi], "confidence": 0.5})
    return out if out else None


def stage_b_event_fusion(articles, candidate_clusters, matrix):
    if not candidate_clusters:
        return [], [], {
            "stage_b_low_cost_reviews": 0,
            "stage_b_high_quality_reviews": 0,
            "stage_b_clusters_before": 0,
            "stage_b_clusters_after": 0,
        }

    final_clusters = []
    confidences = []
    low_cost_reviews = 0
    high_quality_reviews = 0

    for cluster in candidate_clusters:
        if len(cluster) <= 1:
            final_clusters.append(cluster)
            confidences.append(1.0)
            continue

        heuristic_conf = _heuristic_cluster_confidence(cluster, articles, matrix)
        cohesion = _cluster_cohesion(cluster, matrix)
        needs_low_cost = (
            OPENROUTER_API_KEY
            and low_cost_reviews < STAGE_B_LOW_COST_REVIEW_MAX
            and (cohesion < 0.9 or len(cluster) >= 6)
        )
        groups = None
        if needs_low_cost:
            groups = _llm_fuse_cluster(articles, cluster, LOW_COST_LLM_MODEL, strict=False)
            low_cost_reviews += 1

        if not groups:
            groups = [{"indices": cluster, "confidence": heuristic_conf}]

        for group in groups:
            gidx = sorted(set(group.get("indices", [])))
            if not gidx:
                continue
            conf = group.get("confidence")
            try:
                conf = float(conf) if conf is not None else _heuristic_cluster_confidence(gidx, articles, matrix)
            except Exception:
                conf = _heuristic_cluster_confidence(gidx, articles, matrix)
            conf = max(0.3, min(0.98, conf))

            needs_high_quality = (
                OPENROUTER_API_KEY
                and high_quality_reviews < STAGE_B_HIGH_QUALITY_REVIEW_MAX
                and len(gidx) > 1
                and STAGE_B_BOUNDARY_CONF_LOW <= conf <= STAGE_B_BOUNDARY_CONF_HIGH
            )
            if needs_high_quality:
                reviewed = _llm_fuse_cluster(articles, gidx, HIGH_QUALITY_LLM_MODEL, strict=True)
                high_quality_reviews += 1
                if reviewed:
                    for sub in reviewed:
                        sidx = sorted(set(sub.get("indices", [])))
                        if not sidx:
                            continue
                        sconf = sub.get("confidence")
                        try:
                            sconf = float(sconf) if sconf is not None else _heuristic_cluster_confidence(sidx, articles, matrix)
                        except Exception:
                            sconf = _heuristic_cluster_confidence(sidx, articles, matrix)
                        final_clusters.append(sidx)
                        confidences.append(max(0.3, min(0.99, sconf)))
                    continue

            final_clusters.append(gidx)
            confidences.append(conf)

    dedup = {}
    for idx, cluster in enumerate(final_clusters):
        key = tuple(sorted(set(cluster)))
        if not key:
            continue
        dedup[key] = max(confidences[idx], dedup.get(key, 0.0))

    merged_clusters = [list(k) for k in dedup.keys()]
    merged_confidences = [dedup[k] for k in dedup.keys()]
    order = sorted(
        range(len(merged_clusters)),
        key=lambda i: max(articles[x].get("published", "") for x in merged_clusters[i]),
        reverse=True,
    )
    merged_clusters = [merged_clusters[i] for i in order]
    merged_confidences = [merged_confidences[i] for i in order]
    logger.info(
        "Stage B fusion: %d candidate groups -> %d final event clusters (low=%d, high=%d)",
        len(candidate_clusters),
        len(merged_clusters),
        low_cost_reviews,
        high_quality_reviews,
    )
    return merged_clusters, merged_confidences, {
        "stage_b_low_cost_reviews": low_cost_reviews,
        "stage_b_high_quality_reviews": high_quality_reviews,
        "stage_b_clusters_before": len(candidate_clusters),
        "stage_b_clusters_after": len(merged_clusters),
    }


def build_event_clusters(articles):
    if not articles:
        return [], None, [], {}
    if len(articles) == 1:
        return [[0]], None, [1.0], {
            "stage_a_base_clusters": 1,
            "stage_a_candidates": 1,
            "stage_b_low_cost_reviews": 0,
            "stage_b_high_quality_reviews": 0,
            "stage_b_clusters_before": 1,
            "stage_b_clusters_after": 1,
        }

    texts = [_article_embedding_text(a) for a in articles]
    logger.info(f"Computing semantic embeddings via OpenRouter ({EMBEDDING_MODEL})...")
    matrix = get_semantic_embeddings(texts)
    if matrix is None:
        fallback_msg = "Semantic embedding unavailable; falling back to TF-IDF clustering."
        if REQUIRE_SEMANTIC_EMBEDDING:
            logger.error(f"{fallback_msg} REQUIRE_SEMANTIC_EMBEDDING=1, exiting.")
            sys.exit(2)
        logger.warning(fallback_msg)
        clusters = cluster_with_tfidf_fallback(articles)
        confidences = [_heuristic_cluster_confidence(cluster, articles, None) for cluster in clusters]
        return clusters, None, confidences, {
            "stage_a_base_clusters": len(clusters),
            "stage_a_candidates": len(clusters),
            "stage_b_low_cost_reviews": 0,
            "stage_b_high_quality_reviews": 0,
            "stage_b_clusters_before": len(clusters),
            "stage_b_clusters_after": len(clusters),
        }

    attach_event_fingerprints(articles)
    candidates, stats_a = stage_a_candidate_recall(articles, matrix)
    clusters, confidences, stats_b = stage_b_event_fusion(articles, candidates, matrix)
    return clusters, matrix, confidences, {**stats_a, **stats_b}


# ─── LLM Helpers ────────────────────────────────────────────────────────────

def call_llm(messages, temperature=0.3, max_tokens=4000, model=None):
    if not OPENROUTER_API_KEY: return None
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fashion-feed.manus.space",
    }
    payload = {"model": model or LLM_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
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
    body = (
        article.get("full_text")
        or article.get("content_text")
        or article.get("content_snippet")
        or ""
    ).strip()[:ARTICLE_ANALYSIS_INPUT_CHARS]
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

    result = call_llm(
        [
            {
                "role": "system",
                "content": "你是轻奢品牌内部情报平台的资深编辑。你负责做文章摘要、分类和相关性判断。只输出 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=1200,
        model=LOW_COST_LLM_MODEL,
    )
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
    sorted_clusters = sorted(clusters, key=lambda c: (-len(c), max(articles[i]["published"] for i in c)))
    total = min(len(sorted_clusters), MAX_TOPICS)
    logger.info(f"Generating summaries for {total} topics with {LLM_CONCURRENCY} concurrent workers...")

    topics = []
    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as ex:
        futures = {}
        for ci, cluster in enumerate(sorted_clusters[:total]):
            futures[ex.submit(generate_summary_for_cluster, articles, cluster, ci, total)] = (ci, cluster)

        done_count = 0
        for future in as_completed(futures):
            ci, cluster = futures[future]
            try:
                topic = future.result()
                if topic:
                    topic["_cluster_indices"] = cluster
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
            "_cluster_indices": cluster,
        })
    return topics


def prepare_articles_for_embedding(articles):
    if not articles:
        return articles
    fill_article_fulltext_from_web(articles)
    build_embedding_summaries(articles)
    attach_event_fingerprints(articles)
    return articles


def _topic_state_from_generated_topic(topic, cluster_indices, articles, matrix, cluster_confidence=0.6):
    topic = dict(topic)
    article_keys = [articles[i].get("article_key", "") for i in cluster_indices if 0 <= i < len(articles)]
    article_keys = [k for k in article_keys if k]
    cluster_articles = [articles[i] for i in cluster_indices if 0 <= i < len(articles)]

    centroid = None
    if matrix is not None and len(cluster_indices) > 0:
        cluster_vectors = matrix[cluster_indices]
        centroid = cluster_vectors.mean(axis=0)
        norm = np.linalg.norm(centroid)
        centroid = centroid / norm if norm > 0 else centroid

    merged_fp = merge_event_fingerprints([a.get("event_fingerprint", {}) for a in cluster_articles])
    source_set = sorted(set(s.get("name", "") for s in topic.get("sources", []) if s.get("name")))
    if not source_set:
        source_set = sorted(set(a.get("source", "") for a in cluster_articles if a.get("source")))
    entity_set = merged_fp.get("entities", [])[:16]

    topic_id = topic.get("id") or hashlib.md5(("|".join(sorted(article_keys)) + topic.get("published", "")).encode()).hexdigest()[:12]
    topic["id"] = topic_id
    topic["topic_id"] = topic_id
    topic["article_keys"] = sorted(set(article_keys))
    topic["article_count"] = int(topic.get("article_count") or len(topic["article_keys"]) or 1)
    topic["centroid_embedding"] = vector_to_json(centroid)
    topic["event_fingerprint"] = merged_fp
    topic["cluster_confidence"] = float(max(0.3, min(0.99, cluster_confidence)))
    topic["last_merged_at"] = utc_now_iso()
    topic["source_set"] = source_set
    topic["entity_set"] = entity_set
    return topic


def build_topics_state_from_full_run(articles, clusters, matrix, cluster_confidences):
    topics = generate_all_summaries(articles, clusters)
    topics_state = []
    confidence_map = {}
    for idx, cluster in enumerate(clusters):
        confidence_map[tuple(sorted(cluster))] = cluster_confidences[idx] if idx < len(cluster_confidences) else 0.6

    for topic in topics:
        cluster_indices = topic.pop("_cluster_indices", [])
        conf = confidence_map.get(tuple(sorted(cluster_indices)), _heuristic_cluster_confidence(cluster_indices, articles, matrix))
        topics_state.append(_topic_state_from_generated_topic(topic, cluster_indices, articles, matrix, conf))
    return sort_topics_by_published(topics_state)


def merge_sources(existing_sources, new_articles):
    dedup = {}
    for src in existing_sources or []:
        link = src.get("link", "")
        if link:
            dedup[link] = src
    for a in new_articles:
        link = a.get("link", "")
        if not link:
            continue
        dedup[link] = {
            "name": a.get("source", ""),
            "title": a.get("title", ""),
            "link": link,
            "lang": a.get("source_lang", ""),
        }
    return list(dedup.values())


def refresh_topic_editorial(topic, new_articles):
    if not OPENROUTER_API_KEY:
        return None
    context = []
    for a in new_articles[:6]:
        context.append(
            f"来源: {a.get('source','')} ({a.get('source_lang','')})\n标题: {a.get('title','')}\n摘要: {(a.get('article_summary') or a.get('embedding_summary') or a.get('content_snippet') or '')[:240]}"
        )
    prompt = f"""这是已存在的话题，请基于新增文章更新这个话题描述。

已有话题:
标题: {topic.get('title', '')}
摘要: {topic.get('summary', '')}
分类: {topic.get('category_name', '')}

新增文章:
{chr(10).join(context)}

输出JSON:
{{"title":"更新后标题","summary":"更新后摘要","key_points":["要点1","要点2","要点3"],"tags":["标签1","标签2","标签3"],"category":"从以下选一个: 秀场/系列, 街拍/造型, 趋势总结, 品牌/市场"}}
"""
    result = call_llm(
        [
            {"role": "system", "content": "你是时尚资讯编辑，负责增量维护话题。只输出JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1200,
    )
    parsed = extract_json(result)
    return parsed if isinstance(parsed, dict) else None


def refresh_existing_topic(topic, new_articles, new_vectors):
    updated = dict(topic)
    old_keys = set(updated.get("article_keys", []))
    for a in new_articles:
        if a.get("article_key"):
            old_keys.add(a["article_key"])
    updated["article_keys"] = sorted(old_keys)
    updated["article_count"] = max(int(updated.get("article_count") or 0), len(updated["article_keys"]))
    updated["sources"] = merge_sources(updated.get("sources", []), new_articles)
    updated["published"] = max([updated.get("published", "")] + [a.get("published", "") for a in new_articles])
    if not updated.get("image"):
        for a in new_articles:
            if a.get("image"):
                updated["image"] = a["image"]
                break

    old_centroid = vector_from_json(updated.get("centroid_embedding", []))
    old_count = max(int(topic.get("article_count") or 0), 1 if old_centroid is not None else 0)
    if new_vectors is not None and len(new_vectors) > 0:
        total = new_vectors.sum(axis=0)
        if old_centroid is not None and old_count > 0:
            total = total + old_centroid * old_count
            denom = old_count + len(new_vectors)
        else:
            denom = len(new_vectors)
        centroid = total / max(denom, 1)
        norm = np.linalg.norm(centroid)
        centroid = centroid / norm if norm > 0 else centroid
        updated["centroid_embedding"] = vector_to_json(centroid)

    existing_fp = updated.get("event_fingerprint", {})
    incoming_fps = [a.get("event_fingerprint", {}) for a in new_articles]
    merged_fp = merge_event_fingerprints([existing_fp] + incoming_fps)
    updated["event_fingerprint"] = merged_fp
    updated["source_set"] = sorted(set(updated.get("source_set", [])) | set(merged_fp.get("sources", [])))
    updated["entity_set"] = sorted(set(updated.get("entity_set", [])) | set(merged_fp.get("entities", [])))[:24]
    prev_conf = float(updated.get("cluster_confidence", 0.6) or 0.6)
    updated["cluster_confidence"] = max(0.3, min(0.99, 0.7 * prev_conf + 0.3 * 0.82))
    updated["last_merged_at"] = utc_now_iso()

    editorial = refresh_topic_editorial(updated, new_articles)
    if editorial:
        updated["title"] = editorial.get("title", updated.get("title", ""))
        updated["summary"] = editorial.get("summary", updated.get("summary", ""))
        updated["key_points"] = editorial.get("key_points", updated.get("key_points", []))
        updated["tags"] = editorial.get("tags", updated.get("tags", []))
        cat_name = editorial.get("category", updated.get("category_name", "品牌/市场"))
        cat_id = CATEGORY_MAP.get(cat_name, updated.get("category", "brand-market"))
        updated["category"] = cat_id
        updated["category_name"] = CATEGORY_NAME_MAP.get(cat_id, cat_name)

    return updated


def build_new_topic_from_article(article, vector, cluster_idx, total):
    topic = generate_summary_for_cluster([article], [0], cluster_idx, total)
    if not topic:
        return None
    topic_id = topic.get("id") or hashlib.md5((article.get("article_key", "") + article.get("published", "")).encode()).hexdigest()[:12]
    topic["id"] = topic_id
    topic["topic_id"] = topic_id
    topic["article_keys"] = [article.get("article_key", "")]
    topic["article_count"] = 1
    topic["centroid_embedding"] = vector_to_json(vector)
    fp = article.get("event_fingerprint", build_article_event_fingerprint(article))
    topic["event_fingerprint"] = merge_event_fingerprints([fp])
    topic["source_set"] = [article.get("source", "")] if article.get("source") else []
    topic["entity_set"] = fp.get("entities", [])[:16]
    topic["cluster_confidence"] = 0.7
    topic["last_merged_at"] = utc_now_iso()
    return topic


def normalize_topics_state(topics_state):
    normalized = []
    for topic in topics_state or []:
        t = dict(topic)
        tid = t.get("topic_id") or t.get("id") or hashlib.md5((t.get("title", "") + t.get("published", "")).encode()).hexdigest()[:12]
        t["topic_id"] = tid
        t["id"] = t.get("id", tid)
        t["article_keys"] = sorted(set(t.get("article_keys", [])))
        t["article_count"] = int(t.get("article_count") or len(t["article_keys"]) or 1)
        t["sources"] = t.get("sources", [])
        t["centroid_embedding"] = vector_to_json(vector_from_json(t.get("centroid_embedding", [])))
        fp = t.get("event_fingerprint")
        if not isinstance(fp, dict):
            fp = {}
        if not fp.get("hash"):
            fp = merge_event_fingerprints(
                [
                    fp,
                    {
                        "entities": extract_entities_from_text(f"{t.get('title','')} {t.get('summary','')}", limit=12),
                        "actions": extract_actions_from_text(f"{t.get('title','')} {t.get('summary','')}", limit=8),
                        "time_bucket": _time_bucket(t.get("published", "")),
                        "sources": [s.get("name", "") for s in t.get("sources", []) if s.get("name")],
                    },
                ]
            )
        t["event_fingerprint"] = fp
        t["source_set"] = sorted(set(t.get("source_set", [])) | set(fp.get("sources", [])))
        if not t["source_set"]:
            t["source_set"] = sorted(set(s.get("name", "") for s in t.get("sources", []) if s.get("name")))
        t["entity_set"] = sorted(set(t.get("entity_set", [])) | set(fp.get("entities", [])))[:24]
        conf = t.get("cluster_confidence", 0.6)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.6
        t["cluster_confidence"] = max(0.3, min(0.99, conf))
        t["last_merged_at"] = t.get("last_merged_at") or t.get("published") or utc_now_iso()
        normalized.append(t)
    return sort_topics_by_published(normalized)


def apply_topic_cap_with_tombstones(topics_state, tombstones):
    topics_state = sort_topics_by_published(topics_state)
    if len(topics_state) <= MAX_TOPICS:
        return topics_state, prune_tombstones(tombstones), 0

    kept = topics_state[:MAX_TOPICS]
    evicted = topics_state[MAX_TOPICS:]
    evicted_keys = []
    for topic in evicted:
        evicted_keys.extend(topic.get("article_keys", []))

    updated_tombstones = add_tombstones(prune_tombstones(tombstones), evicted_keys)
    active_keys = set(k for topic in kept for k in topic.get("article_keys", []))
    updated_tombstones = [row for row in updated_tombstones if row.get("article_key") not in active_keys]
    return kept, updated_tombstones, len(evicted)


def topics_state_to_output(topics_state):
    output = []
    for t in topics_state:
        row = dict(t)
        row.pop("topic_id", None)
        row.pop("article_keys", None)
        row.pop("centroid_embedding", None)
        row.pop("event_fingerprint", None)
        row.pop("source_set", None)
        row.pop("entity_set", None)
        row.pop("cluster_confidence", None)
        row.pop("last_merged_at", None)
        output.append(row)
    return sort_topics_by_published(output)


def build_state_payload(topics_state, tombstones, last_full_rebuild_at=""):
    active_keys = sorted(set(k for topic in topics_state for k in topic.get("article_keys", [])))
    tombstones = prune_tombstones(tombstones)
    active_key_set = set(active_keys)
    tombstones = [row for row in tombstones if row.get("article_key") not in active_key_set]
    return {
        "version": 2,
        "updated_at": utc_now_iso(),
        "last_full_rebuild_at": last_full_rebuild_at or "",
        "seen_article_keys": active_keys,
        "tombstones": tombstones,
        "topics_state": topics_state,
    }


def select_incremental_new_articles(articles, previous_snapshot, state):
    prev_keys = snapshot_key_set(previous_snapshot)
    seen_keys = set(state.get("seen_article_keys", []))
    tombstones = prune_tombstones(state.get("tombstones", []))
    blocked = tombstone_key_set(tombstones)

    selected = []
    blocked_count = 0
    for a in articles:
        key = a.get("article_key", "")
        if not key:
            continue
        if key in blocked:
            blocked_count += 1
            continue
        if key in seen_keys:
            continue
        if key in prev_keys:
            continue
        selected.append(a)
    return selected, tombstones, blocked_count


def _topic_article_hybrid_score(topic, article, topic_vec, article_vec):
    embed_score = 0.0
    if (
        topic_vec is not None
        and article_vec is not None
        and hasattr(topic_vec, "shape")
        and hasattr(article_vec, "shape")
        and topic_vec.shape == article_vec.shape
        and topic_vec.shape[0] > 0
    ):
        embed_score = max(0.0, min(1.0, float(np.dot(topic_vec, article_vec))))
    fp_score = fingerprint_similarity(topic.get("event_fingerprint", {}), article.get("event_fingerprint", {}))
    recency_score = time_bucket_score(
        topic.get("event_fingerprint", {}).get("time_bucket"),
        article.get("event_fingerprint", {}).get("time_bucket"),
    )
    return 0.62 * embed_score + 0.28 * fp_score + 0.10 * recency_score


def _topic_pair_hybrid_score(topic_a, topic_b):
    vec_a = vector_from_json(topic_a.get("centroid_embedding", ""))
    vec_b = vector_from_json(topic_b.get("centroid_embedding", ""))
    embed_score = 0.0
    if (
        vec_a is not None
        and vec_b is not None
        and hasattr(vec_a, "shape")
        and hasattr(vec_b, "shape")
        and vec_a.shape == vec_b.shape
        and vec_a.shape[0] > 0
    ):
        embed_score = max(0.0, min(1.0, float(np.dot(vec_a, vec_b))))
    fp_score = fingerprint_similarity(topic_a.get("event_fingerprint", {}), topic_b.get("event_fingerprint", {}))
    return 0.68 * embed_score + 0.32 * fp_score


def _llm_review_topic_pair(topic_a, topic_b, model, strict=False):
    if not OPENROUTER_API_KEY:
        return None
    guidance = "仅在确认同一事件时返回 merge=true。" if strict else "若两者高度可能同一事件可返回 merge=true。"
    prompt = f"""你是时尚资讯聚合审核员，请判断两个 topic 是否应该合并为同一事件。

Topic A:
标题: {topic_a.get('title','')}
摘要: {(topic_a.get('summary','') or '')[:260]}
时间: {topic_a.get('published','')}
来源: {', '.join(topic_a.get('source_set', [])[:8])}
实体: {', '.join(topic_a.get('entity_set', [])[:8])}

Topic B:
标题: {topic_b.get('title','')}
摘要: {(topic_b.get('summary','') or '')[:260]}
时间: {topic_b.get('published','')}
来源: {', '.join(topic_b.get('source_set', [])[:8])}
实体: {', '.join(topic_b.get('entity_set', [])[:8])}

要求：
1. 输出 merge 与 confidence(0-1)
2. {guidance}

JSON:
{{"merge": true, "confidence": 0.81}}
"""
    result = call_llm(
        [
            {"role": "system", "content": "你是事件级聚合审核员，只输出JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.05 if strict else 0.12,
        max_tokens=400,
        model=model,
    )
    parsed = extract_json(result)
    if not isinstance(parsed, dict):
        return None
    merge = bool(parsed.get("merge", False))
    try:
        conf = float(parsed.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    return {"merge": merge, "confidence": max(0.0, min(1.0, conf))}


def _refresh_merged_topic_editorial(merged_topic, component_topics):
    if not OPENROUTER_API_KEY:
        return merged_topic
    if len(component_topics) <= 1:
        return merged_topic
    context = []
    for t in component_topics[:6]:
        context.append(
            f"标题: {t.get('title','')}\n摘要: {(t.get('summary','') or '')[:220]}\n来源: {', '.join(t.get('source_set', [])[:6])}"
        )
    prompt = f"""请将以下多个已确认同一事件的话题融合成一个更完整的话题描述。

{chr(10).join(context)}

输出JSON:
{{"title":"融合后标题","summary":"融合后摘要","key_points":["要点1","要点2","要点3"],"tags":["标签1","标签2","标签3"],"category":"从以下选一个: 秀场/系列, 街拍/造型, 趋势总结, 品牌/市场"}}
"""
    parsed = extract_json(
        call_llm(
            [
                {"role": "system", "content": "你是时尚资讯编辑，只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=900,
            model=LOW_COST_LLM_MODEL,
        )
    )
    if not isinstance(parsed, dict):
        return merged_topic
    merged_topic["title"] = parsed.get("title", merged_topic.get("title", ""))
    merged_topic["summary"] = parsed.get("summary", merged_topic.get("summary", ""))
    merged_topic["key_points"] = parsed.get("key_points", merged_topic.get("key_points", []))
    merged_topic["tags"] = parsed.get("tags", merged_topic.get("tags", []))
    cat_name = parsed.get("category", merged_topic.get("category_name", "品牌/市场"))
    cat_id = CATEGORY_MAP.get(cat_name, merged_topic.get("category", "brand-market"))
    merged_topic["category"] = cat_id
    merged_topic["category_name"] = CATEGORY_NAME_MAP.get(cat_id, cat_name)
    return merged_topic


def _merge_topic_component(topics):
    if len(topics) == 1:
        topic = dict(topics[0])
        topic["last_merged_at"] = utc_now_iso()
        return topic

    sorted_topics = sorted(topics, key=lambda t: t.get("published", ""), reverse=True)
    merged = dict(sorted_topics[0])
    merged["article_keys"] = sorted(set(k for t in sorted_topics for k in t.get("article_keys", [])))
    merged["article_count"] = max(len(merged["article_keys"]), sum(int(t.get("article_count") or 0) for t in sorted_topics))
    merged["sources"] = merge_sources([], [])
    for t in sorted_topics:
        merged["sources"] = merge_sources(merged.get("sources", []), [
            {
                "source": s.get("name", ""),
                "title": s.get("title", ""),
                "link": s.get("link", ""),
                "source_lang": s.get("lang", ""),
            }
            for s in t.get("sources", [])
        ])
    merged["published"] = max(t.get("published", "") for t in sorted_topics)
    if not merged.get("image"):
        for t in sorted_topics:
            if t.get("image"):
                merged["image"] = t["image"]
                break

    fps = [t.get("event_fingerprint", {}) for t in sorted_topics]
    merged_fp = merge_event_fingerprints(fps)
    merged["event_fingerprint"] = merged_fp
    merged["source_set"] = sorted(set(s for t in sorted_topics for s in t.get("source_set", [])) | set(merged_fp.get("sources", [])))
    merged["entity_set"] = sorted(set(e for t in sorted_topics for e in t.get("entity_set", [])) | set(merged_fp.get("entities", [])))[:24]
    weighted_conf = 0.0
    total_weight = 0.0
    for t in sorted_topics:
        weight = max(float(t.get("article_count", 1) or 1), 1.0)
        weighted_conf += weight * float(t.get("cluster_confidence", 0.6) or 0.6)
        total_weight += weight
    merged["cluster_confidence"] = max(0.3, min(0.99, weighted_conf / max(total_weight, 1.0)))

    vectors = []
    weights = []
    for t in sorted_topics:
        vec = vector_from_json(t.get("centroid_embedding", ""))
        if vec is None:
            continue
        w = max(float(t.get("article_count", 1) or 1), 1.0)
        vectors.append(vec * w)
        weights.append(w)
    if vectors:
        centroid = np.sum(vectors, axis=0) / max(np.sum(weights), 1.0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        merged["centroid_embedding"] = vector_to_json(centroid)

    merged["last_merged_at"] = utc_now_iso()
    merged = _refresh_merged_topic_editorial(merged, sorted_topics)
    return merged


def cross_topic_event_fusion(topics_state):
    topics = normalize_topics_state(topics_state)
    if len(topics) <= 1:
        return topics, {
            "cross_merge_candidates": 0,
            "cross_merge_low_reviews": 0,
            "cross_merge_high_reviews": 0,
            "cross_merge_merged_topics": 0,
        }

    candidate_pairs = []
    for i, j in combinations(range(len(topics)), 2):
        score = _topic_pair_hybrid_score(topics[i], topics[j])
        if score >= TOPIC_CROSS_MERGE_BOUNDARY_SCORE:
            candidate_pairs.append((score, i, j))
    candidate_pairs.sort(reverse=True, key=lambda x: x[0])
    candidate_pairs = candidate_pairs[:220]

    parents = list(range(len(topics)))
    low_reviews = 0
    high_reviews = 0

    for score, i, j in candidate_pairs:
        if _union_find(parents, i) == _union_find(parents, j):
            continue
        should_merge = False
        conf = score
        if score >= TOPIC_CROSS_MERGE_STRONG_SCORE:
            should_merge = True
        elif OPENROUTER_API_KEY and low_reviews < TOPIC_CROSS_MERGE_LOW_COST_MAX:
            review = _llm_review_topic_pair(topics[i], topics[j], LOW_COST_LLM_MODEL, strict=False)
            low_reviews += 1
            if review:
                should_merge = review.get("merge", False)
                conf = review.get("confidence", score)
            if (
                should_merge
                and OPENROUTER_API_KEY
                and high_reviews < TOPIC_CROSS_MERGE_HIGH_QUALITY_MAX
                and STAGE_B_BOUNDARY_CONF_LOW <= conf <= STAGE_B_BOUNDARY_CONF_HIGH
            ):
                high = _llm_review_topic_pair(topics[i], topics[j], HIGH_QUALITY_LLM_MODEL, strict=True)
                high_reviews += 1
                if high:
                    should_merge = high.get("merge", should_merge)
                    conf = high.get("confidence", conf)
        if should_merge:
            _union_merge(parents, i, j)

    components = {}
    for idx in range(len(topics)):
        root = _union_find(parents, idx)
        components.setdefault(root, []).append(topics[idx])

    merged_topics = [_merge_topic_component(group) for group in components.values()]
    merged_topics = normalize_topics_state(merged_topics)
    merged_count = len(topics) - len(merged_topics)
    logger.info(
        "Cross-topic fusion: %d -> %d topics (merged=%d, low=%d, high=%d)",
        len(topics),
        len(merged_topics),
        merged_count,
        low_reviews,
        high_reviews,
    )
    return merged_topics, {
        "cross_merge_candidates": len(candidate_pairs),
        "cross_merge_low_reviews": low_reviews,
        "cross_merge_high_reviews": high_reviews,
        "cross_merge_merged_topics": merged_count,
    }


def incremental_update_topics(topics_state, new_articles):
    topics_state = normalize_topics_state(topics_state)
    if not new_articles:
        return topics_state, {
            "incremental_assigned_to_existing": 0,
            "incremental_new_topics_created": 0,
            "cross_merge_candidates": 0,
            "cross_merge_low_reviews": 0,
            "cross_merge_high_reviews": 0,
            "cross_merge_merged_topics": 0,
        }

    prepare_articles_for_embedding(new_articles)
    matrix = get_semantic_embeddings([_article_embedding_text(a) for a in new_articles])
    if matrix is None:
        msg = "Semantic embedding unavailable during incremental update."
        if REQUIRE_SEMANTIC_EMBEDDING:
            logger.error(msg)
            sys.exit(2)
        logger.warning(msg)
        matrix = np.zeros((len(new_articles), 0), dtype=np.float32)

    topic_vectors = [vector_from_json(t.get("centroid_embedding", [])) for t in topics_state]
    assignments = {i: [] for i in range(len(topics_state))}
    unassigned = []

    for idx, art in enumerate(new_articles):
        vec = matrix[idx] if matrix is not None and idx < len(matrix) else None
        best_topic = None
        best_score = -1.0
        for ti, tvec in enumerate(topic_vectors):
            score = _topic_article_hybrid_score(topics_state[ti], art, tvec, vec)
            if score > best_score:
                best_score = score
                best_topic = ti
        if best_topic is not None and best_score >= INCREMENTAL_ASSIGN_SCORE_THRESHOLD:
            assignments[best_topic].append((idx, best_score))
        else:
            unassigned.append(idx)

    assigned_count = 0
    for ti, items in assignments.items():
        if not items:
            continue
        idxs = [item[0] for item in items]
        avg_score = float(np.mean([item[1] for item in items])) if items else 0.6
        topic_articles = [new_articles[i] for i in idxs]
        vecs = matrix[idxs] if matrix is not None and len(matrix) >= max(idxs) + 1 else None
        topics_state[ti] = refresh_existing_topic(topics_state[ti], topic_articles, vecs)
        topics_state[ti]["cluster_confidence"] = max(
            0.3,
            min(0.99, 0.7 * float(topics_state[ti].get("cluster_confidence", 0.6)) + 0.3 * avg_score),
        )
        assigned_count += len(idxs)

    created_topics = 0
    if unassigned:
        residual_articles = [new_articles[i] for i in unassigned]
        clusters, residual_matrix, residual_conf, _ = build_event_clusters(residual_articles)
        new_topics = build_topics_state_from_full_run(residual_articles, clusters, residual_matrix, residual_conf)
        created_topics = len(new_topics)
        topics_state.extend(new_topics)

    topics_state, cross_stats = cross_topic_event_fusion(topics_state)
    stats = {
        "incremental_assigned_to_existing": assigned_count,
        "incremental_new_topics_created": created_topics,
    }
    stats.update(cross_stats)
    return sort_topics_by_published(topics_state), stats


# ─── Output ──────────────────────────────────────────────────────────────────

def compute_quality_metrics(topics_state, rebuild_mode):
    total_topics = len(topics_state)
    total_articles = sum(int(t.get("article_count") or 0) for t in topics_state)
    merge_rate = 0.0
    if total_articles > 0:
        merge_rate = max(0.0, min(1.0, (total_articles - total_topics) / total_articles))
    source_counts = []
    high_conf = 0
    for topic in topics_state:
        sources = topic.get("source_set", [])
        if not sources:
            sources = sorted(set(s.get("name", "") for s in topic.get("sources", []) if s.get("name")))
        source_counts.append(len(sources))
        if float(topic.get("cluster_confidence", 0.0) or 0.0) >= HIGH_CONF_TOPIC_THRESHOLD:
            high_conf += 1
    avg_sources_per_topic = float(np.mean(source_counts)) if source_counts else 0.0
    high_conf_ratio = high_conf / total_topics if total_topics else 0.0
    return {
        "merge_rate": round(merge_rate, 4),
        "avg_sources_per_topic": round(avg_sources_per_topic, 4),
        "high_confidence_topic_ratio": round(high_conf_ratio, 4),
        "rebuild_mode": rebuild_mode,
    }


def build_output(topics, all_sources, incremental_meta=None, quality_meta=None):
    return {
        "meta": {
            "generated_at": datetime.datetime.now().isoformat(),
            "total_topics": len(topics),
            "total_articles": sum(t["article_count"] for t in topics),
            "sources_count": len(all_sources),
            "sources": sorted(all_sources),
            "incremental": incremental_meta or {},
            "quality": quality_meta or {},
        },
        "categories": CATEGORIES,
        "topics": topics,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    logger.info("="*60)
    logger.info("Fashion Feed Aggregator v6 — Multi-Source Event Fusion")
    logger.info("="*60)
    logger.info(f"Incremental mode: {INCREMENTAL_MODE}, state={STATE_FILE}, snapshot={RSS_SNAPSHOT_FILE}")

    state = load_json_or_default(STATE_FILE, _json_default_state)
    previous_snapshot = load_json_or_default(RSS_SNAPSHOT_FILE, _json_default_snapshot)
    full_rebuild = should_run_full_rebuild(state, previous_snapshot)

    sources = load_sources()
    raw_articles = fetch_all_sources(sources)
    if not raw_articles:
        logger.error("No articles fetched"); sys.exit(1)

    articles = deduplicate_articles(raw_articles)
    articles = fill_missing_images_from_web(articles)
    articles = fill_article_fulltext_from_web(articles)
    articles = enrich_and_filter_articles(articles)
    if not articles:
        logger.error("No articles left after article-level LLM filtering"); sys.exit(1)
    articles = attach_article_keys(articles)
    articles = attach_event_fingerprints(articles)
    all_source_names = list(set(a["source"] for a in articles))
    current_snapshot = build_rss_snapshot(articles)

    tombstones_seed = prune_tombstones(state.get("tombstones", []))
    blocked_keys = tombstone_key_set(tombstones_seed)
    pipeline_stats = {}
    last_full_rebuild_at = state.get("last_full_rebuild_at", "")

    if full_rebuild:
        logger.info("Running FULL rebuild pipeline")
        rebuild_articles = [a for a in articles if a.get("article_key") not in blocked_keys]
        if blocked_keys:
            logger.info(f"Filtered {len(articles)-len(rebuild_articles)} tombstoned articles before full rebuild")
        if not rebuild_articles:
            logger.error("No articles left after tombstone filtering"); sys.exit(1)

        prepare_articles_for_embedding(rebuild_articles)
        clusters, matrix, cluster_confidences, fusion_stats = build_event_clusters(rebuild_articles)
        topics_state = build_topics_state_from_full_run(rebuild_articles, clusters, matrix, cluster_confidences)
        topics_state, cross_stats = cross_topic_event_fusion(topics_state)
        pipeline_stats.update(fusion_stats)
        pipeline_stats.update(cross_stats)
        new_articles_count = len(rebuild_articles)
        blocked_delta = len(blocked_keys)
        rebuilt_full = True
        last_full_rebuild_at = utc_now_iso()
    else:
        logger.info("Running INCREMENTAL update pipeline")
        new_articles, tombstones_seed, blocked_delta = select_incremental_new_articles(articles, previous_snapshot, state)
        logger.info(f"Incremental delta: {len(new_articles)} new articles")
        topics_state, incremental_stats = incremental_update_topics(state.get("topics_state", []), new_articles)
        pipeline_stats.update(incremental_stats)
        new_articles_count = len(new_articles)
        rebuilt_full = False

    topics_state, tombstones_final, evicted_topics = apply_topic_cap_with_tombstones(topics_state, tombstones_seed)
    output_topics = topics_state_to_output(topics_state)
    state_payload = build_state_payload(topics_state, tombstones_final, last_full_rebuild_at=last_full_rebuild_at)
    quality_meta = compute_quality_metrics(topics_state, "full" if rebuilt_full else "incremental")

    output = build_output(
        output_topics,
        all_source_names,
        {
            "is_incremental_run": not rebuilt_full,
            "rebuilt_full": rebuilt_full,
            "new_articles_in_2h": new_articles_count,
            "blocked_by_tombstones": blocked_delta,
            "evicted_topics": evicted_topics,
            "pipeline_stats": pipeline_stats,
        },
        quality_meta=quality_meta,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    save_json(STATE_FILE, state_payload)
    save_json(RSS_SNAPSHOT_FILE, current_snapshot)

    logger.info(f"\nOutput: {OUTPUT_FILE}")
    logger.info(f"State: {STATE_FILE}")
    logger.info(f"Snapshot: {RSS_SNAPSHOT_FILE}")
    logger.info(f"Topics: {output['meta']['total_topics']}")
    logger.info(f"Articles covered: {output['meta']['total_articles']}")
    logger.info(f"Sources: {output['meta']['sources_count']}")

    multi_topics = [t for t in output_topics if t["article_count"] > 1]
    logger.info(f"Multi-source topics: {len(multi_topics)}")
    for t in multi_topics[:10]:
        src_names = list(set(s["name"] for s in t["sources"]))
        logger.info(f"  [{t['article_count']}x] {t['title']} — {', '.join(src_names)}")
    logger.info("Done!")


if __name__ == "__main__":
    main()
