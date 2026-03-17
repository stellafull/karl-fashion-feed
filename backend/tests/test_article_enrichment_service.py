from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models.article import Article
from backend.app.schemas.llm.article_enrichment import ArticleEnrichmentSchema
from backend.app.service.article_enrichment_service import ArticleEnrichmentService
from backend.app.service.article_markdown_service import ArticleMarkdownService


class StubLLMClient:
    def __init__(self, result: ArticleEnrichmentSchema | Exception) -> None:
        self._result = result

    def complete_json(self, **_: object) -> ArticleEnrichmentSchema:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class ArticleEnrichmentServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def test_enrich_article_populates_story_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            markdown_service = ArticleMarkdownService(Path(tmp_dir))
            markdown_service.write_markdown(
                relative_path="2026-03-13/article-1.md",
                content="# Runway Story\n\nOriginal body\n",
            )
            service = ArticleEnrichmentService(
                llm_client=StubLLMClient(
                    ArticleEnrichmentSchema(
                        should_publish=True,
                        reject_reason="",
                        title_zh="秀场速递",
                        summary_zh="这是一篇关于秀场造型的报道。",
                        tags=["秀场", "巴黎"],
                        brands=["Chanel"],
                        category_candidates=["高端时装", "秀场/系列"],
                    )
                ),
                markdown_service=markdown_service,
            )

            with self.session_factory() as session:
                article = Article(
                    article_id="article-1",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/story",
                    original_url="https://example.com/story",
                    title_raw="Runway Story",
                    summary_raw="Raw summary",
                    markdown_rel_path="2026-03-13/article-1.md",
                    published_at=datetime(2026, 3, 13, 8, 0, 0),
                )
                session.add(article)
                session.commit()

                changed = service.enrich_article(session, article)
                session.commit()

                self.assertTrue(changed)
                self.assertEqual(article.enrichment_status, "done")
                self.assertTrue(article.should_publish)
                self.assertEqual(article.title_zh, "秀场速递")
                self.assertIn("Chanel", article.cluster_text or "")

    def test_enrich_article_marks_failed_on_llm_error(self) -> None:
        service = ArticleEnrichmentService(
            llm_client=StubLLMClient(RuntimeError("boom")),
            markdown_service=ArticleMarkdownService(Path(tempfile.gettempdir())),
        )

        with self.session_factory() as session:
            article = Article(
                article_id="article-2",
                source_name="WWD",
                source_type="rss",
                source_lang="en",
                category="行业动态",
                canonical_url="https://example.com/market",
                original_url="https://example.com/market",
                title_raw="Market Story",
                summary_raw="Summary",
            )
            session.add(article)
            session.commit()

            with self.assertRaises(RuntimeError):
                service.enrich_article(session, article)

            session.commit()
            self.assertEqual(article.enrichment_status, "failed")
            self.assertIn("boom", article.enrichment_error or "")

    def test_enrich_article_accepts_null_reject_reason_for_publishable_result(self) -> None:
        service = ArticleEnrichmentService(
            llm_client=StubLLMClient(
                ArticleEnrichmentSchema(
                    should_publish=True,
                    reject_reason=None,
                    title_zh="巴黎时装周观察",
                    summary_zh="聚焦本季巴黎时装周上的关键造型和品牌动向。",
                    tags=["巴黎时装周"],
                    brands=["Balenciaga"],
                    category_candidates=["秀场/系列"],
                )
            ),
            markdown_service=ArticleMarkdownService(Path(tempfile.gettempdir())),
        )

        with self.session_factory() as session:
            article = Article(
                article_id="article-3",
                source_name="The Zoe Report",
                source_type="rss",
                source_lang="en",
                category="秀场/系列",
                canonical_url="https://example.com/fashion-week",
                original_url="https://example.com/fashion-week",
                title_raw="Fashion Week Story",
                summary_raw="Summary",
            )
            session.add(article)
            session.commit()

            changed = service.enrich_article(session, article)
            session.commit()

            self.assertTrue(changed)
            self.assertEqual(article.enrichment_status, "done")
            self.assertTrue(article.should_publish)
            self.assertEqual(article.reject_reason, "")
            self.assertEqual(article.title_zh, "巴黎时装周观察")

    def test_enrich_article_overrides_legacy_market_fit_rejection(self) -> None:
        service = ArticleEnrichmentService(
            llm_client=StubLLMClient(
                ArticleEnrichmentSchema(
                    should_publish=False,
                    reject_reason="文章内容为购物推荐，不适合作为时尚资讯发布。",
                    title_zh="亚马逊平价显瘦穿搭",
                    summary_zh="亚马逊上的平价时尚单品和热销趋势。",
                    tags=["购物推荐", "亚马逊"],
                    brands=["Amazon"],
                    category_candidates=["时尚穿搭"],
                )
            ),
            markdown_service=ArticleMarkdownService(Path(tempfile.gettempdir())),
        )

        with self.session_factory() as session:
            article = Article(
                article_id="article-4",
                source_name="Elite Daily",
                source_type="rss",
                source_lang="en",
                category="时尚穿搭",
                canonical_url="https://example.com/amazon-fashion",
                original_url="https://example.com/amazon-fashion",
                title_raw="Amazon Fashion Trend",
                summary_raw="Shopping roundup",
            )
            session.add(article)
            session.commit()

            changed = service.enrich_article(session, article)
            session.commit()

            self.assertTrue(changed)
            self.assertEqual(article.enrichment_status, "done")
            self.assertTrue(article.should_publish)
            self.assertEqual(article.reject_reason, "")

if __name__ == "__main__":
    unittest.main()
