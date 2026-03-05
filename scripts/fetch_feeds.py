#!/usr/bin/env python3
"""
Fashion Feed Aggregator v2 — Topic Clustering Mode
====================================================
Pipeline:
  1. Load RSS sources from sources.yaml
  2. Fetch articles from all enabled sources (target: 500+)
  3. Deduplicate by URL
  4. Batch-cluster articles into topics via LLM
  5. For each topic: generate a comprehensive Chinese summary
  6. Output feed-data.json for the frontend
"""

import os
import sys
import json
import hashlib
import re
import datetime
import time
import logging
import math
from urllib.parse import urlparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
import requests
import feedparser
from bs4 import BeautifulSoup

# ─── Configuration ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.0-flash-001")

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "client" / "public"
OUTPUT_FILE = OUTPUT_DIR / "feed-data.json"
SOURCES_FILE = SCRIPT_DIR / "sources.yaml"

# Target: fetch up to this many raw articles before dedup
MAX_TOTAL_RAW = 600
# After dedup + filtering, keep up to this many for clustering
MAX_ARTICLES_FOR_CLUSTERING = 500
# Max topics in final output
MAX_TOPICS = 120

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

CATEGORIES = [
    {"id": "all", "name": "全部", "icon": "Newspaper"},
    {"id": "haute-couture", "name": "高端时装", "icon": "Crown"},
    {"id": "streetwear", "name": "潮流街头", "icon": "Flame"},
    {"id": "industry", "name": "行业动态", "icon": "TrendingUp"},
    {"id": "menswear", "name": "男装风尚", "icon": "Shirt"},
    {"id": "avant-garde", "name": "先锋文化", "icon": "Palette"},
]

CATEGORY_MAP = {
    "高端时装": "haute-couture",
    "潮流街头": "streetwear",
    "行业动态": "industry",
    "男装风尚": "menswear",
    "先锋文化": "avant-garde",
}


# ─── YAML Source Loader ─────────────────────────────────────────────────────

def load_sources():
    """Load RSS sources from sources.yaml."""
    if not SOURCES_FILE.exists():
        logger.error(f"sources.yaml not found at {SOURCES_FILE}")
        sys.exit(1)
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        sources = yaml.safe_load(f)
    enabled = [s for s in sources if s.get("enabled", True)]
    logger.info(f"Loaded {len(enabled)} enabled sources from sources.yaml")
    return enabled


# ─── Utility Functions ──────────────────────────────────────────────────────

def clean_html(html_content):
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(
        ["script", "style", "img", "video", "audio", "iframe", "input", "noscript"]
    ):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]


def extract_image(entry):
    """Extract the best image URL from a feed entry."""
    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            if media.get("medium") == "image" or media.get("type", "").startswith("image"):
                return media.get("url", "")
        if entry.media_content[0].get("url"):
            return entry.media_content[0]["url"]
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        return entry.media_thumbnail[0].get("url", "")
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href", "")
    content = ""
    if hasattr(entry, "content") and entry.content:
        content = entry.content[0].get("value", "")
    elif hasattr(entry, "description"):
        content = entry.description or ""
    elif hasattr(entry, "summary"):
        content = entry.summary or ""
    if content:
        soup = BeautifulSoup(content, "html.parser")
        img = soup.find("img")
        if img and img.get("src") and img["src"].startswith("http"):
            return img["src"]
    return ""


def normalize_url(url):
    if not url:
        return ""
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return clean.lower()


def get_published_date(entry):
    for attr in ["published_parsed", "updated_parsed", "created_parsed"]:
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                dt = datetime.datetime(*parsed[:6])
                return dt.isoformat()
            except Exception:
                pass
    for attr in ["published", "updated", "created"]:
        val = getattr(entry, attr, None)
        if val:
            return val
    return datetime.datetime.now().isoformat()


def get_article_id(link, title):
    key = (link or "") + (title or "")
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ─── RSS Fetching (parallel) ───────────────────────────────────────────────

