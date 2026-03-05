#!/usr/bin/env python3
"""
Fashion Feed Aggregator v5 — Optimized Pipeline
=================================================
Pipeline:
  1. Load RSS sources from sources.yaml
  2. Fetch articles from all enabled sources (parallel)
  3. Deduplicate by normalized URL + fuzzy title matching
  4. TF-IDF vectorize → Agglomerative Clustering (cosine distance)
  5. LLM verification: for multi-article clusters only, confirm or split
  6. LLM batch summary: process clusters in batches of 5 concurrently
  7. Output feed-data.json for the frontend (up to 500 topics)

Key optimizations vs v4:
  - Concurrent LLM calls (ThreadPoolExecutor, 5 workers)
  - Relaxed clustering threshold (0.78) for better aggregation
  - Batch processing with progress tracking
"""

import os, sys, json, hashlib, re, datetime, time, logging
from urllib.parse import urlparse
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
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.0-flash-001")

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "client" / "public"
OUTPUT_FILE = OUTPUT_DIR / "feed-data.json"
SOURCES_FILE = SCRIPT_DIR / "sources.yaml"

MAX_ARTICLES_PER_FEED = 30
MAX_TOPICS = 500
LLM_CONCURRENCY = 5  # concurrent LLM calls

# Clustering: higher = more merges. 0.78 means articles need >22% cosine similarity
CLUSTER_DISTANCE_THRESHOLD = 0.78
TITLE_DEDUP_THRESHOLD = 0.6

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


# ─── Utilities ───────────────────────────────────────────────────────────────

def clean_html(html_content):
    if not html_content: return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["script","style","img","video","audio","iframe","input","noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()[:3000]

def extract_image(entry):
    for attr in ["media_content", "media_thumbnail"]:
        items = getattr(entry, attr, None)
        if items:
            for item in (items if isinstance(items, list) else [items]):
                url = item.get("url", "") if isinstance(item, dict) else ""
                if url: return url
    if hasattr(entry, "enclosures"):
        for enc in entry.enclosures:
            if enc.get("type","").startswith("image"): return enc.get("href","")
    for attr in ["content", "description", "summary"]:
        val = getattr(entry, attr, None)
        if val:
            content = val[0].get("value","") if isinstance(val, list) else (val or "")
            if content:
                soup = BeautifulSoup(content, "html.parser")
                img = soup.find("img")
                if img and img.get("src","").startswith("http"): return img["src"]
    return ""

def normalize_url(url):
    if not url: return ""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()

def get_published_date(entry):
    for attr in ["published_parsed","updated_parsed","created_parsed"]:
        parsed = getattr(entry, attr, None)
        if parsed:
            try: return datetime.datetime(*parsed[:6]).isoformat()
            except: pass
    for attr in ["published","updated","created"]:
        val = getattr(entry, attr, None)
        if val: return val
    return datetime.datetime.now().isoformat()

def title_bigrams(title):
    t = re.sub(r"[^\w\s]", "", title.lower().strip())
    t = re.sub(r"\s+", " ", t)
    return {t[i:i+2] for i in range(len(t)-1)} if len(t) >= 2 else set()

def jaccard_sim(a, b):
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


# ─── RSS Fetching ────────────────────────────────────────────────────────────

def load_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        sources = yaml.safe_load(f)
    enabled = [s for s in sources if s.get("enabled", True)]
    logger.info(f"Loaded {len(enabled)} enabled sources")
    return enabled

def fetch_single_feed(cfg):
    name, url = cfg["name"], cfg["url"]
    lang = cfg.get("lang", "en")
    category = cfg.get("category", "品牌/市场")
    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"  [{name}] HTTP {resp.status_code}")
            return articles
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
            link = getattr(entry, "link", "")
            title = getattr(entry, "title", "")
            if not link or not title: continue
            content = ""
            if hasattr(entry, "content") and entry.content:
                content = entry.content[0].get("value","")
            elif hasattr(entry, "description"): content = entry.description or ""
            elif hasattr(entry, "summary"): content = entry.summary or ""
            articles.append({
                "id": hashlib.md5((link+title).encode()).hexdigest()[:12],
                "title": title.strip(), "link": link, "source": name,
                "source_lang": lang, "category_hint": category,
                "category_id": CATEGORY_MAP.get(category, "brand-market"),
                "image": extract_image(entry),
                "published": get_published_date(entry),
                "content_snippet": clean_html(content)[:800],
            })
        logger.info(f"  [{name}] {len(articles)} articles")
    except Exception as e:
        logger.error(f"  [{name}] Error: {e}")
    return articles

