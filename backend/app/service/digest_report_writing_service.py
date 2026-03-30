"""Business-day digest report writing service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.models import Article, Digest
from backend.app.prompts.digest_report_writing_prompt import build_digest_report_writing_prompt
from backend.app.schemas.llm.digest_report_writing import DigestReportWritingSchema
from backend.app.service.article_parse_service import ArticleMarkdownService
from backend.app.service.digest_packaging_service import _ResolvedPlan
from backend.app.service.llm_rate_limiter import LlmRateLimiter

if TYPE_CHECKING:
    from openai import AsyncOpenAI


@dataclass(frozen=True)
class _ArticleSourceInput:
    article_id: str
    source_name: str
    title_raw: str
    summary_raw: str
    body_markdown: str


@dataclass(frozen=True)
class _WrittenDigest:
    digest: Digest
    story_keys: tuple[str, ...]
    article_ids: tuple[str, ...]


class DigestReportWritingService:
    """Write digest long-form content from packaged plans and selected article sources."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        markdown_root: Path | None = None,
        rate_limiter: LlmRateLimiter | None = None,
    ) -> None:
        self._client = client
        self._markdown_service = ArticleMarkdownService(markdown_root)
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    async def write_digests(
        self,
        session: Session,
        business_day: date,
        *,
        run_id: str,
        plans: list[_ResolvedPlan],
    ) -> list[_WrittenDigest]:
        written: list[_WrittenDigest] = []
        for plan in plans:
            written.append(
                await self.write_digest(
                    session,
                    business_day=business_day,
                    run_id=run_id,
                    plan=plan,
                )
            )
        return written

    async def write_digest(
        self,
        session: Session,
        *,
        business_day: date,
        run_id: str,
        plan: _ResolvedPlan,
    ) -> _WrittenDigest:
        article_sources = self._load_article_sources(session, plan.article_ids)
        schema = await self._write_report(plan, article_sources)
        return self._resolve_written_digest(
            business_day=business_day,
            run_id=run_id,
            plan=plan,
            article_sources=article_sources,
            schema=schema,
        )

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
        plan: _ResolvedPlan,
        article_sources: list[_ArticleSourceInput],
    ) -> DigestReportWritingSchema:
        client = self._get_client()
        with self._rate_limiter.lease("digest_report_writing"):
            response = await client.chat.completions.create(
                model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                temperature=0,
                messages=[
                    {"role": "system", "content": build_digest_report_writing_prompt()},
                    {"role": "user", "content": self._build_user_message(plan, article_sources)},
                ],
                response_format={"type": "json_object"},
            )
        raw_content = response.choices[0].message.content or "{}"
        return DigestReportWritingSchema.model_validate_json(raw_content)

    def _resolve_written_digest(
        self,
        *,
        business_day: date,
        run_id: str,
        plan: _ResolvedPlan,
        article_sources: list[_ArticleSourceInput],
        schema: DigestReportWritingSchema,
    ) -> _WrittenDigest:
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
            business_date=business_day,
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
        return _WrittenDigest(
            digest=digest,
            story_keys=plan.story_keys,
            article_ids=tuple(requested_article_ids),
        )

    def _build_user_message(
        self,
        plan: _ResolvedPlan,
        article_sources: list[_ArticleSourceInput],
    ) -> str:
        payload = {
            "plan": {
                "facet": plan.facet,
                "story_keys": list(plan.story_keys),
                "article_ids": list(plan.article_ids),
                "editorial_angle": plan.editorial_angle,
                "title_zh": plan.title_zh,
                "dek_zh": plan.dek_zh,
                "source_names": list(plan.source_names),
            },
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

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = STORY_SUMMARIZATION_MODEL_CONFIG.api_key
            if not api_key:
                raise RuntimeError("digest report writing requires configured API key")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
                timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
            )
        return self._client
