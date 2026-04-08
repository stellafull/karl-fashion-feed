from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from contextlib import nullcontext
from datetime import date
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    Article,
    ArticleEventFrame,
    Digest,
    DigestArticle,
    DigestStory,
    PipelineRun,
    Story,
    ensure_article_storage_schema,
)
from backend.app.schemas.llm.digest_packaging import DigestPackagingSchema
from backend.app.schemas.llm.digest_report_writing import DigestReportWritingSchema
from backend.app.schemas.llm.facet_assignment import FacetAssignmentSchema
from backend.app.schemas.llm.story_cluster_judgment import StoryClusterJudgmentSchema
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.digest_packaging_service import DigestPackagingService
from backend.app.service.digest_report_writing_service import DigestReportWritingService
from backend.app.service.story_facet_assignment_service import StoryFacetAssignmentService
from backend.app.service.story_clustering_service import StoryClusteringService


class _FakeStructuredResponseAgent:
    def __init__(
        self,
        *,
        responder: Callable[[dict[str, object]], object],
        call_log: list[dict[str, object]],
    ) -> None:
        self._responder = responder
        self._call_log = call_log

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self._call_log.append(payload)
        return {"structured_response": self._responder(payload)}


def _build_fake_story_cluster_agent(call_log: list[dict[str, object]]) -> _FakeStructuredResponseAgent:
    def responder(_: dict[str, object]) -> StoryClusterJudgmentSchema:
        return StoryClusterJudgmentSchema.model_validate(
            {
                "groups": [
                    {
                        "seed_event_frame_id": "frame-1",
                        "member_event_frame_ids": ["frame-1"],
                        "synopsis_zh": "Acme 同日秀场事件",
                        "event_type": "runway_show",
                        "anchor_json": {"brand": "Acme"},
                    }
                ]
            }
        )

    return _FakeStructuredResponseAgent(responder=responder, call_log=call_log)


def _build_fake_facet_assignment_agent(call_log: list[dict[str, object]]) -> _FakeStructuredResponseAgent:
    def responder(payload: dict[str, object]) -> FacetAssignmentSchema:
        messages = payload.get("messages")
        assert isinstance(messages, list)
        user_payload = json.loads(messages[0]["content"])
        stories = user_payload.get("stories", [])
        response_payload = {
            "stories": [
                {
                    "story_key": story["story_key"],
                    "facets": ["runway_series"],
                }
                for story in stories
            ]
        }
        return FacetAssignmentSchema.model_validate(response_payload)

    return _FakeStructuredResponseAgent(responder=responder, call_log=call_log)


def _build_fake_digest_packaging_agent(call_log: list[dict[str, object]]) -> _FakeStructuredResponseAgent:
    def responder(payload: dict[str, object]) -> DigestPackagingSchema:
        messages = payload.get("messages")
        assert isinstance(messages, list)
        user_payload = json.loads(messages[0]["content"])
        facet = str(user_payload["facet"])
        stories = user_payload.get("stories", [])
        story_keys: list[str] = []
        article_ids: list[str] = []
        for story in stories:
            story_key = str(story["story_key"])
            if story_key not in story_keys:
                story_keys.append(story_key)
            for article_id in story.get("article_ids", []):
                normalized_article_id = str(article_id)
                if normalized_article_id not in article_ids:
                    article_ids.append(normalized_article_id)

        response_payload = {
            "digests": [
                {
                    "facet": facet,
                    "story_keys": story_keys,
                    "article_ids": article_ids,
                    "editorial_angle": "同日主事件聚焦",
                    "title_zh": "同日事件摘要",
                    "dek_zh": "聚焦当日单一主事件",
                }
            ]
        }
        return DigestPackagingSchema.model_validate(response_payload)

    return _FakeStructuredResponseAgent(responder=responder, call_log=call_log)


def _build_fake_digest_report_writing_agent(call_log: list[dict[str, object]]) -> _FakeStructuredResponseAgent:
    def responder(payload: dict[str, object]) -> DigestReportWritingSchema:
        messages = payload.get("messages")
        assert isinstance(messages, list)
        user_payload = json.loads(messages[0]["content"])
        plan = user_payload["plan"]
        article_ids = [str(article_id) for article_id in plan["article_ids"]]
        return DigestReportWritingSchema.model_validate(
            {
                "title_zh": "同日时尚摘要",
                "dek_zh": "覆盖当日核心事件",
                "body_markdown": "## 长文正文\n\nAcme 在巴黎发布 FW26 系列并触发同日事件聚合。",
                "source_article_ids": article_ids,
            }
        )

    return _FakeStructuredResponseAgent(responder=responder, call_log=call_log)


def _build_fake_rate_limiter() -> SimpleNamespace:
    return SimpleNamespace(lease=lambda *_: nullcontext())


def _build_session() -> Session:
    business_day = date(2026, 3, 30)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    session.add(PipelineRun(run_id="run-1", business_date=business_day))
    session.add(
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
        )
    )
    session.add(
        ArticleEventFrame(
            event_frame_id="frame-1",
            article_id="article-1",
            business_date=business_day,
            event_type="runway_show",
            subject_json={"brand": "Acme", "person": "Jane"},
            action_text="发布",
            object_text="新品",
            place_text="Paris",
            collection_text="FW26",
            season_text="FW26",
            show_context_text="",
            evidence_json=[{"quote": "Acme FW26"}],
            signature_json={"brand": "Acme"},
            extraction_confidence=0.95,
            extraction_status="done",
            extraction_error=None,
        )
    )
    session.commit()
    return session


