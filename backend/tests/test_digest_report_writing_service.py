from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Article, Digest, PipelineRun, ensure_article_storage_schema
from backend.app.service.digest_packaging_service import ResolvedDigestPlan
from backend.app.service.digest_report_writing_service import DigestReportWritingService


def _build_fake_llm_client(raw_content: str) -> SimpleNamespace:
    async def create(**_: object) -> SimpleNamespace:
        return response

    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw_content))])
    completions = SimpleNamespace(create=create)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


def _build_fake_rate_limiter() -> SimpleNamespace:
    return SimpleNamespace(lease=lambda *_: nullcontext())


def _build_session(root_path: Path) -> Session:
    business_day = date(2026, 3, 30)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    session.add(PipelineRun(run_id="run-1", business_date=business_day))
    session.add_all(
        [
            Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-1",
                original_url="https://example.com/article-1",
                title_raw="Article 1",
                summary_raw="Summary 1",
                markdown_rel_path="2026/03/30/article-1.md",
            ),
            Article(
                article_id="article-2",
                source_name="WWD",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-2",
                original_url="https://example.com/article-2",
                title_raw="Article 2",
                summary_raw="Summary 2",
                markdown_rel_path="2026/03/30/article-2.md",
            ),
        ]
    )
    (root_path / "2026/03/30").mkdir(parents=True, exist_ok=True)
    (root_path / "2026/03/30/article-1.md").write_text("# Article 1\n\nBody 1\n", encoding="utf-8")
    (root_path / "2026/03/30/article-2.md").write_text("# Article 2\n\nBody 2\n", encoding="utf-8")
    session.commit()
    return session


class DigestReportWritingServiceTest(unittest.TestCase):
    def test_write_digest_returns_digest_shaped_object_from_resolved_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _build_session(Path(tmp_dir))
            self.addCleanup(session.close)
            service = DigestReportWritingService(
                client=_build_fake_llm_client(
                    (
                        '{"title_zh":"本日品牌动作速写","dek_zh":"导语摘要",'
                        '"body_markdown":"# 正文\\n\\n聚合后的内容",'
                        '"source_article_ids":["article-2","article-1"]}'
                    )
                ),
                markdown_root=Path(tmp_dir),
                rate_limiter=_build_fake_rate_limiter(),
            )

            written = asyncio.run(
                service.write_digest(
                    session,
                    run_id="run-1",
                    plan=ResolvedDigestPlan(
                        business_date=date(2026, 3, 30),
                        facet="trend_summary",
                        story_keys=("story-1", "story-2"),
                        article_ids=("article-1", "article-2"),
                        editorial_angle="用品牌动作解释趋势变化",
                        title_zh="包装阶段标题",
                        dek_zh="包装阶段导语",
                        source_names=("Vogue", "WWD"),
                    ),
                )
            )

        self.assertIsInstance(written, Digest)
        self.assertEqual("trend_summary", written.facet)
        self.assertEqual("本日品牌动作速写", written.title_zh)
        self.assertEqual("导语摘要", written.dek_zh)
        self.assertEqual("# 正文\n\n聚合后的内容", written.body_markdown)
        self.assertEqual(["Vogue", "WWD"], written.source_names_json)
        self.assertEqual(2, written.source_article_count)
        self.assertEqual(
            ("article-2", "article-1"),
            written.selected_source_article_ids,
        )
