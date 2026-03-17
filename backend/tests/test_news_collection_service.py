from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from backend.app.config.source_config import DetailConfig, DiscoveryConfig, SourceConfig
from backend.app.service.news_collection_service import NewsCollectionService


class NewsCollectionServiceTest(unittest.TestCase):
    def test_collect_rss_articles_resolves_canonical_seed(self) -> None:
        rss_source = SourceConfig(
            name="Vogue",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/feed.xml",
            detail=DetailConfig(
                title_selectors=("h1",),
                content_selectors=("article",),
            ),
        )

        xml_text = """<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Runway Story</title>
              <link>https://example.com/story?utm_source=rss#top</link>
              <description><![CDATA[<p>Short summary.</p>]]></description>
              <pubDate>Fri, 13 Mar 2026 08:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        """
        detail_html = """
            <html>
              <head>
                <link rel="canonical" href="https://example.com/story" />
                <meta name="description" content="Runway detail summary" />
              </head>
              <body>
                <h1>Runway Story</h1>
                <article>
                  <p>Long runway paragraph with enough detail for seed extraction.</p>
                </article>
              </body>
            </html>
        """

        async def fetch_text(url: str) -> str:
            if url == "https://example.com/feed.xml":
                return xml_text
            return detail_html

        service = NewsCollectionService(source_configs=[rss_source], fetch_text=fetch_text)
        articles = asyncio.run(service.collect_articles())

        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertEqual(article.canonical_url, "https://example.com/story")
        self.assertEqual(article.title, "Runway Story")
        self.assertEqual(article.summary, "Runway detail summary")
        self.assertEqual(article.published_at, datetime(2026, 3, 13, 8, 0, 0))

    def test_collect_articles_filters_by_published_after(self) -> None:
        rss_source = SourceConfig(
            name="Vogue",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/feed.xml",
            detail=DetailConfig(
                title_selectors=("h1",),
                content_selectors=("article",),
            ),
        )

        xml_text = """<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Recent Story</title>
              <link>https://example.com/recent</link>
              <pubDate>Fri, 13 Mar 2026 08:00:00 GMT</pubDate>
            </item>
            <item>
              <title>Old Story</title>
              <link>https://example.com/old</link>
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
                  <h1>Fallback detail title.</h1>
                  <article><p>Fallback detail body.</p></article>
                </body></html>
            """

        service = NewsCollectionService(source_configs=[rss_source], fetch_text=fetch_text)
        articles = asyncio.run(
            service.collect_articles(published_after=datetime(2026, 2, 12, 0, 0, 0))
        )

        self.assertEqual([article.title for article in articles], ["Fallback detail title."])

    def test_collect_web_articles_discovers_urls_and_returns_seed(self) -> None:
        web_source = SourceConfig(
            name="Street Style",
            type="web",
            lang="en",
            category="潮流街头",
            max_articles=5,
            start_urls=("https://example.com/style",),
            allowed_domains=("example.com",),
            discovery=DiscoveryConfig(
                link_selectors=("a.story-link",),
                article_url_patterns=(r"/style/story-\d$",),
            ),
            detail=DetailConfig(
                title_selectors=("h1",),
                content_selectors=("article",),
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
                  </head>
                  <body>
                    <h1>Street Style Story</h1>
                    <article>
                      <p>Lead paragraph before image.</p>
                    </article>
                  </body>
                </html>
            """,
        }

        async def fetch_text(url: str) -> str:
            return pages[url]

        service = NewsCollectionService(source_configs=[web_source], fetch_text=fetch_text)
        articles = asyncio.run(service.collect_articles())

        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertEqual(article.canonical_url, "https://example.com/style/story-1")
        self.assertEqual(article.title, "Street Style Story")
        self.assertEqual(article.summary, "Lead paragraph before image.")

    def test_parse_article_html_returns_text_blocks_and_images(self) -> None:
        source = SourceConfig(
            name="Vogue",
            type="rss",
            lang="en",
            category="高端时装",
            max_articles=5,
            feed_url="https://example.com/feed.xml",
            detail=DetailConfig(
                title_selectors=("h1",),
                content_selectors=("article",),
                image_selectors=("meta[property='og:image']",),
            ),
        )
        html = """
            <html>
              <head>
                <link rel="canonical" href="https://example.com/story" />
                <meta property="og:image" content="https://example.com/hero.jpg" />
              </head>
              <body>
                <h1>Runway Story</h1>
                <article>
                  <p>Lead paragraph before image.</p>
                  <figure>
                    <img src="https://example.com/look.jpg" alt="Inline look" />
                    <figcaption>Figure caption should stay on image rows.</figcaption>
                  </figure>
                  <p>Second paragraph after the image.</p>
                </article>
              </body>
            </html>
        """

        service = NewsCollectionService(source_configs=[source])
        parsed = service.parse_article_html(
            source_name="Vogue",
            url="https://example.com/story",
            html_text=html,
        )

        self.assertEqual(parsed.title, "Runway Story")
        self.assertEqual(len(parsed.images), 2)
        inline_image = next(image for image in parsed.images if image.source_url == "https://example.com/look.jpg")
        self.assertEqual(tuple(block.kind for block in parsed.markdown_blocks), ("paragraph", "paragraph"))
        self.assertEqual(inline_image.caption_raw, "Figure caption should stay on image rows.")
        self.assertIn("Second paragraph", inline_image.context_snippet)


if __name__ == "__main__":
    unittest.main()
