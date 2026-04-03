from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config.llm_config import Configuration
from backend.app.models import Article, Digest, PipelineRun, Story, ensure_article_storage_schema
from backend.app.prompts.digest_report_writing_prompt import build_digest_report_writing_prompt
from backend.app.schemas.llm.digest_report_writing import DigestReportWritingSchema
from backend.app.service.digest_packaging_service import ResolvedDigestPlan
from backend.app.service.digest_report_writing_service import DigestReportWritingService
from backend.app.service.llm_debug_artifact_service import LlmDebugArtifactRecorder


class _FakeAgent:
    def __init__(
        self,
        responses: list[DigestReportWritingSchema | dict[str, object]],
        *,
        call_log: list[dict[str, object]] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._call_log = call_log if call_log is not None else []
        self.invoke_calls = 0

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.invoke_calls += 1
        self._call_log.append(payload)
        if not self._responses:
            raise AssertionError("fake agent exhausted queued responses")
        return {"structured_response": self._responses.pop(0)}


class _FakeChatModel:
    def __init__(self, structured_model: _FakeAgent) -> None:
        self._structured_model = structured_model
        self.structured_output_calls: list[tuple[type[object], str]] = []

    def with_structured_output(self, schema: type[object], *, method: str):
        self.structured_output_calls.append((schema, method))
        return self._structured_model


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
            Story(
                story_key="story-1",
                business_date=business_day,
                event_type="runway_show",
                synopsis_zh="Acme 巴黎秀场",
                anchor_json={"brand": "Acme"},
                article_membership_json=["article-1"],
                created_run_id="run-1",
                clustering_status="done",
            ),
            Story(
                story_key="story-2",
                business_date=business_day,
                event_type="campaign_launch",
                synopsis_zh="Beta 发布新广告大片",
                anchor_json={"brand": "Beta"},
                article_membership_json=["article-2"],
                created_run_id="run-1",
                clustering_status="done",
            ),
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
    def test_get_agent_fails_fast_when_api_key_is_missing(self) -> None:
        service = DigestReportWritingService(
            configuration=Configuration(api_key=None),
            rate_limiter=_build_fake_rate_limiter(),
        )

        with self.assertRaisesRegex(RuntimeError, "configured API key"):
            service._get_agent()

    def test_write_digest_builds_structured_model_with_required_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _build_session(Path(tmp_dir))
            self.addCleanup(session.close)
            fake_agent = _FakeAgent(
                [
                    DigestReportWritingSchema.model_validate(
                        {
                            "title_zh": "本日品牌动作速写",
                            "dek_zh": "导语摘要",
                            "body_markdown": "# 正文\n\n聚合后的内容",
                            "source_article_ids": ["article-1"],
                        }
                    )
                ]
            )
            fake_model = _FakeChatModel(fake_agent)
            service = DigestReportWritingService(
                markdown_root=Path(tmp_dir),
                rate_limiter=_build_fake_rate_limiter(),
            )

            with patch(
                "backend.app.service.digest_report_writing_service.build_story_model",
                return_value=fake_model,
            ) as build_story_model_mock:
                digest = asyncio.run(
                    service.write_digest(
                        session,
                        run_id="run-1",
                        plan=ResolvedDigestPlan(
                            business_date=date(2026, 3, 30),
                            facet="trend_summary",
                            story_keys=("story-1", "story-2"),
                            article_ids=("article-1", "article-2"),
                            editorial_angle="用品牌动作解释趋势变化",
                            source_names=("Vogue", "WWD"),
                        ),
                    )
                )

        self.assertEqual("trend_summary", digest.facet)
        build_story_model_mock.assert_called_once_with(service._configuration)
        self.assertEqual(
            [(DigestReportWritingSchema, "json_schema")],
            fake_model.structured_output_calls,
        )

    def test_write_digest_returns_digest_shaped_object_from_resolved_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _build_session(Path(tmp_dir))
            self.addCleanup(session.close)
            call_log: list[dict[str, object]] = []
            service = DigestReportWritingService(
                agent=_FakeAgent(
                    [
                        DigestReportWritingSchema.model_validate(
                            {
                                "title_zh": "本日品牌动作速写",
                                "dek_zh": "导语摘要",
                                "body_markdown": "# 正文\n\n聚合后的内容",
                                "source_article_ids": ["article-2", "article-1"],
                            }
                        )
                    ],
                    call_log=call_log,
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
        self.assertEqual(1, len(call_log))
        payload = json.loads(call_log[0]["messages"][0]["content"])
        self.assertEqual(["story-1", "story-2"], payload["plan"]["story_keys"])
        self.assertEqual(["article-1", "article-2"], payload["plan"]["article_ids"])
        self.assertIn("Body 1", payload["source_articles"][0]["body_markdown"])

    def test_write_digest_sends_story_summaries_and_omits_packaging_title_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _build_session(Path(tmp_dir))
            self.addCleanup(session.close)
            call_log: list[dict[str, object]] = []
            service = DigestReportWritingService(
                agent=_FakeAgent(
                    [
                        DigestReportWritingSchema.model_validate(
                            {
                                "title_zh": "本日品牌动作速写",
                                "dek_zh": "导语摘要",
                                "body_markdown": "# 正文\n\n聚合后的内容",
                                "source_article_ids": ["article-2", "article-1"],
                            }
                        )
                    ],
                    call_log=call_log,
                ),
                markdown_root=Path(tmp_dir),
                rate_limiter=_build_fake_rate_limiter(),
            )

            asyncio.run(
                service.write_digest(
                    session,
                    run_id="run-1",
                    plan=ResolvedDigestPlan(
                        business_date=date(2026, 3, 30),
                        facet="trend_summary",
                        story_keys=("story-1", "story-2"),
                        article_ids=("article-1", "article-2"),
                        editorial_angle="用品牌动作解释趋势变化",
                        source_names=("Vogue", "WWD"),
                    ),
                )
            )

        payload = json.loads(call_log[0]["messages"][0]["content"])
        self.assertNotIn("title_zh", payload["plan"])
        self.assertNotIn("dek_zh", payload["plan"])
        self.assertEqual(
            [
                {"story_key": "story-1", "synopsis_zh": "Acme 巴黎秀场", "event_type": "runway_show"},
                {"story_key": "story-2", "synopsis_zh": "Beta 发布新广告大片", "event_type": "campaign_launch"},
            ],
            payload["story_summaries"],
        )

    def test_write_digest_fails_when_story_summary_rows_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _build_session(Path(tmp_dir))
            self.addCleanup(session.close)
            session.query(Story).delete()
            session.commit()
            service = DigestReportWritingService(
                agent=_FakeAgent([]),
                markdown_root=Path(tmp_dir),
                rate_limiter=_build_fake_rate_limiter(),
            )

            with self.assertRaisesRegex(ValueError, "missing story summary rows"):
                asyncio.run(
                    service.write_digest(
                        session,
                        run_id="run-1",
                        plan=ResolvedDigestPlan(
                            business_date=date(2026, 3, 30),
                            facet="trend_summary",
                            story_keys=("story-1",),
                            article_ids=("article-1",),
                            editorial_angle="用品牌动作解释趋势变化",
                            source_names=("Vogue",),
                        ),
                    )
                )

    def test_write_digest_records_unique_artifacts_for_same_facet_plans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            session = _build_session(root)
            self.addCleanup(session.close)
            service = DigestReportWritingService(
                agent=_FakeAgent(
                    [
                        DigestReportWritingSchema.model_validate(
                            {
                                "title_zh": "本日品牌动作速写",
                                "dek_zh": "导语摘要",
                                "body_markdown": "# 正文\n\n聚合后的内容",
                                "source_article_ids": ["article-2", "article-1"],
                            }
                        ),
                        DigestReportWritingSchema.model_validate(
                            {
                                "title_zh": "本日品牌动作速写",
                                "dek_zh": "导语摘要",
                                "body_markdown": "# 正文\n\n聚合后的内容",
                                "source_article_ids": ["article-2", "article-1"],
                            }
                        ),
                    ]
                ),
                markdown_root=root,
                rate_limiter=_build_fake_rate_limiter(),
                artifact_recorder=LlmDebugArtifactRecorder(
                    base_dir=root / "llm-artifacts",
                    enabled=True,
                ),
            )
            common_plan = {
                "business_date": date(2026, 3, 30),
                "facet": "trend_summary",
                "article_ids": ("article-1", "article-2"),
                "editorial_angle": "用品牌动作解释趋势变化",
                "source_names": ("Vogue", "WWD"),
            }

            asyncio.run(
                service.write_digest(
                    session,
                    run_id="run-1",
                    plan=ResolvedDigestPlan(
                        story_keys=("story-1",),
                        **common_plan,
                    ),
                )
            )
            asyncio.run(
                service.write_digest(
                    session,
                    run_id="run-1",
                    plan=ResolvedDigestPlan(
                        story_keys=("story-2",),
                        **common_plan,
                    ),
                )
            )

            stage_dir = root / "llm-artifacts" / "run-1" / "digest_report_writing"
            prompt_files = sorted(stage_dir.glob("*/prompt.json"))
            response_files = sorted(stage_dir.glob("*/response.json"))
            self.assertEqual(2, len(prompt_files))
            self.assertEqual(2, len(response_files))
            prompt_payload = json.loads(prompt_files[0].read_text(encoding="utf-8"))
            response_payload = json.loads(response_files[0].read_text(encoding="utf-8"))
            self.assertIn("system_prompt", prompt_payload)
            self.assertIn("invoke_payload", prompt_payload)
            self.assertEqual("user", prompt_payload["invoke_payload"]["messages"][0]["role"])
            self.assertIn("structured_response", response_payload)
            self.assertEqual("本日品牌动作速写", response_payload["structured_response"]["title_zh"])
