from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from backend.app.config.source_config import (
    DetailConfig,
    DiscoveryConfig,
    SourceConfig,
)
from backend.app.service.news_collection_service import NewsCollectionService


class NewsCollectionServiceTest(unittest.TestCase):
    def test_collect_rss_articles(self) -> None:
        rss_source = SourceConfig(
            name="Vogue",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/feed.xml",
        )

        xml_text = """<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
          <channel>
            <title>Example Feed</title>
            <item>
              <title>Runway Story</title>
              <link>https://example.com/story?utm_source=rss#top</link>
              <description><![CDATA[An extended summary for the story that is long enough to avoid detail fallback. An extended summary for the story that is long enough to avoid detail fallback.]]></description>
              <pubDate>Fri, 13 Mar 2026 08:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        """

        async def fetch_text(url: str) -> str:
            self.assertEqual(url, "https://example.com/feed.xml")
            return xml_text

        service = NewsCollectionService(source_configs=[rss_source], fetch_text=fetch_text)
        articles = asyncio.run(service.collect_articles())

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].canonical_url, "https://example.com/story")
        self.assertEqual(articles[0].title, "Runway Story")

    def test_collect_articles_filters_by_published_after(self) -> None:
        rss_source = SourceConfig(
            name="Vogue",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/feed.xml",
        )

        xml_text = """<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Recent Story</title>
              <link>https://example.com/recent</link>
              <description>Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary Recent summary</description>
              <pubDate>Fri, 13 Mar 2026 08:00:00 GMT</pubDate>
            </item>
            <item>
              <title>Old Story</title>
              <link>https://example.com/old</link>
              <description>Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary Old summary</description>
              <pubDate>Fri, 10 Jan 2026 08:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        """

        async def fetch_text(url: str) -> str:
            if url == "https://example.com/feed.xml":
                return xml_text
            return """
                <html><body>
                  <h1>Fallback</h1>
                  <article>Fallback body</article>
                </body></html>
            """

        service = NewsCollectionService(source_configs=[rss_source], fetch_text=fetch_text)
        articles = asyncio.run(
            service.collect_articles(
                published_after=datetime(2026, 2, 12, 0, 0, 0),
            )
        )

        self.assertEqual([article.title for article in articles], ["Recent Story"])

    def test_collect_web_articles(self) -> None:
        web_source = SourceConfig(
            name="Complex Style",
            type="web",
            lang="en",
            category="潮流街头",
            max_articles=5,
            start_urls=("https://example.com/style",),
            allowed_domains=("example.com",),
            discovery=DiscoveryConfig(
                link_selectors=("a.story-link",),
                article_url_patterns=(r"/style/story-1$",),
            ),
            detail=DetailConfig(
                title_selectors=("h1",),
                content_selectors=("article",),
                published_selectors=("time[datetime]",),
                image_selectors=("meta[property='og:image']",),
            ),
        )

        pages = {
            "https://example.com/style": """
                <html><body>
                  <a class="story-link" href="/style/story-1?utm_source=feed">Story</a>
                </body></html>
            """,
            "https://example.com/style/story-1": """
                <html>
                  <head>
                    <link rel="canonical" href="https://example.com/style/story-1" />
                    <meta property="og:image" content="https://example.com/image.jpg" />
                  </head>
                  <body>
                    <h1>Street Style Story</h1>
                    <time datetime="2026-03-13T08:00:00+00:00"></time>
                    <article>Full article body for semantic clustering.</article>
                  </body>
                </html>
            """,
        }

        async def fetch_text(url: str) -> str:
            return pages[url]

        service = NewsCollectionService(source_configs=[web_source], fetch_text=fetch_text)
        articles = asyncio.run(service.collect_articles())

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].canonical_url, "https://example.com/style/story-1")
        self.assertEqual(articles[0].image_url, "https://example.com/image.jpg")
        self.assertIn("semantic clustering", articles[0].content)

    def test_collect_web_articles_respects_max_pages_override(self) -> None:
        web_source = SourceConfig(
            name="Complex Style",
            type="web",
            lang="en",
            category="潮流街头",
            max_articles=5,
            start_urls=("https://example.com/style",),
            allowed_domains=("example.com",),
            discovery=DiscoveryConfig(
                link_selectors=("a.story-link",),
                article_url_patterns=(r"/style/story-\d$",),
                pagination_selectors=("a.next",),
                max_pages=1,
            ),
            detail=DetailConfig(
                title_selectors=("h1",),
                content_selectors=("article",),
            ),
        )

        pages = {
            "https://example.com/style": """
                <html><body>
                  <a class="next" href="/style/page-2">Next</a>
                </body></html>
            """,
            "https://example.com/style/page-2": """
                <html><body>
                  <a class="story-link" href="/style/story-2">Story</a>
                </body></html>
            """,
            "https://example.com/style/story-2": """
                <html><body>
                  <h1>Second Page Story</h1>
                  <article>Body from page two.</article>
                </body></html>
            """,
        }

        async def fetch_text(url: str) -> str:
            return pages[url]

        service = NewsCollectionService(source_configs=[web_source], fetch_text=fetch_text)
        articles = asyncio.run(
            service.collect_articles(
                max_pages_per_source=2,
                include_undated=True,
            )
        )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "Second Page Story")

    def test_collect_source_results_skips_failed_source(self) -> None:
        rss_source = SourceConfig(
            name="Good Feed",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/good.xml",
        )
        bad_source = SourceConfig(
            name="Bad Feed",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/bad.xml",
        )

        xml_text = """<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Runway Story</title>
              <link>https://example.com/story</link>
              <description>Long enough summary Long enough summary Long enough summary Long enough summary Long enough summary Long enough summary</description>
              <pubDate>Fri, 13 Mar 2026 08:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        """

        async def fetch_text(url: str) -> str:
            if url == "https://example.com/bad.xml":
                raise RuntimeError("boom")
            return xml_text

        service = NewsCollectionService(
            source_configs=[rss_source, bad_source],
            fetch_text=fetch_text,
            source_concurrency=2,
            continue_on_source_error=True,
        )

        results = asyncio.run(service.collect_source_results())
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].source_name, "Good Feed")
        self.assertEqual(len(results[0].articles), 1)
        self.assertIsNone(results[0].error)
        self.assertEqual(results[1].source_name, "Bad Feed")
        self.assertIsNotNone(results[1].error)


if __name__ == "__main__":
    unittest.main()