class StoryDigestRuntimeIntegrationTest(unittest.TestCase):
    def test_same_day_runtime_clusters_stories_then_generates_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _build_session()
            self.addCleanup(session.close)
            business_day = date(2026, 3, 30)
            run_id = "run-1"
            markdown_root = Path(tmp_dir)
            (markdown_root / "2026/03/30").mkdir(parents=True, exist_ok=True)
            (markdown_root / "2026/03/30/article-1.md").write_text(
                "# Article 1\n\nAcme released FW26 in Paris.\n",
                encoding="utf-8",
            )

            cluster_call_log: list[dict[str, object]] = []
            facet_call_log: list[dict[str, object]] = []
            packaging_call_log: list[dict[str, object]] = []
            report_call_log: list[dict[str, object]] = []

            stories = asyncio.run(
                StoryClusteringService(
                    agent=_build_fake_story_cluster_agent(cluster_call_log),
                    rate_limiter=_build_fake_rate_limiter(),
                ).cluster_business_day(
                    session,
                    business_day,
                    run_id=run_id,
                )
            )

            digests = asyncio.run(
                DigestGenerationService(
                    facet_assignment_service=StoryFacetAssignmentService(
                        agent=_build_fake_facet_assignment_agent(facet_call_log),
                        rate_limiter=_build_fake_rate_limiter(),
                    ),
                    packaging_service=DigestPackagingService(
                        agent=_build_fake_digest_packaging_agent(packaging_call_log),
                        rate_limiter=_build_fake_rate_limiter(),
                    ),
                    report_writing_service=DigestReportWritingService(
                        agent=_build_fake_digest_report_writing_agent(report_call_log),
                        markdown_root=markdown_root,
                        rate_limiter=_build_fake_rate_limiter(),
                    ),
                ).generate_for_day(
                    session,
                    business_day,
                    run_id=run_id,
                )
            )

        self.assertEqual(1, len(stories))
        self.assertEqual(1, len(digests))
        story_key = stories[0].story_key
        self.assertEqual(1, len(cluster_call_log))
        self.assertEqual(1, len(facet_call_log))
        self.assertEqual(1, len(packaging_call_log))
        self.assertEqual(1, len(report_call_log))
        facet_request = json.loads(facet_call_log[0]["messages"][0]["content"])
        self.assertEqual([story_key], [story["story_key"] for story in facet_request["stories"]])
        packaging_request = json.loads(packaging_call_log[0]["messages"][0]["content"])
        self.assertEqual("runway_series", packaging_request["facet"])
        self.assertEqual(["runway_series"], packaging_request["stories"][0]["facets"])
        self.assertEqual(["article-1"], packaging_request["stories"][0]["article_ids"])
        self.assertEqual(story_key, packaging_request["stories"][0]["story_key"])
        report_request = json.loads(report_call_log[0]["messages"][0]["content"])
        self.assertEqual([story_key], report_request["plan"]["story_keys"])
        self.assertEqual(["article-1"], report_request["plan"]["article_ids"])
        self.assertIn("Acme released FW26 in Paris.", report_request["source_articles"][0]["body_markdown"])
        persisted_stories = session.scalars(select(Story).where(Story.business_date == business_day)).all()
        self.assertEqual(1, len(persisted_stories))
        persisted_digests = session.scalars(select(Digest).where(Digest.business_date == business_day)).all()
        self.assertEqual(1, len(persisted_digests))
        self.assertIn("长文正文", persisted_digests[0].body_markdown)
        self.assertGreater(len(persisted_digests[0].body_markdown.strip()), 20)

        digest_story_rows = session.execute(select(DigestStory.story_key)).all()
        digest_article_rows = session.execute(select(DigestArticle.article_id)).all()
        self.assertEqual(1, len(digest_story_rows))
        self.assertEqual(1, len(digest_article_rows))

        project_root = Path(__file__).resolve().parents[1]
        self.assertFalse((project_root / "app/service/strict_story_packing_service.py").exists())
        self.assertFalse((project_root / "app/prompts/strict_story_tiebreak_prompt.py").exists())
        self.assertFalse((project_root / "app/schemas/llm/strict_story_tiebreak.py").exists())

    def test_merge_batch_metadata_normalizes_failure_summary_to_current_runtime_keys(self) -> None:
        from backend.app.tasks.aggregation_tasks import _merge_batch_metadata

        run = PipelineRun(
            run_id="run-meta",
            business_date=date(2026, 3, 30),
            story_status="failed",
            story_error="story failed",
            digest_status="failed",
            digest_error="digest failed",
            metadata_json={
                "failure_summary": {
                    "strict_story": "legacy",
                    "sources": {"source-a": "source failed"},
                    "parse": "parse failed",
                    "event_frame": "event frame failed",
                    "legacy_extra": "legacy extra failed",
                }
            },
        )

        _merge_batch_metadata(run)

        self.assertEqual(
            {
                "sources": {"source-a": "source failed"},
                "parse": "parse failed",
                "event_frame": "event frame failed",
                "story": "story failed",
                "digest": "digest failed",
            },
            run.metadata_json["failure_summary"],
        )
