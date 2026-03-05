#!/usr/bin/env python3
"""
Fashion Feed Aggregator Script
Fetches RSS feeds from multiple fashion publications, deduplicates articles,
uses LLM to translate/summarize/categorize, and outputs a JSON data file
for the frontend to consume.
"""

import os
import sys
import json
import hashlib
import re
import datetime
import time
import logging
from urllib.parse import urlparse
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup

# ─── Configuration ───────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# OpenRouter API (compatible with OpenAI SDK format)
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'
LLM_MODEL = os.environ.get('LLM_MODEL', 'google/gemini-2.0-flash-001')

# Output paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / 'client' / 'public'
OUTPUT_FILE = OUTPUT_DIR / 'feed-data.json'
CACHE_FILE = SCRIPT_DIR / '.feed_cache.json'

# Max articles per feed to process
MAX_ARTICLES_PER_FEED = 5
# Max total articles to keep
MAX_TOTAL_ARTICLES = 50

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ─── Fashion RSS Sources ─────────────────────────────────────────────────────

FASHION_FEEDS = [
    # English sources
    {"name": "Vogue", "url": "https://www.vogue.com/feed/rss", "lang": "en", "category": "高端时装"},
    {"name": "Vogue Fashion", "url": "https://www.vogue.com/feed/fashion/rss", "lang": "en", "category": "高端时装"},
    {"name": "WWD", "url": "https://wwd.com/feed/", "lang": "en", "category": "行业动态"},
    {"name": "Hypebeast", "url": "https://hypebeast.com/feed", "lang": "en", "category": "潮流街头"},
    {"name": "Hypebeast Fashion", "url": "https://hypebeast.com/fashion/feed", "lang": "en", "category": "潮流街头"},
    {"name": "Highsnobiety", "url": "https://www.highsnobiety.com/feed/", "lang": "en", "category": "潮流街头"},
    {"name": "GQ", "url": "https://www.gq.com/feed/rss", "lang": "en", "category": "男装风尚"},
    {"name": "Elle", "url": "https://www.elle.com/rss/all.xml/", "lang": "en", "category": "高端时装"},
    {"name": "Fashionista", "url": "https://fashionista.com/.rss/full/", "lang": "en", "category": "行业动态"},
    {"name": "BOF", "url": "https://www.businessoffashion.com/feed", "lang": "en", "category": "行业动态"},
    {"name": "Dazed", "url": "https://www.dazeddigital.com/rss", "lang": "en", "category": "先锋文化"},
    {"name": "i-D", "url": "https://i-d.co/feed/", "lang": "en", "category": "先锋文化"},
    {"name": "Harper's Bazaar", "url": "https://www.harpersbazaar.com/rss/all.xml/", "lang": "en", "category": "高端时装"},
    {"name": "Fashion Dive", "url": "https://www.fashiondive.com/feeds/news/", "lang": "en", "category": "行业动态"},
    # Japanese sources
    {"name": "WWD Japan", "url": "https://www.wwdjapan.com/feed", "lang": "ja", "category": "行业动态"},
    {"name": "Vogue Japan", "url": "https://www.vogue.co.jp/feed/rss", "lang": "ja", "category": "高端时装"},
]

# Categories for the frontend
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


# ─── Utility Functions ───────────────────────────────────────────────────────

def clean_html(html_content):
    """Remove HTML tags and clean text for LLM processing."""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["script", "style", "img", "video", "audio", "iframe", "input", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:3000]  # Truncate for LLM context