def fetch_single_feed(feed_config):
    """Fetch articles from a single RSS source. Returns list of article dicts."""
    name = feed_config["name"]
    url = feed_config["url"]
    lang = feed_config.get("lang", "en")
    category = feed_config.get("category", "行业动态")
    max_articles = feed_config.get("max_articles", 30)

    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"  [{name}] HTTP {resp.status_code}")
            return articles

        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:max_articles]:
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

            articles.append(
                {
                    "id": get_article_id(link, title),
                    "title": title,
                    "link": link,
                    "source": name,
                    "source_lang": lang,
                    "category_hint": category,
                    "category_id": CATEGORY_MAP.get(category, "industry"),
                    "image": extract_image(entry),
                    "published": get_published_date(entry),
                    "content_snippet": clean_html(content)[:800],
                }
            )
        logger.info(f"  [{name}] {len(articles)} articles")
    except Exception as e:
        logger.error(f"  [{name}] Error: {e}")
    return articles


def fetch_all_feeds(sources):
    """Fetch from all sources in parallel."""
    all_articles = []
    seen_urls = set()

    logger.info(f"Fetching from {len(sources)} sources...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_single_feed, src): src for src in sources}
        for future in as_completed(futures):
            for art in future.result():
                norm = normalize_url(art["link"])
                if norm not in seen_urls:
                    seen_urls.add(norm)
                    all_articles.append(art)

    # Sort by published date descending
    all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)
    all_articles = all_articles[:MAX_ARTICLES_FOR_CLUSTERING]
    logger.info(f"Total deduplicated articles: {len(all_articles)}")
    return all_articles


# ─── LLM Helpers ────────────────────────────────────────────────────────────