def fetch_all_feeds(sources):
    all_articles = []
    logger.info(f"Fetching from {len(sources)} sources...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for future in as_completed({ex.submit(fetch_single_feed, s): s for s in sources}):
            all_articles.extend(future.result())
    logger.info(f"Total raw articles: {len(all_articles)}")
    return all_articles


# ─── Deduplication ───────────────────────────────────────────────────────────

def deduplicate_articles(articles):
    seen_urls, seen_bgs, deduped = set(), [], []
    for art in articles:
        norm = normalize_url(art["link"])
        if norm in seen_urls: continue
        bg = title_bigrams(art["title"])
        if any(jaccard_sim(bg, eb) > TITLE_DEDUP_THRESHOLD for eb in seen_bgs): continue
        seen_urls.add(norm); seen_bgs.append(bg); deduped.append(art)
    deduped.sort(key=lambda x: x.get("published",""), reverse=True)
    logger.info(f"After dedup: {len(deduped)} articles (removed {len(articles)-len(deduped)})")
    return deduped


# ─── TF-IDF Clustering ──────────────────────────────────────────────────────

def cluster_articles(articles):
    if len(articles) <= 1: return [[i] for i in range(len(articles))]

    # Build corpus: title repeated 3x + snippet for weight
    corpus = [f"{a['title']} {a['title']} {a['title']} {a['content_snippet'][:300]}" for a in articles]

    logger.info("Computing TF-IDF vectors...")
    tfidf = TfidfVectorizer(max_features=10000, ngram_range=(1,2), stop_words="english", min_df=1, max_df=0.95)
    matrix = tfidf.fit_transform(corpus)
    logger.info(f"TF-IDF matrix: {matrix.shape}")

    logger.info(f"Agglomerative Clustering (threshold={CLUSTER_DISTANCE_THRESHOLD})...")
    model = AgglomerativeClustering(
        n_clusters=None, distance_threshold=CLUSTER_DISTANCE_THRESHOLD,
        metric="cosine", linkage="average"
    )
    labels = model.fit_predict(matrix.toarray())

    clusters = {}
    for idx, lbl in enumerate(labels):
        clusters.setdefault(lbl, []).append(idx)
    result = list(clusters.values())

    multi = [c for c in result if len(c) > 1]
    logger.info(f"Clusters: {len(result)} total, {len(multi)} multi-article (max size: {max(len(c) for c in multi) if multi else 0})")
    if multi:
        for c in sorted(multi, key=len, reverse=True)[:10]:
            titles = [articles[i]["title"][:50] for i in c]
            logger.info(f"  [{len(c)}x] {titles[0]}...")
    return result


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
            resp = requests.post(OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=90)
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
        context_parts.append(f"来源: {a['source']} ({a['source_lang']})\n标题: {a['title']}\n内容: {a['content_snippet'][:500]}")
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
            "summary": a["content_snippet"][:300],
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
            "summary": a["content_snippet"][:300],
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
    logger.info("Fashion Feed Aggregator v5 — Optimized Pipeline")
    logger.info("="*60)

    sources = load_sources()
    raw_articles = fetch_all_feeds(sources)
    if not raw_articles:
        logger.error("No articles fetched"); sys.exit(1)

    articles = deduplicate_articles(raw_articles)
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
