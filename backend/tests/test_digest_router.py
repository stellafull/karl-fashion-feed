from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.models import Article, Digest, DigestArticle, PipelineRun, ensure_article_storage_schema
from backend.app.router.digest_router import build_digest_detail_response


class DigestRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ensure_article_storage_schema(self.engine)
        self.session = Session(self.engine)
        self.session.add(
            PipelineRun(
                run_id="run-1",
                business_date=date(2026, 3, 30),
                run_type="digest_daily",
                status="done",
                story_status="done",
                digest_status="done",
                metadata_json={},
            )
        )
        self.session.add(
            Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/canonical-1",
                original_url="https://example.com/original-1?ref=tracking",
                title_raw="Canonical Link Should Be Exposed",
                summary_raw="summary",
                markdown_rel_path="articles/article-1.md",
            )
        )
        self.session.add(
            Digest(
                digest_key="digest-1",
                business_date=date(2026, 3, 30),
                facet="runway_series",
                title_zh="标题",
                dek_zh="导语",
                body_markdown="正文",
                source_article_count=1,
                source_names_json=["Vogue"],
                created_run_id="run-1",
                generation_status="done",
            )
        )
        self.session.add(
            DigestArticle(
                digest_key="digest-1",
                article_id="article-1",
                rank=0,
            )
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_build_digest_detail_response_prefers_canonical_source_link(self) -> None:
        payload = build_digest_detail_response(self.session, digest_key="digest-1")

        self.assertEqual(1, len(payload.sources))
        self.assertEqual("https://example.com/canonical-1", payload.sources[0].link)