def call_llm(messages, temperature=0.3, max_tokens=4000):
    if not OPENROUTER_API_KEY:
        return None
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fashion-feed.manus.space",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    for attempt in range(3):
        try:
            resp = requests.post(
                OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"LLM attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return None


def extract_json(text):
    """Extract JSON object or array from LLM response text."""
    if not text:
        return None
    # Try to find JSON block in markdown code fence
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # Try object
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try array
    m = re.search(r"(\[[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


# ─── Topic Clustering Pipeline ──────────────────────────────────────────────

def build_article_index(articles):
    """Build a compact text index for clustering."""
    lines = []
    for i, art in enumerate(articles):
        src = art["source"]
        title = art["title"][:80]
        snippet = art["content_snippet"][:120]
        lines.append(f"[{i}] {src} | {title} | {snippet}")
    return lines


def cluster_batch(article_lines, batch_start_idx):
    """Send a batch of article lines to LLM for topic clustering.
    Returns list of cluster dicts: {topic, article_indices, category}
    """
    text = "\n".join(article_lines)
    prompt = f"""你是一位专业的时尚资讯编辑。以下是一批时尚资讯文章索引，每行格式为：[编号] 来源 | 标题 | 内容片段

{text}

请将报道**同一事件、同一话题或高度相关**的文章归为一组。独立的文章也单独成组。

输出JSON数组（不要输出其他内容）：
[
  {{
    "topic": "话题简述（中文，10字以内）",
    "indices": [0, 3, 7],
    "category": "从以下选一个: 高端时装, 潮流街头, 行业动态, 男装风尚, 先锋文化"
  }}
]

规则：
1. 每篇文章只能属于一个组
2. 报道同一品牌同一事件的文章应合并（如多家媒体报道同一场秀）
3. 不要遗漏任何文章编号
4. 独立文章也要单独成组（indices只有一个元素）"""

    messages = [
        {
            "role": "system",
            "content": "你是时尚资讯编辑，擅长识别相关新闻话题。只输出JSON数组。",
        },
        {"role": "user", "content": prompt},
    ]
    result = call_llm(messages, temperature=0.15, max_tokens=4000)
    parsed = extract_json(result)

    if isinstance(parsed, dict) and "clusters" in parsed:
        parsed = parsed["clusters"]
    if not isinstance(parsed, list):
        return None

    # Adjust indices to global offset
    clusters = []
    for c in parsed:
        if not isinstance(c, dict):
            continue
        indices = c.get("indices", c.get("article_indices", []))
        clusters.append(
            {
                "topic": c.get("topic", ""),
                "indices": [batch_start_idx + idx for idx in indices],
                "category": c.get("category", "行业动态"),
            }
        )
    return clusters


def generate_topic_summary(topic_articles):
    """Generate a comprehensive Chinese summary for a topic cluster."""
    # Build context from all articles in the cluster
    context_parts = []
    for art in topic_articles:
        context_parts.append(
            f"来源: {art['source']} ({art['source_lang']})\n"
            f"标题: {art['title']}\n"
            f"内容: {art['content_snippet']}\n"
        )
    context = "\n---\n".join(context_parts)

    num_articles = len(topic_articles)
    if num_articles == 1:
        summary_instruction = "请将这篇文章翻译/改写为流畅的中文资讯，200-300字。"
    else:
        summary_instruction = (
            f"这{num_articles}篇文章报道了同一话题。"
            "请综合所有来源，撰写一篇全面的中文资讯摘要，300-500字。"
            "要整合各来源的独特信息，不要简单罗列。"
        )

    prompt = f"""{summary_instruction}

原始文章：
{context}

请严格按以下JSON格式输出（不要输出其他内容）：
{{
  "title": "中文标题（简洁有力，15字以内）",
  "summary": "中文综合摘要",
  "key_points": ["要点1", "要点2", "要点3"],
  "tags": ["标签1", "标签2", "标签3"],
  "is_sensitive": false
}}

注意：
1. 标题要吸引人，像杂志标题
2. 摘要要全面整合多源信息，语言流畅自然
3. key_points 提取3-5个核心要点
4. 如果涉及中国政治敏感话题，is_sensitive设为true
5. tags是中文关键词"""

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位顶级时尚杂志的中文编辑。你的任务是将多源时尚资讯"
                "综合为一篇高质量的中文报道。语言要专业、流畅、有杂志感。只输出JSON。"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    result = call_llm(messages, temperature=0.3, max_tokens=2000)
    return extract_json(result)


# ─── Main Pipeline ──────────────────────────────────────────────────────────

def run_clustering(articles):
    """Cluster articles into topics using batched LLM calls."""
    if not OPENROUTER_API_KEY:
        logger.warning("No API key — skipping clustering, using raw articles as topics")
        return _fallback_no_llm(articles)

    article_lines = build_article_index(articles)
    total = len(article_lines)
    batch_size = 50  # Process 50 articles per LLM call
    all_clusters = []

    logger.info(f"Clustering {total} articles in batches of {batch_size}...")
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = article_lines[start:end]
        logger.info(f"  Clustering batch [{start}:{end}]...")

        clusters = cluster_batch(batch, start)
        if clusters:
            all_clusters.extend(clusters)
        else:
            # Fallback: each article is its own topic
            logger.warning(f"  Batch [{start}:{end}] clustering failed, using fallback")
            for i in range(start, end):
                all_clusters.append(
                    {
                        "topic": "",
                        "indices": [i],
                        "category": articles[i]["category_hint"],
                    }
                )
        time.sleep(1)  # Rate limit

    # Validate: ensure no article is in multiple clusters, no article is lost
    assigned = set()
    valid_clusters = []
    for c in all_clusters:
        clean_indices = [i for i in c["indices"] if i < total and i not in assigned]
        if clean_indices:
            assigned.update(clean_indices)
            c["indices"] = clean_indices
            valid_clusters.append(c)

    # Add any orphaned articles as single-article topics
    for i in range(total):
        if i not in assigned:
            valid_clusters.append(
                {
                    "topic": "",
                    "indices": [i],
                    "category": articles[i]["category_hint"],
                }
            )

    logger.info(f"Formed {len(valid_clusters)} topic clusters")
    return valid_clusters


def generate_all_summaries(articles, clusters):
    """Generate Chinese summaries for all topic clusters."""
    if not OPENROUTER_API_KEY:
        return _fallback_no_llm(articles)

    # Sort clusters: multi-article clusters first, then by recency
    def cluster_sort_key(c):
        size = len(c["indices"])
        newest = max(articles[i]["published"] for i in c["indices"])
        return (-size, newest)

    clusters.sort(key=cluster_sort_key, reverse=False)
    # Actually we want multi-article first, then newest first
    clusters.sort(key=lambda c: (-len(c["indices"]), ""))

    topics = []
    logger.info(f"Generating summaries for {min(len(clusters), MAX_TOPICS)} topics...")

    for ci, cluster in enumerate(clusters[:MAX_TOPICS]):
        topic_articles = [articles[i] for i in cluster["indices"]]
        logger.info(
            f"  Topic [{ci+1}/{min(len(clusters), MAX_TOPICS)}] "
            f"({len(topic_articles)} articles): "
            f"{topic_articles[0]['title'][:50]}..."
        )

        summary_data = generate_topic_summary(topic_articles)

        if summary_data and not summary_data.get("is_sensitive", False):
            # Pick the best image from the cluster
            images = [a["image"] for a in topic_articles if a.get("image")]
            best_image = images[0] if images else ""

            # Collect all source references
            sources_list = []
            for a in topic_articles:
                sources_list.append(
                    {
                        "name": a["source"],
                        "title": a["title"],
                        "link": a["link"],
                        "lang": a["source_lang"],
                    }
                )

            # Newest published date
            newest_date = max(a["published"] for a in topic_articles)

            category_name = cluster.get("category", topic_articles[0].get("category_hint", "行业动态"))
            category_id = CATEGORY_MAP.get(category_name, "industry")

            topics.append(
                {
                    "id": hashlib.md5(
                        summary_data.get("title", "")[:30].encode()
                    ).hexdigest()[:12],
                    "title": summary_data.get("title", topic_articles[0]["title"]),
                    "summary": summary_data.get("summary", ""),
                    "key_points": summary_data.get("key_points", []),
                    "tags": summary_data.get("tags", []),
                    "category": category_id,
                    "category_name": category_name,
                    "image": best_image,
                    "published": newest_date,
                    "sources": sources_list,
                    "article_count": len(topic_articles),
                }
            )
        elif summary_data and summary_data.get("is_sensitive", False):
            logger.info(f"    Skipped (sensitive content)")
        else:
            # Fallback for failed LLM call
            a = topic_articles[0]
            topics.append(
                {
                    "id": a["id"],
                    "title": a["title"],
                    "summary": a["content_snippet"][:300],
                    "key_points": [],
                    "tags": [],
                    "category": cluster.get("category_id", a.get("category_id", "industry")),
                    "category_name": a.get("category_hint", "行业动态"),
                    "image": a.get("image", ""),
                    "published": a["published"],
                    "sources": [
                        {
                            "name": a2["source"],
                            "title": a2["title"],
                            "link": a2["link"],
                            "lang": a2["source_lang"],
                        }
                        for a2 in topic_articles
                    ],
                    "article_count": len(topic_articles),
                }
            )

        time.sleep(0.5)

    # Sort final topics by date (newest first)
    topics.sort(key=lambda t: t.get("published", ""), reverse=True)
    return topics


def _fallback_no_llm(articles):
    """Fallback when no LLM API key: treat each article as its own topic."""
    topics = []
    for a in articles[:MAX_TOPICS]:
        topics.append(
            {
                "id": a["id"],
                "title": a["title"],
                "summary": a["content_snippet"][:300],
                "key_points": [],
                "tags": [],
                "category": a.get("category_id", "industry"),
                "category_name": a.get("category_hint", "行业动态"),
                "image": a.get("image", ""),
                "published": a["published"],
                "sources": [
                    {
                        "name": a["source"],
                        "title": a["title"],
                        "link": a["link"],
                        "lang": a["source_lang"],
                    }
                ],
                "article_count": 1,
            }
        )
    return topics


def build_output(topics, all_sources):
    """Build the final JSON for the frontend."""
    output = {
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
    return output


def main():
    logger.info("=" * 60)
    logger.info("Fashion Feed Aggregator v2 — Topic Clustering Mode")
    logger.info("=" * 60)

    # Step 1: Load sources
    sources = load_sources()

    # Step 2: Fetch all feeds
    articles = fetch_all_feeds(sources)
    if not articles:
        logger.error("No articles fetched, exiting")
        sys.exit(1)

    all_source_names = list(set(a["source"] for a in articles))

    # Step 3: Cluster into topics
    clusters = run_clustering(articles)

    # Step 4: Generate summaries
    topics = generate_all_summaries(articles, clusters)

    # Step 5: Build and write output
    output = build_output(topics, all_source_names)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"Output: {OUTPUT_FILE}")
    logger.info(f"Topics: {output['meta']['total_topics']}")
    logger.info(f"Articles covered: {output['meta']['total_articles']}")
    logger.info(f"Sources: {output['meta']['sources_count']}")
    logger.info("Done!")


if __name__ == "__main__":
    main()
