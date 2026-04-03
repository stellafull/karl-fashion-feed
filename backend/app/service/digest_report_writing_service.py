"""Business-day digest report writing service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import Configuration
from backend.app.models import Article, Digest, Story
from backend.app.prompts.digest_report_writing_prompt import build_digest_report_writing_prompt
from backend.app.schemas.llm.digest_report_writing import DigestReportWritingSchema
from backend.app.service.article_parse_service import ArticleMarkdownService
from backend.app.service.digest_packaging_service import ResolvedDigestPlan
from backend.app.service.langchain_model_factory import StructuredOutputRunnable, build_story_model
from backend.app.service.llm_debug_artifact_service import (
    LlmDebugArtifactRecorder,
    build_llm_debug_artifact_recorder_from_env,
)
from backend.app.service.llm_rate_limiter import LlmRateLimiter


@dataclass(frozen=True)
class _ArticleSourceInput:
    article_id: str
    source_name: str
    title_raw: str
    summary_raw: str
    body_markdown: str


@dataclass(frozen=True)
class _StorySummaryInput:
    story_key: str
    synopsis_zh: str
    event_type: str


class DigestReportWritingService:
    """Write digest long-form content from packaged plans and selected article sources."""

    def __init__(
        self,
        *,
        agent: Any | None = None,
        configuration: Configuration | None = None,
        markdown_root: Path | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        artifact_recorder: LlmDebugArtifactRecorder | None = None,
    ) -> None:
        self._agent = agent
        self._configuration = configuration or Configuration.from_runnable_config()
        self._markdown_service = ArticleMarkdownService(markdown_root)
        self._rate_limiter = rate_limiter or LlmRateLimiter()
        self._artifact_recorder = artifact_recorder or build_llm_debug_artifact_recorder_from_env()

    async def write_digest(
        self,
        session: Session,
        plan: ResolvedDigestPlan,
        *,
        run_id: str,
    ) -> Digest:
        story_summaries = self._load_story_summaries(session, plan.business_date, plan.story_keys)
        article_sources = self._load_article_sources(session, plan.article_ids)
        schema = await self._write_report(plan, story_summaries, article_sources, run_id=run_id)
        return self._resolve_written_digest(
            run_id=run_id,
            plan=plan,
            article_sources=article_sources,
            schema=schema,
        )

    def _load_story_summaries(
        self,
        session: Session,
        business_date: date,
        story_keys: tuple[str, ...],
    ) -> list[_StorySummaryInput]:
        rows = list(
            session.scalars(
                select(Story)
                .where(Story.story_key.in_(story_keys))
                .order_by(Story.story_key.asc())
            ).all()
        )
        story_by_key = {row.story_key: row for row in rows}
        missing_story_keys = [story_key for story_key in story_keys if story_key not in story_by_key]
        if missing_story_keys:
            joined = ", ".join(missing_story_keys)
            raise ValueError(f"missing story summary rows for digest report writing: {joined}")
        loaded: list[_StorySummaryInput] = []
        for story_key in story_keys:
            story = story_by_key[story_key]
            if story.business_date != business_date:
                raise ValueError(
                    "story summary business_date mismatch for digest report writing: "
                    f"{story_key} expected {business_date.isoformat()} got {story.business_date.isoformat()}"
                )
            synopsis_zh = story.synopsis_zh.strip()
            if not synopsis_zh:
                raise ValueError(
                    "story summary synopsis_zh cannot be blank for digest report writing: "
                    f"{story_key}"
                )
            loaded.append(
                _StorySummaryInput(
                    story_key=story_key,
                    synopsis_zh=synopsis_zh,
                    event_type=story.event_type.strip() or "general",
                )
            )
        return loaded

    def _load_article_sources(
        self,
        session: Session,
        article_ids: tuple[str, ...],
    ) -> list[_ArticleSourceInput]:
        rows = list(
            session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.article_id.asc())
            ).all()
        )
        article_by_id = {row.article_id: row for row in rows}
        missing_article_ids = [article_id for article_id in article_ids if article_id not in article_by_id]
        if missing_article_ids:
            joined = ", ".join(missing_article_ids)
            raise ValueError(f"missing article source rows for digest report writing: {joined}")

        loaded: list[_ArticleSourceInput] = []
        for article_id in article_ids:
            article = article_by_id[article_id]
            if not article.markdown_rel_path:
                raise ValueError(f"markdown_rel_path is required for digest report writing: {article_id}")
            loaded.append(
                _ArticleSourceInput(
                    article_id=article.article_id,
                    source_name=article.source_name,
                    title_raw=article.title_raw,
                    summary_raw=article.summary_raw,
                    body_markdown=self._markdown_service.read_markdown(
                        relative_path=article.markdown_rel_path
                    ),
                )
            )
        return loaded

    async def _write_report(
        self,
        plan: ResolvedDigestPlan,
        story_summaries: list[_StorySummaryInput],
        article_sources: list[_ArticleSourceInput],
        *,
        run_id: str,
    ) -> DigestReportWritingSchema:
        agent = self._get_agent()
        system_prompt = build_digest_report_writing_prompt()
        user_message = self._build_user_message(plan, story_summaries, article_sources)
        invoke_payload = {
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }
        prompt_payload = {
            "model": self._configuration.story_summarization_model,
            "system_prompt": system_prompt,
            "invoke_payload": invoke_payload,
        }
        with self._rate_limiter.lease("digest_report_writing"):
            result = await agent.ainvoke(invoke_payload)
        structured_response = result["structured_response"]
        if self._artifact_recorder.enabled:
            self._artifact_recorder.record(
                run_id=run_id,
                stage="digest_report_writing",
                object_key=self._artifact_object_key(plan),
                prompt_text=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
                response_text=json.dumps(
                    {"structured_response": self._jsonable_structured_response(structured_response)},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        if isinstance(structured_response, DigestReportWritingSchema):
            return structured_response
        return DigestReportWritingSchema.model_validate(structured_response)

    def _artifact_object_key(self, plan: ResolvedDigestPlan) -> str:
        story_segment = "-".join(plan.story_keys) if plan.story_keys else "none"
        article_segment = "-".join(plan.article_ids) if plan.article_ids else "none"
        return f"facet-{plan.facet}-stories-{story_segment}-articles-{article_segment}"

    def _resolve_written_digest(
        self,
        *,
        run_id: str,
        plan: ResolvedDigestPlan,
        article_sources: list[_ArticleSourceInput],
        schema: DigestReportWritingSchema,
    ) -> Digest:
        title_zh = schema.title_zh.strip()
        if not title_zh:
            raise ValueError("digest report writing title_zh cannot be blank")
        dek_zh = schema.dek_zh.strip()
        if not dek_zh:
            raise ValueError("digest report writing dek_zh cannot be blank")
        body_markdown = schema.body_markdown.strip()
        if not body_markdown:
            raise ValueError("digest report writing body_markdown cannot be blank")

        requested_article_ids = [article_id.strip() for article_id in schema.source_article_ids]
        if any(not article_id for article_id in requested_article_ids):
            raise ValueError("digest report writing source_article_ids contains blank value")
        if len(set(requested_article_ids)) != len(requested_article_ids):
            raise ValueError("digest report writing source_article_ids contains duplicates")

        allowed_article_ids = set(plan.article_ids)
        unknown_article_ids = sorted(article_id for article_id in requested_article_ids if article_id not in allowed_article_ids)
        if unknown_article_ids:
            joined = ", ".join(unknown_article_ids)
            raise ValueError(f"digest report writing unknown source_article_ids: {joined}")

        source_name_by_article = {
            article.article_id: article.source_name for article in article_sources
        }
        source_names = sorted({source_name_by_article[article_id] for article_id in requested_article_ids})
        digest = Digest(
            business_date=plan.business_date,
            facet=plan.facet,
            title_zh=title_zh,
            dek_zh=dek_zh,
            body_markdown=body_markdown,
            source_article_count=len(requested_article_ids),
            source_names_json=source_names,
            created_run_id=run_id,
            generation_status="done",
            generation_error=None,
        )
        digest.selected_source_article_ids = tuple(requested_article_ids)
        return digest

    def _build_user_message(
        self,
        plan: ResolvedDigestPlan,
        story_summaries: list[_StorySummaryInput],
        article_sources: list[_ArticleSourceInput],
    ) -> str:
        payload = {
            "plan": {
                "business_date": plan.business_date.isoformat(),
                "facet": plan.facet,
                "story_keys": list(plan.story_keys),
                "article_ids": list(plan.article_ids),
                "editorial_angle": plan.editorial_angle,
                "source_names": list(plan.source_names),
            },
            "story_summaries": [
                {
                    "story_key": story.story_key,
                    "synopsis_zh": story.synopsis_zh,
                    "event_type": story.event_type,
                }
                for story in story_summaries
            ],
            "source_articles": [
                {
                    "article_id": article.article_id,
                    "source_name": article.source_name,
                    "title_raw": article.title_raw,
                    "summary_raw": article.summary_raw,
                    "body_markdown": article.body_markdown,
                }
                for article in article_sources
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _get_agent(self):
        if self._agent is None:
            self._agent = StructuredOutputRunnable(
                model=build_story_model(self._configuration),
                schema=DigestReportWritingSchema,
                system_prompt=build_digest_report_writing_prompt(),
            )
        return self._agent

    def _jsonable_structured_response(self, structured_response: object) -> object:
        if isinstance(structured_response, DigestReportWritingSchema):
            return structured_response.model_dump(mode="json")
        if isinstance(structured_response, dict):
            return structured_response
        return str(structured_response)


__all__ = ["DigestReportWritingService"]
