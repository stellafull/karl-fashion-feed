import importlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.config import llm_config
from backend.app.service import news_collection_service as news_service


SERVICE_MODULE_PATH = Path(__file__).resolve().parents[2] / "app" / "service" / "news_collection_service.py"


class DummyResponse:
    def __init__(self, url, text, *, content_type="text/html"):
        self.url = url
        self.text = text
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"


def load_fresh_news_service(module_name):
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class NewsCollectionServiceTests(unittest.TestCase):
    def test_load_sources_uses_override_path_and_filters_disabled_items(self):
        yaml_text = """
        - name: Enabled RSS
          url: https://example.com/feed.xml
          enabled: true
        - name: Disabled RSS
          url: https://example.com/other.xml
          enabled: false
        """
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
            handle.write(yaml_text)
            sources_path = Path(handle.name)

        try:
            sources = news_service.load_sources(sources_file=sources_path)
        finally:
            sources_path.unlink(missing_ok=True)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["name"], "Enabled RSS")
        self.assertEqual(sources[0]["type"], "rss")

    def test_normalize_source_config_keeps_old_rss_shape_working(self):
        source = news_service.normalize_source_config(
            {
                "name": "Legacy RSS",
                "url": "https://example.com/feed.xml",
                "lang": "zh",
                "category": "品牌/市场",
                "max_articles": 12,
            }
        )

        self.assertEqual(source["type"], "rss")
        self.assertEqual(source["feed_url"], "https://example.com/feed.xml")
        self.assertEqual(source["max_items"], 12)
        self.assertFalse(source["detail"]["fetch_detail"])

    def test_normalize_source_config_supports_crawl(self):
        source = news_service.normalize_source_config(
            {
                "name": "Crawl Source",
                "type": "crawl",
                "start_urls": ["https://example.com/fashion"],
                "allowed_domains": ["example.com"],
                "discovery": {
                    "link_selectors": [".post-list a[href]"],
                    "article_url_patterns": [r"/fashion/"],
                    "exclude_patterns": [r"/tag/"],
                    "max_pages": 3,
                },
                "detail": {
                    "content_selectors": [".article-body"],
                },
            }
        )

        self.assertEqual(source["type"], "crawl")
        self.assertEqual(source["start_urls"], ["https://example.com/fashion"])
        self.assertEqual(source["discovery"]["max_pages"], 3)
        self.assertIn(".article-body", source["detail"]["content_selectors"])
        self.assertTrue(source["detail"]["fetch_detail"])

    def test_extract_discovery_links_uses_domain_and_pattern_filters(self):
        source = news_service.normalize_source_config(
            {
                "name": "Filtered Crawl",
                "type": "crawl",
                "start_urls": ["https://example.com/fashion"],
                "allowed_domains": ["example.com"],
                "discovery": {
                    "link_selectors": [".items a[href]"],
                    "article_url_patterns": [r"/fashion/"],
                    "exclude_patterns": [r"/tag/"],
                },
            }
        )

        html = """
        <div class="items">
          <a href="/fashion/look-1">Look 1</a>
          <a href="/tag/look-1">Tag page</a>
          <a href="https://outside.com/fashion/look-2">Outside</a>
        </div>
        """
        links = news_service.extract_discovery_links(html, "https://example.com/fashion", source)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], "https://example.com/fashion/look-1")

    def test_parse_article_page_uses_configured_selectors_and_canonical_url(self):
        html = """
        <html>
          <head>
            <link rel="canonical" href="/stories/look-1?utm_source=rss&ref=homepage">
          </head>
          <body>
            <h1>Fallback title</h1>
            <div class="custom-title">Configured Runway Story</div>
            <div class="meta"><time datetime="2026-03-09T12:00:00Z"></time></div>
            <div class="hero"><img src="/images/look-1.jpg" alt="Look 1"></div>
            <div class="article-copy">
              <p>First paragraph covers runway silhouettes, fabric direction, and styling choices in enough detail to clear the extraction threshold cleanly.</p>
              <div class="remove-me">Remove this subscription prompt.</div>
              <p>Second paragraph adds casting context, venue notes, and brand positioning so the configured selector path returns a realistic article body.</p>
            </div>
          </body>
        </html>
        """

        parsed = news_service.parse_article_page(
            html,
            "https://example.com/news/look-1?utm_medium=social",
            detail_cfg={
                "title_selectors": [".custom-title"],
                "content_selectors": [".article-copy"],
                "published_selectors": [".meta time"],
                "image_selectors": [".hero img"],
                "remove_selectors": [".remove-me"],
                "strip_query_params": ["ref"],
            },
        )

        self.assertEqual(parsed["canonical_url"], "https://example.com/stories/look-1")
        self.assertEqual(parsed["title"], "Configured Runway Story")
        self.assertEqual(parsed["published"], "2026-03-09T12:00:00+00:00")
        self.assertEqual(parsed["image"], "https://example.com/images/look-1.jpg")
        self.assertIn("First paragraph covers runway silhouettes", parsed["content_text"])
        self.assertIn("Second paragraph adds casting context", parsed["content_text"])
        self.assertNotIn("subscription prompt", parsed["content_text"])

    def test_parse_article_page_falls_back_to_supplied_values(self):
        fallback = {
            "title": "Fallback title",
            "published": "2026-03-08T08:30:00+00:00",
            "content_text": "Fallback body copy",
            "image": "https://example.com/fallback.jpg",
        }

        parsed = news_service.parse_article_page(
            "<html><body><div>Short copy only</div></body></html>",
            "https://example.com/story?utm_medium=social&foo=bar",
            detail_cfg={"strip_query_params": ["foo"]},
            fallback=fallback,
        )

        self.assertEqual(parsed["canonical_url"], "https://example.com/story")
        self.assertEqual(parsed["title"], "Fallback title")
        self.assertEqual(parsed["published"], "2026-03-08T08:30:00+00:00")
        self.assertEqual(parsed["content_text"], "Fallback body copy")
        self.assertEqual(parsed["image"], "https://example.com/fallback.jpg")

    def test_fetch_article_detail_returns_fallback_record_when_fetch_fails(self):
        source = news_service.normalize_source_config(
            {
                "name": "Detail Failure",
                "url": "https://example.com/feed.xml",
            }
        )
        fallback = {
            "title": "Fallback headline",
            "published": "2026-03-07T09:15:00+00:00",
            "content_text": "Fallback article body",
            "image": "https://example.com/images/fallback.jpg",
            "canonical_url": "https://example.com/story?utm_medium=email",
        }

        with mock.patch.object(
            news_service,
            "fetch_html_async",
            new=mock.AsyncMock(side_effect=requests.RequestException("boom")),
        ):
            article = news_service.fetch_article_detail(
                source,
                "https://example.com/story?utm_source=rss",
                fallback=fallback,
            )

        self.assertEqual(article["title"], "Fallback headline")
        self.assertEqual(article["link"], "https://example.com/story")
        self.assertEqual(article["canonical_url"], "https://example.com/story")
        self.assertEqual(article["published"], "2026-03-07T09:15:00+00:00")
        self.assertEqual(article["content_text"], "Fallback article body")
        self.assertEqual(article["image"], "https://example.com/images/fallback.jpg")

    def test_fetch_crawl_source_discovers_paginated_articles_and_parses_detail_pages(self):
        source = news_service.normalize_source_config(
            {
                "name": "Paginated Crawl",
                "type": "crawl",
                "start_urls": ["https://example.com/news?page=1&utm_source=feed"],
                "allowed_domains": ["example.com"],
                "max_items": 5,
                "detail_concurrency": 2,
                "discovery": {
                    "link_selectors": [".listing a.story-link"],
                    "article_url_patterns": [r"/stories/"],
                    "exclude_patterns": [r"/sponsored/"],
                    "pagination_selectors": ["a.next"],
                    "max_pages": 2,
                },
                "detail": {
                    "title_selectors": ["h1.headline"],
                    "content_selectors": [".article-body"],
                    "published_selectors": ["time[datetime]"],
                    "image_selectors": [".hero img"],
                },
            }
        )

        page_one_html = """
        <html>
          <body>
            <div class="listing">
              <a class="story-link" href="/stories/look-1?utm_source=feed">Look One teaser</a>
              <a class="story-link" href="/sponsored/look-ignored">Ignore me</a>
            </div>
            <a class="next" href="/news?page=2&utm_medium=next">Next</a>
          </body>
        </html>
        """
        page_two_html = """
        <html>
          <body>
            <div class="listing">
              <a class="story-link" href="https://example.com/stories/look-2?utm_medium=ref">Look Two teaser</a>
            </div>
          </body>
        </html>
        """
        article_one_html = """
        <html>
          <head>
            <link rel="canonical" href="/stories/look-1?utm_campaign=rss">
          </head>
          <body>
            <h1 class="headline">Look One Full Title</h1>
            <time datetime="2026-03-10T09:00:00Z"></time>
            <div class="hero"><img src="/images/look-1.jpg"></div>
            <div class="article-body">
              <p>Look one body paragraph describes silhouettes, accessories, and styling direction in a long enough block to pass the parser threshold.</p>
              <p>Another long paragraph adds brand context, venue notes, and visual references for realistic crawl coverage.</p>
            </div>
          </body>
        </html>
        """
        article_two_html = """
        <html>
          <head>
            <meta property="og:url" content="/stories/look-2?utm_medium=ref">
          </head>
          <body>
            <h1 class="headline">Look Two Full Title</h1>
            <time datetime="2026-03-10T11:30:00Z"></time>
            <div class="hero"><img src="https://example.com/images/look-2.jpg"></div>
            <div class="article-body">
              <p>Look two body paragraph covers fabrication, color direction, and styling references across a detailed seasonal report.</p>
              <p>The second paragraph adds commercial context and buyer reaction so crawl detail parsing returns substantive text.</p>
            </div>
          </body>
        </html>
        """

        responses = {
            "https://example.com/news?page=1&utm_source=feed": DummyResponse(
                "https://example.com/news?page=1&utm_source=feed",
                page_one_html,
            ),
            "https://example.com/news?page=2": DummyResponse(
                "https://example.com/news?page=2",
                page_two_html,
            ),
            "https://example.com/stories/look-1": DummyResponse(
                "https://example.com/stories/look-1?utm_source=feed",
                article_one_html,
            ),
            "https://example.com/stories/look-2": DummyResponse(
                "https://example.com/stories/look-2?utm_medium=ref",
                article_two_html,
            ),
        }

        async def fetch_html_side_effect(url, *, session=None, timeout=news_service.DEFAULT_FETCH_TIMEOUT):
            return responses[url]

        with mock.patch.object(
            news_service,
            "fetch_html_async",
            new=mock.AsyncMock(side_effect=fetch_html_side_effect),
        ):
            articles = news_service.fetch_crawl_source(source)

        self.assertEqual(len(articles), 2)
        by_canonical = {article["canonical_url"]: article for article in articles}
        self.assertEqual(
            set(by_canonical),
            {
                "https://example.com/stories/look-1",
                "https://example.com/stories/look-2",
            },
        )
        self.assertEqual(by_canonical["https://example.com/stories/look-1"]["link"], "https://example.com/stories/look-1")
        self.assertEqual(by_canonical["https://example.com/stories/look-2"]["link"], "https://example.com/stories/look-2")
        self.assertEqual(by_canonical["https://example.com/stories/look-1"]["title"], "Look One Full Title")
        self.assertEqual(by_canonical["https://example.com/stories/look-2"]["title"], "Look Two Full Title")
        self.assertEqual(by_canonical["https://example.com/stories/look-1"]["image"], "https://example.com/images/look-1.jpg")
        self.assertEqual(by_canonical["https://example.com/stories/look-2"]["image"], "https://example.com/images/look-2.jpg")

    def test_deduplicate_articles_prefers_canonical_url(self):
        source = news_service.normalize_source_config(
            {
                "name": "Canonical RSS",
                "url": "https://example.com/feed.xml",
            }
        )
        article_a = news_service.build_article_record(
            source,
            link="https://example.com/story?utm_source=test",
            canonical_url="https://example.com/story",
            title="A Story",
            published="2026-03-01T10:00:00",
            content_text="The same article content",
        )
        article_b = news_service.build_article_record(
            source,
            link="https://example.com/story?utm_medium=email",
            canonical_url="https://example.com/story",
            title="A Story",
            published="2026-03-01T10:05:00",
            content_text="The same article content",
        )

        deduped = news_service.deduplicate_articles([article_a, article_b])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["canonical_url"], "https://example.com/story")

    def test_apply_article_analysis_keeps_fashion_tech_crossovers(self):
        source = news_service.normalize_source_config(
            {
                "name": "Tech Fashion",
                "url": "https://example.com/feed.xml",
            }
        )
        article = news_service.build_article_record(
            source,
            link="https://example.com/apple-watch-fashion",
            title="Apple and Hermès launch new wearable collaboration",
            content_text="A luxury fashion collaboration with a new wearable release.",
        )

        enriched = news_service.apply_article_analysis(
            article,
            {
                "keep": True,
                "relevance_score": 86,
                "reason": "科技品牌与奢侈品牌联名，属于时尚科技交叉内容。",
                "summary_zh": "Apple 与 Hermès 推出新联名可穿戴产品。",
                "category": "品牌/市场",
                "tags": ["Apple", "Hermès", "联名"],
                "content_type": "fashion-tech",
                "is_sensitive": False,
            },
        )

        self.assertTrue(enriched["is_relevant"])
        self.assertEqual(enriched["content_type"], "fashion-tech")
        self.assertEqual(enriched["category_id"], "brand-market")

    def test_apply_article_analysis_drops_irrelevant_or_sensitive_items(self):
        source = news_service.normalize_source_config(
            {
                "name": "General News",
                "url": "https://example.com/feed.xml",
            }
        )
        article = news_service.build_article_record(
            source,
            link="https://example.com/unrelated",
            title="Unrelated macro market update",
            content_text="Generic non-fashion market update.",
        )

        enriched = news_service.apply_article_analysis(
            article,
            {
                "keep": False,
                "relevance_score": 12,
                "reason": "与时尚产业无明显关联。",
                "summary_zh": "无",
                "category": "品牌/市场",
                "tags": [],
                "content_type": "other",
                "is_sensitive": False,
            },
        )
        sensitive = news_service.apply_article_analysis(
            article,
            {
                "keep": True,
                "relevance_score": 70,
                "reason": "敏感内容不保留。",
                "summary_zh": "无",
                "category": "品牌/市场",
                "tags": [],
                "content_type": "other",
                "is_sensitive": True,
            },
        )

        self.assertFalse(enriched["is_relevant"])
        self.assertFalse(sensitive["is_relevant"])
        self.assertTrue(sensitive["is_sensitive"])

    def test_service_module_imports_without_feedparser_available(self):
        real_import_module = importlib.import_module

        def side_effect(name, package=None):
            if name == "feedparser":
                raise ModuleNotFoundError("No module named 'feedparser'")
            return real_import_module(name, package)

        with mock.patch("importlib.import_module", side_effect=side_effect):
            fresh_module = load_fresh_news_service("news_collection_service_no_feedparser")

        self.assertTrue(hasattr(fresh_module, "fetch_rss_source"))
        self.assertTrue(callable(fresh_module.collect_articles))

    def test_fetch_rss_source_raises_controlled_error_without_feedparser(self):
        real_import_module = importlib.import_module

        def side_effect(name, package=None):
            if name == "feedparser":
                raise ModuleNotFoundError("No module named 'feedparser'")
            return real_import_module(name, package)

        with mock.patch("importlib.import_module", side_effect=side_effect):
            fresh_module = load_fresh_news_service("news_collection_service_missing_feedparser")
            source = fresh_module.normalize_source_config(
                {
                    "name": "RSS Source",
                    "url": "https://example.com/feed.xml",
                }
            )

            with self.assertRaises(fresh_module.MissingCollectionDependencyError) as context:
                fresh_module.fetch_rss_source(source)

        self.assertIn("feedparser", str(context.exception))

    def test_collect_articles_runs_collection_pipeline_in_order(self):
        calls = []
        sources = [{"name": "Mock Source"}]
        raw_articles = [{"id": "raw"}]
        deduped_articles = [{"id": "deduped"}]
        imaged_articles = [{"id": "imaged"}]
        enriched_articles = [{"id": "enriched"}]

        def dedup_side_effect(items):
            calls.append(("dedup", items))
            return deduped_articles

        def image_side_effect(items):
            calls.append(("images", items))
            return imaged_articles

        def enrich_side_effect(items):
            calls.append(("enrich", items))
            return enriched_articles

        with mock.patch.object(news_service, "load_sources", return_value=sources) as load_sources:
            with mock.patch.object(news_service, "fetch_all_sources", return_value=raw_articles) as fetch_all_sources:
                with mock.patch.object(news_service, "deduplicate_articles", side_effect=dedup_side_effect):
                    with mock.patch.object(news_service, "fill_missing_images_from_web", side_effect=image_side_effect):
                        with mock.patch.object(news_service, "enrich_and_filter_articles", side_effect=enrich_side_effect):
                            result = news_service.collect_articles(sources_file="custom-sources.yaml")

        self.assertEqual(result, enriched_articles)
        load_sources.assert_called_once_with(sources_file="custom-sources.yaml")
        fetch_all_sources.assert_called_once_with(sources)
        self.assertEqual(
            calls,
            [
                ("dedup", raw_articles),
                ("images", deduped_articles),
                ("enrich", imaged_articles),
            ],
        )

    def test_fetch_all_sources_routes_through_async_fetcher(self):
        sources = [
            {"name": "RSS One", "type": "rss"},
            {"name": "Crawl One", "type": "crawl"},
        ]

        async def fetch_source_async(source, session):
            return [{"source": source["name"]}]

        with mock.patch.object(
            news_service,
            "fetch_source_async",
            new=mock.AsyncMock(side_effect=fetch_source_async),
        ) as fetch_source_async_mock:
            articles = news_service.fetch_all_sources(sources)

        self.assertCountEqual(
            articles,
            [{"source": "RSS One"}, {"source": "Crawl One"}],
        )
        self.assertEqual(fetch_source_async_mock.await_count, 2)


