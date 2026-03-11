import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.service import news_collection_service as news_service


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


if __name__ == "__main__":
    unittest.main()