def extract_image(entry):
    """Extract the best image URL from a feed entry."""
    # Try media:content
    if hasattr(entry, 'media_content') and entry.media_content:
        for media in entry.media_content:
            if media.get('medium') == 'image' or media.get('type', '').startswith('image'):
                return media.get('url', '')
        # If no explicit image type, take first media_content
        if entry.media_content[0].get('url'):
            return entry.media_content[0]['url']

    # Try media:thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')

    # Try enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href', '')

    # Try to find image in content/description
    content = ''
    if hasattr(entry, 'content') and entry.content:
        content = entry.content[0].get('value', '')
    elif hasattr(entry, 'description'):
        content = entry.description or ''
    elif hasattr(entry, 'summary'):
        content = entry.summary or ''

    if content:
        soup = BeautifulSoup(content, 'html.parser')
        img = soup.find('img')
        if img and img.get('src'):
            src = img['src']
            if src.startswith('http'):
                return src

    return ''


def get_article_id(link, title):
    """Generate a unique ID for deduplication."""
    key = (link or '') + (title or '')
    return hashlib.md5(key.encode()).hexdigest()[:12]


def normalize_url(url):
    """Normalize URL for dedup comparison."""
    if not url:
        return ''
    parsed = urlparse(url)
    # Remove common tracking params, fragments
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
    return clean.lower()


def get_published_date(entry):
    """Extract and format published date from entry."""
    for attr in ['published_parsed', 'updated_parsed', 'created_parsed']:
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                dt = datetime.datetime(*parsed[:6])
                return dt.isoformat()
            except:
                pass
    for attr in ['published', 'updated', 'created']:
        val = getattr(entry, attr, None)
        if val:
            return val
    return datetime.datetime.now().isoformat()


# ─── LLM Processing ─────────────────────────────────────────────────────────

def call_llm(messages, temperature=0.3):
    """Call OpenRouter API for LLM processing."""
    if not OPENROUTER_API_KEY:
        return None

    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://fashion-feed.manus.space',
    }

    payload = {
        'model': LLM_MODEL,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': 1000,
    }

    try:
        resp = requests.post(OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"LLM API error: {e}")
        return None


def process_article_with_llm(title, content, source_lang, source_name):
    """Use LLM to translate, summarize, and categorize an article."""
    category_options = "、".join([c["name"] for c in CATEGORIES if c["id"] != "all"])

    prompt = f"""你是一位专业的时尚行业编辑。请处理以下时尚资讯文章。

来源: {source_name}
原文语言: {source_lang}
标题: {title}
内容摘要: {content[:2000]}

请严格按以下JSON格式输出（不要输出其他内容）:
{{
  "title_zh": "中文标题（简洁有力，15字以内）",
  "summary_zh": "中文摘要（100-150字，包含关键信息）",
  "category": "从以下分类中选一个最合适的: {category_options}",
  "tags": ["标签1", "标签2", "标签3"],
  "is_sensitive": false
}}

注意:
1. 如果原文是英文或日文，请翻译为流畅的中文
2. 如果内容涉及政治敏感话题（如中国政治、台湾问题、领土争议等），将is_sensitive设为true
3. tags应该是与时尚相关的中文关键词
4. category必须是给定选项之一"""

    messages = [
        {"role": "system", "content": "你是一位专业的时尚行业中文编辑，擅长翻译和总结时尚资讯。请只输出JSON格式的结果。"},
        {"role": "user", "content": prompt}
    ]

    result = call_llm(messages)
    if not result:
        return None

    # Parse JSON from response
    try:
        # Try to extract JSON from the response
        json_match = re.search(r'\{[\s\S]*\}', result)
        if json_match:
            parsed = json.loads(json_match.group())
            return parsed
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM response for: {title}")

    return None


# ─── News Clustering ─────────────────────────────────────────────────────────