class NewsCollectionLLMConfigTests(unittest.TestCase):
    def test_llm_config_reads_env_overrides_without_service_import_state(self):
        with mock.patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_CHAT_URL": "https://example.com/chat",
                "LLM_MODEL": "example/model",
                "OPENROUTER_HTTP_REFERER": "https://example.com/ref",
            },
            clear=True,
        ):
            config = llm_config.get_news_collection_llm_config()

        self.assertTrue(config.is_configured)
        self.assertEqual(config.provider.api_key, "test-key")
        self.assertEqual(config.provider.chat_url, "https://example.com/chat")
        self.assertEqual(config.provider.http_referer, "https://example.com/ref")
        self.assertEqual(config.article_analysis.model.model, "example/model")
        self.assertEqual(config.article_analysis.model.temperature, 0.1)
        self.assertEqual(config.article_analysis.model.max_tokens, 1200)
        self.assertEqual(config.article_analysis.model.input_chars, 2200)

    def test_llm_prompt_builder_keeps_shared_prompt_contract_explicit(self):
        config = llm_config.get_news_collection_llm_config()
        messages = config.article_analysis.build_messages(article_text="source: Example\ntitle: Test")

        self.assertEqual(messages[0]["content"], config.article_analysis.prompt.system_prompt)
        self.assertIn("请严格输出 JSON", messages[1]["content"])
        self.assertIn("source: Example", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
