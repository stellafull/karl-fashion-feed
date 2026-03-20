from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.config.source_config import DetailConfig, SourceConfig
from backend.app.core.database import Base
from backend.app.models.article import Article, ArticleImage
from backend.app.service.article_collection_service import ArticleCollectionService
from backend.app.service.article_contracts import CollectedArticle
from backend.app.service.article_parse_service import ArticleMarkdownService, ArticleParseService
from backend.app.service.news_collection_service import NewsCollectionService


class StubCollector:
    def __init__(self, articles):
        self._articles = articles
        self.last_kwargs = None

    async def collect_articles(self, **kwargs):
        self.last_kwargs = kwargs
        return list(self._articles)


class ArticleCollectionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def test_store_articles_skips_duplicates_in_batch_and_db(self) -> None:
        service = ArticleCollectionService(session_factory=self.session_factory)
        article = CollectedArticle(
            source_name="Vogue",
            source_type="rss",
            lang="en",
            category="高端时装",
            url="https://example.com/story?utm_source=rss",
            canonical_url="https://example.com/story",
            title="Story",
            summary="Summary",
            published_at=datetime(2026, 3, 13, 8, 0, 0),
        )

        result = service.store_articles([article, article])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.skipped_in_batch, 1)

        second_result = service.store_articles([article])
        self.assertEqual(second_result.inserted, 0)
        self.assertEqual(second_result.skipped_existing, 1)

        with self.session_factory() as session:
            stored_article = session.scalars(select(Article)).one()
            self.assertEqual(stored_article.parse_status, "pending")
            self.assertIsNone(stored_article.markdown_rel_path)
            self.assertEqual(stored_article.published_at, datetime(2026, 3, 13, 8, 0, 0))

    def test_collect_articles_passes_collection_options(self) -> None:
        collector = StubCollector([])
        service = ArticleCollectionService(
            session_factory=self.session_factory,
            collector=collector,
        )

        cutoff = datetime(2026, 2, 12, 8, 0, 0)
        asyncio.run(
            service.collect_articles(
                source_names=["Vogue"],
                limit_sources=3,
                published_after=cutoff,
                max_articles_per_source=100,
                max_pages_per_source=4,
                include_undated=True,
            )
        )

        self.assertEqual(
            collector.last_kwargs,
            {
                "source_names": ["Vogue"],
                "limit_sources": 3,
                "published_after": cutoff,
                "max_articles_per_source": 100,
                "max_pages_per_source": 4,
                "include_undated": True,
            },
        )


class ArticleParseServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _patch_http_session(
        self,
        *,
        text_payloads: dict[str, str] | None = None,
        byte_payloads: dict[str, bytes] | None = None,
        text_errors: dict[str, Exception] | None = None,
    ):
        text_payloads = text_payloads or {}
        byte_payloads = byte_payloads or {}
        text_errors = text_errors or {}

        class FakeResponse:
            def __init__(self, url: str) -> None:
                self._url = url

            async def __aenter__(self):
                if self._url in text_errors:
                    raise text_errors[self._url]
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

            def raise_for_status(self) -> None:
                return None

            async def text(self) -> str:
                if self._url not in text_payloads:
                    raise AssertionError(self._url)
                return text_payloads[self._url]

            async def read(self) -> bytes:
                if self._url not in byte_payloads:
                    raise AssertionError(self._url)
                return byte_payloads[self._url]

        class FakeClientSession:
            def __init__(self, *args, **kwargs) -> None:
                del args, kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

            def get(self, url: str):
                return FakeResponse(url)

        return unittest.mock.patch(
            "backend.app.service.article_parse_service.aiohttp.ClientSession",
            FakeClientSession,
        )

    def test_parse_articles_writes_pure_text_markdown_and_image_assets(self) -> None:
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id="article-1",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/story",
                    original_url="https://example.com/story",
                    title_raw="Seed title",
                    summary_raw="Seed summary",
                    parse_status="pending",
                )
            )
            session.commit()

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
                <meta name="description" content="Runway detail summary" />
              </head>
              <body>
                <h1>Runway Story</h1>
                <article>
                  <p>Lead paragraph before image.</p>
                  <figure>
                    <img src="https://example.com/look.jpg" alt="Look image" />
                    <figcaption>Figure caption that should stay off markdown.</figcaption>
                  </figure>
                  <p>Follow-up paragraph after image.</p>
                </article>
              </body>
            </html>
        """

        collector = NewsCollectionService(source_configs=[source])
        service = ArticleParseService(
            session_factory=self.session_factory,
            collector=collector,
            markdown_service=self.markdown_service,
        )
        with self._patch_http_session(
            text_payloads={"https://example.com/story": html},
            byte_payloads={
                "https://example.com/hero.jpg": _png_bytes((255, 0, 0)),
                "https://example.com/look.jpg": _png_bytes((0, 255, 0)),
            },
        ):
            result = asyncio.run(service.parse_articles(article_ids=["article-1"]))
        self.assertEqual(result.parsed, 1)
        self.assertEqual(result.failed, 0)

        with self.session_factory() as session:
            stored_article = session.get(Article, "article-1")
            stored_images = session.scalars(
                select(ArticleImage).where(ArticleImage.article_id == "article-1").order_by(ArticleImage.position.asc())
            ).all()

        assert stored_article is not None
        hero_image = next(image for image in stored_images if image.image_id == stored_article.hero_image_id)
        inline_image = next(image for image in stored_images if image.source_url == "https://example.com/look.jpg")
        self.assertEqual(stored_article.parse_status, "done")
        self.assertTrue(stored_article.markdown_rel_path.endswith(".md"))
        self.assertEqual(stored_article.image_url, "https://example.com/hero.jpg")
        self.assertGreater(stored_article.character_count or 0, 0)
        self.assertEqual(hero_image.source_url, "https://example.com/hero.jpg")
        self.assertEqual(len(stored_images), 2)
        self.assertTrue(all(image.image_hash for image in stored_images))
        self.assertEqual(inline_image.caption_raw, "Figure caption that should stay off markdown.")
        self.assertIn("Follow-up paragraph", inline_image.context_snippet)

        markdown_path = Path(self.temp_dir.name) / stored_article.markdown_rel_path
        markdown_content = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# Runway Story", markdown_content)
        self.assertIn("Lead paragraph before image.", markdown_content)
        self.assertIn("Follow-up paragraph after image.", markdown_content)
        self.assertNotIn("[image:", markdown_content)
        self.assertNotIn("Figure caption", markdown_content)

    def test_parse_articles_marks_failures_without_writing_markdown(self) -> None:
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id="article-2",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/fail",
                    original_url="https://example.com/fail",
                    title_raw="Seed title",
                    summary_raw="Seed summary",
                    parse_status="pending",
                )
            )
            session.commit()

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
            ),
        )

        collector = NewsCollectionService(source_configs=[source])
        service = ArticleParseService(
            session_factory=self.session_factory,
            collector=collector,
            markdown_service=self.markdown_service,
        )
        with self._patch_http_session(
            text_errors={"https://example.com/fail": RuntimeError("boom")},
        ):
            result = asyncio.run(service.parse_articles(article_ids=["article-2"]))
        self.assertEqual(result.parsed, 0)
        self.assertEqual(result.failed, 1)

        with self.session_factory() as session:
            stored_article = session.get(Article, "article-2")
            stored_images = session.scalars(select(ArticleImage)).all()

        assert stored_article is not None
        self.assertEqual(stored_article.parse_status, "failed")
        self.assertIn("boom", stored_article.parse_error or "")
        self.assertEqual(stored_images, [])

    def test_parse_articles_preserve_image_ids_on_reparse(self) -> None:
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id="article-3",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/reparse",
                    original_url="https://example.com/reparse",
                    title_raw="Seed title",
                    summary_raw="Seed summary",
                    parse_status="pending",
                )
            )
            session.commit()

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
                <link rel="canonical" href="https://example.com/reparse" />
                <meta property="og:image" content="https://example.com/hero.jpg" />
              </head>
              <body>
                <h1>Runway Story</h1>
                <article>
                  <p>Lead paragraph before image.</p>
                  <img src="https://example.com/look.jpg" alt="Look image" />
                  <p>Follow-up paragraph after image.</p>
                </article>
              </body>
            </html>
        """

        collector = NewsCollectionService(source_configs=[source])
        service = ArticleParseService(
            session_factory=self.session_factory,
            collector=collector,
            markdown_service=self.markdown_service,
        )
        with self._patch_http_session(
            text_payloads={"https://example.com/reparse": html},
            byte_payloads={
                "https://example.com/hero.jpg": _png_bytes((255, 0, 0)),
                "https://example.com/look.jpg": _png_bytes((0, 255, 0)),
            },
        ):
            first_result = asyncio.run(service.parse_articles(article_ids=["article-3"]))
        self.assertEqual(first_result.parsed, 1)

        with self.session_factory() as session:
            first_images = session.scalars(
                select(ArticleImage)
                .where(ArticleImage.article_id == "article-3")
                .order_by(ArticleImage.position.asc())
            ).all()
            image_ids_by_url = {image.normalized_url: image.image_id for image in first_images}
            hero_image = next(image for image in first_images if image.normalized_url == "https://example.com/hero.jpg")
            hero_image.observed_description = "keep me"
            session.commit()

            stored_article = session.get(Article, "article-3")
            assert stored_article is not None
            stored_article.parse_status = "failed"
            session.commit()

        with self._patch_http_session(
            text_payloads={"https://example.com/reparse": html},
            byte_payloads={
                "https://example.com/hero.jpg": _png_bytes((255, 0, 0)),
                "https://example.com/look.jpg": _png_bytes((0, 255, 0)),
            },
        ):
            second_result = asyncio.run(service.parse_articles(article_ids=["article-3"]))
        self.assertEqual(second_result.parsed, 1)

        with self.session_factory() as session:
            reparsed_images = session.scalars(
                select(ArticleImage)
                .where(ArticleImage.article_id == "article-3")
                .order_by(ArticleImage.position.asc())
            ).all()
            reparsed_by_url = {image.normalized_url: image for image in reparsed_images}

        self.assertEqual(
            {url: image.image_id for url, image in reparsed_by_url.items()},
            image_ids_by_url,
        )
        self.assertEqual(reparsed_by_url["https://example.com/hero.jpg"].observed_description, "keep me")

    def test_parse_articles_reuse_duplicate_image_analysis_by_hash(self) -> None:
        with self.session_factory() as session:
            session.add_all(
                [
                    Article(
                        article_id="article-4",
                        source_name="Vogue",
                        source_type="rss",
                        source_lang="en",
                        category="高端时装",
                        canonical_url="https://example.com/first",
                        original_url="https://example.com/first",
                        title_raw="Seed title",
                        summary_raw="Seed summary",
                        parse_status="pending",
                    ),
                    Article(
                        article_id="article-5",
                        source_name="Vogue",
                        source_type="rss",
                        source_lang="en",
                        category="高端时装",
                        canonical_url="https://example.com/second",
                        original_url="https://example.com/second",
                        title_raw="Seed title",
                        summary_raw="Seed summary",
                        parse_status="pending",
                    ),
                ]
            )
            session.commit()

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
            ),
        )
        pages = {
            "https://example.com/first": """
                <html><body><h1>First</h1><article><img src="https://example.com/shared.jpg" /></article></body></html>
            """,
            "https://example.com/second": """
                <html><body><h1>Second</h1><article><img src="https://example.com/shared-copy.jpg" /></article></body></html>
            """,
        }

        collector = NewsCollectionService(source_configs=[source])
        service = ArticleParseService(
            session_factory=self.session_factory,
            collector=collector,
            markdown_service=self.markdown_service,
        )
        with self._patch_http_session(
            text_payloads=pages,
            byte_payloads={
                "https://example.com/shared.jpg": _png_bytes((0, 0, 255)),
                "https://example.com/shared-copy.jpg": _png_bytes((0, 0, 255)),
            },
        ):
            first_result = asyncio.run(service.parse_articles(article_ids=["article-4"]))
        self.assertEqual(first_result.parsed, 1)

        with self.session_factory() as session:
            first_image = session.scalars(select(ArticleImage).where(ArticleImage.article_id == "article-4")).one()
            first_image.visual_status = "done"
            first_image.observed_description = "shared image"
            first_image.analysis_metadata_json = {"source": "reused"}
            session.commit()

        with self._patch_http_session(
            text_payloads=pages,
            byte_payloads={
                "https://example.com/shared.jpg": _png_bytes((0, 0, 255)),
                "https://example.com/shared-copy.jpg": _png_bytes((0, 0, 255)),
            },
        ):
            second_result = asyncio.run(service.parse_articles(article_ids=["article-5"]))
        self.assertEqual(second_result.parsed, 1)

        with self.session_factory() as session:
            second_image = session.scalars(select(ArticleImage).where(ArticleImage.article_id == "article-5")).one()

        self.assertEqual(second_image.visual_status, "done")
        self.assertEqual(second_image.observed_description, "shared image")
        self.assertEqual(second_image.analysis_metadata_json["source"], "reused")
        self.assertEqual(len(second_image.image_hash or ""), 16)


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