def cluster_similar_articles(articles):
    """Use LLM to identify and cluster similar news stories."""
    if not OPENROUTER_API_KEY or len(articles) < 5:
        return articles

    # Build a summary of all articles for clustering
    article_list = []
    for i, art in enumerate(articles):
        article_list.append(f"{i}. [{art.get('title_zh', art.get('title', ''))}] - {art.get('source', '')}")

    articles_text = "\n".join(article_list)

    prompt = f"""以下是今日时尚资讯列表，请识别报道同一事件/话题的文章组。

{articles_text}

请输出JSON格式（不要输出其他内容）:
{{
  "clusters": [
    {{
      "topic": "话题简述",
      "article_indices": [0, 3, 7],
      "merged_title": "合并后的中文标题",
      "merged_summary": "综合多篇报道的摘要（150字以内）"
    }}
  ]
}}

注意:
1. 只合并确实报道同一事件的文章（至少2篇）
2. 不相关的文章不需要合并
3. 如果没有可合并的文章，返回空clusters数组"""

    messages = [
        {"role": "system", "content": "你是时尚资讯编辑，擅长识别和合并同一话题的新闻报道。请只输出JSON。"},
        {"role": "user", "content": prompt}
    ]

    result = call_llm(messages, temperature=0.2)
    if not result:
        return articles

    try:
        json_match = re.search(r'\{[\s\S]*\}', result)
        if json_match:
            cluster_data = json.loads(json_match.group())
            clusters = cluster_data.get('clusters', [])

            if not clusters:
                return articles

            # Mark clustered articles
            clustered_indices = set()
            merged_articles = []

            for cluster in clusters:
                indices = cluster.get('article_indices', [])
                if len(indices) < 2:
                    continue

                # Use the first article as base, merge info
                base_idx = indices[0]
                if base_idx >= len(articles):
                    continue

                base = articles[base_idx].copy()
                base['title_zh'] = cluster.get('merged_title', base.get('title_zh', ''))
                base['summary_zh'] = cluster.get('merged_summary', base.get('summary_zh', ''))
                base['is_clustered'] = True
                base['cluster_sources'] = []

                for idx in indices:
                    if idx < len(articles):
                        clustered_indices.add(idx)
                        base['cluster_sources'].append({
                            'source': articles[idx].get('source', ''),
                            'title': articles[idx].get('title', ''),
                            'link': articles[idx].get('link', ''),
                        })

                merged_articles.append(base)

            # Add non-clustered articles
            final = merged_articles[:]
            for i, art in enumerate(articles):
                if i not in clustered_indices:
                    final.append(art)

            return final

    except Exception as e:
        logger.warning(f"Clustering failed: {e}")

    return articles


# ─── Main Pipeline ───────────────────────────────────────────────────────────

def load_cache():
    """Load previously processed article IDs."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"processed_ids": [], "last_run": None}


def save_cache(cache):
    """Save processed article IDs."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_all_feeds():
    """Fetch articles from all configured RSS feeds."""
    all_articles = []
    seen_urls = set()

    for feed_config in FASHION_FEEDS:
        name = feed_config['name']
        url = feed_config['url']
        lang = feed_config['lang']
        category = feed_config['category']

        logger.info(f"Fetching: {name} ({url})")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"  Failed ({resp.status_code})")
                continue

            feed = feedparser.parse(resp.text)
            count = 0

            for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
                link = getattr(entry, 'link', '')
                title = getattr(entry, 'title', '')

                if not link or not title:
                    continue

                # Deduplicate by normalized URL
                norm_url = normalize_url(link)
                if norm_url in seen_urls:
                    continue
                seen_urls.add(norm_url)

                # Extract content
                content = ''
                if hasattr(entry, 'content') and entry.content:
                    content = entry.content[0].get('value', '')
                elif hasattr(entry, 'description'):
                    content = entry.description or ''
                elif hasattr(entry, 'summary'):
                    content = entry.summary or ''

                cleaned_content = clean_html(content)
                image_url = extract_image(entry)
                pub_date = get_published_date(entry)

                article = {
                    'id': get_article_id(link, title),
                    'title': title,
                    'link': link,
                    'source': name,
                    'source_lang': lang,
                    'category_hint': category,
                    'category_id': CATEGORY_MAP.get(category, 'industry'),
                    'image': image_url,
                    'published': pub_date,
                    'content_snippet': cleaned_content[:500],
                }

                all_articles.append(article)
                count += 1

            logger.info(f"  Got {count} articles from {name}")

        except Exception as e:
            logger.error(f"  Error fetching {name}: {e}")

    logger.info(f"Total raw articles: {len(all_articles)}")
    return all_articles


def process_with_llm(articles):
    """Process articles through LLM for translation/summary/categorization."""
    if not OPENROUTER_API_KEY:
        logger.warning("No OPENROUTER_API_KEY set, using raw data without LLM processing")
        # Provide basic fallback without LLM
        for art in articles:
            art['title_zh'] = art['title']
            art['summary_zh'] = art['content_snippet'][:150]
            art['tags'] = []
            art['is_sensitive'] = False
        return articles

    processed = []
    for i, art in enumerate(articles):
        logger.info(f"Processing [{i+1}/{len(articles)}]: {art['title'][:60]}...")

        result = process_article_with_llm(
            art['title'],
            art['content_snippet'],
            art['source_lang'],
            art['source']
        )

        if result:
            art['title_zh'] = result.get('title_zh', art['title'])
            art['summary_zh'] = result.get('summary_zh', art['content_snippet'][:150])
            art['tags'] = result.get('tags', [])
            art['is_sensitive'] = result.get('is_sensitive', False)

            # Update category if LLM suggests one
            llm_category = result.get('category', '')
            if llm_category in CATEGORY_MAP:
                art['category_hint'] = llm_category
                art['category_id'] = CATEGORY_MAP[llm_category]
        else:
            art['title_zh'] = art['title']
            art['summary_zh'] = art['content_snippet'][:150]
            art['tags'] = []
            art['is_sensitive'] = False

        processed.append(art)

        # Rate limiting
        time.sleep(0.5)

    return processed


def build_output(articles):
    """Build the final JSON output for the frontend."""
    # Filter out sensitive content
    safe_articles = [a for a in articles if not a.get('is_sensitive', False)]

    # Sort by published date (newest first)
    safe_articles.sort(key=lambda x: x.get('published', ''), reverse=True)

    # Limit total
    safe_articles = safe_articles[:MAX_TOTAL_ARTICLES]

    # Build output structure
    output = {
        "meta": {
            "generated_at": datetime.datetime.now().isoformat(),
            "total_articles": len(safe_articles),
            "sources_count": len(set(a['source'] for a in safe_articles)),
            "sources": list(set(a['source'] for a in safe_articles)),
        },
        "categories": CATEGORIES,
        "articles": [],
    }

    for art in safe_articles:
        output["articles"].append({
            "id": art['id'],
            "title": art.get('title_zh', art['title']),
            "title_original": art['title'],
            "summary": art.get('summary_zh', art.get('content_snippet', '')[:150]),
            "link": art['link'],
            "source": art['source'],
            "category": art['category_id'],
            "category_name": art.get('category_hint', ''),
            "image": art.get('image', ''),
            "published": art.get('published', ''),
            "tags": art.get('tags', []),
            "is_clustered": art.get('is_clustered', False),
            "cluster_sources": art.get('cluster_sources', []),
        })

    return output


def main():
    logger.info("=" * 60)
    logger.info("Fashion Feed Aggregator - Starting")
    logger.info("=" * 60)

    # Step 1: Fetch all feeds
    articles = fetch_all_feeds()

    if not articles:
        logger.error("No articles fetched, exiting")
        sys.exit(1)

    # Step 2: Process with LLM (translate, summarize, categorize)
    articles = process_with_llm(articles)

    # Step 3: Cluster similar news
    articles = cluster_similar_articles(articles)

    # Step 4: Build output
    output = build_output(articles)

    # Step 5: Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"Output written to {OUTPUT_FILE}")
    logger.info(f"Total articles: {output['meta']['total_articles']}")
    logger.info(f"Sources: {output['meta']['sources_count']}")
    logger.info("Done!")


if __name__ == '__main__':
    main()
