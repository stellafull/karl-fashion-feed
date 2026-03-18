"""Story workflow orchestration for enrichment, embedding, clustering, and draft generation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Article
from backend.app.service.RAG.embedding_service import generate_article_summary_embedding
from backend.app.service.article_cluster_service import ArticleClusterService
from backend.app.service.article_enrichment_service import ArticleEnrichmentService
from backend.app.service.story_generation_service import StoryGenerationService
from backend.app.service.story_pipeline_contracts import EmbeddedArticle, EnrichedArticleRecord, StoryDraft

STAGE_ENRICHMENT = "enrichment"
STAGE_STORY_EMBEDDING = "story_embedding"
STAGE_SEMANTIC_CLUSTER = "semantic_cluster"
STAGE_CLUSTER_REVIEW = "cluster_review"
STAGE_STORY_GENERATION = "story_generation"
PROCESSING_STAGES = (
    STAGE_ENRICHMENT,
    STAGE_STORY_EMBEDDING,
    STAGE_SEMANTIC_CLUSTER,
    STAGE_CLUSTER_REVIEW,
    STAGE_STORY_GENERATION,
)


@dataclass(frozen=True)
class StoryWorkflowResult:
    enriched_count: int
    skipped_existing_enrichment: int
    publishable_records: tuple[EnrichedArticleRecord, ...]
    watermark_ingested_at: datetime | None
    story_drafts: tuple[StoryDraft, ...]
    stages_completed: tuple[str, ...] = field(default_factory=tuple)
    stages_skipped: tuple[str, ...] = field(default_factory=tuple)


class StoryWorkflowService:
    """Run the LLM-driven story workflow on already-ingested articles."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        enrichment_service: ArticleEnrichmentService | None = None,
        embedding_service: Any | None = None,
        cluster_service: ArticleClusterService | None = None,
        story_generation_service: StoryGenerationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._enrichment_service = enrichment_service or ArticleEnrichmentService()
        self._embedding_service = embedding_service
        self._cluster_service = cluster_service or ArticleClusterService()
        self._story_generation_service = story_generation_service or StoryGenerationService()

    async def run(
        self,
        article_ids: list[str],
    ) -> StoryWorkflowResult:
        enriched_count, skipped_existing = await self.enrich_articles(article_ids)
        build_result = await self.build_story_drafts(article_ids)
        if not build_result.publishable_records:
            return StoryWorkflowResult(
                enriched_count=enriched_count,
                skipped_existing_enrichment=skipped_existing,
                publishable_records=tuple(),
                watermark_ingested_at=build_result.watermark_ingested_at,
                story_drafts=tuple(),
                stages_completed=(STAGE_ENRICHMENT,),
                stages_skipped=PROCESSING_STAGES[1:],
            )

        return StoryWorkflowResult(
            enriched_count=enriched_count,
            skipped_existing_enrichment=skipped_existing,
            publishable_records=build_result.publishable_records,
            watermark_ingested_at=build_result.watermark_ingested_at,
            story_drafts=build_result.story_drafts,
            stages_completed=(STAGE_ENRICHMENT, *build_result.stages_completed),
            stages_skipped=build_result.stages_skipped,
        )

    async def enrich_articles(self, article_ids: list[str]) -> tuple[int, int]:
        """Run enrichment for the provided article ids and persist results."""
        return await self._enrich_candidates(article_ids)

    async def build_story_drafts(self, article_ids: list[str]) -> StoryWorkflowResult:
        """Build story drafts from already-enriched publishable articles."""
        publishable_records, watermark = self._load_publishable_records(article_ids)
        if not publishable_records:
            return StoryWorkflowResult(
                enriched_count=0,
                skipped_existing_enrichment=0,
                publishable_records=tuple(),
                watermark_ingested_at=watermark,
                story_drafts=tuple(),
                stages_completed=tuple(),
                stages_skipped=PROCESSING_STAGES[1:],
            )

        embedded_articles = self._embed_articles(publishable_records)
        clusters = await self._cluster_service.cluster_articles(embedded_articles)
        story_drafts = await self._story_generation_service.generate_stories(clusters)

        return StoryWorkflowResult(
            enriched_count=0,
            skipped_existing_enrichment=0,
            publishable_records=tuple(publishable_records),
            watermark_ingested_at=watermark,
            story_drafts=tuple(story_drafts),
            stages_completed=PROCESSING_STAGES[1:],
            stages_skipped=tuple(),
        )

    def _embed_articles(
        self,
        records: list[EnrichedArticleRecord],
    ) -> list[EmbeddedArticle]:
        if self._embedding_service is not None:
            return list(self._embedding_service.embed_articles(records))

        if not records:
            return []

        embeddings = []
        for record in records:
            cluster_text = record.cluster_text.strip()
            if not cluster_text:
                raise ValueError(f"cluster_text is required for story embedding: {record.article_id}")
            embeddings.append(generate_article_summary_embedding(cluster_text))
        return [
            EmbeddedArticle(article=record, embedding=tuple(float(value) for value in embedding))
            for record, embedding in zip(records, embeddings, strict=True)
        ]

    async def _enrich_candidates(self, article_ids: list[str]) -> tuple[int, int]:
        with self._session_factory() as session:
            articles = session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()

        pending_articles: list[Article] = []
        skipped_existing = 0
        for article in articles:
            checker = getattr(self._enrichment_service, "is_complete", None)
            if callable(checker):
                if checker(article):
                    skipped_existing += 1
                    continue
            elif article.enrichment_status == "done" and bool((article.cluster_text or "").strip()):
                skipped_existing += 1
                continue
            pending_articles.append(article)

        if not pending_articles:
            return 0, skipped_existing

        payloads = [self._enrichment_service.build_input(article) for article in pending_articles]
        batch_results = await self._enrichment_service.infer_many(payloads)

        enriched_count = 0
        with self._session_factory() as session:
            for article, outcome in zip(pending_articles, batch_results, strict=True):
                stored = session.get(Article, article.article_id)
                if stored is None:
                    continue
                if isinstance(outcome, Exception):
                    self._enrichment_service.apply_failure(article=stored, error=outcome)
                    continue
                self._enrichment_service.apply_result(article=stored, result=outcome)
                enriched_count += 1
            session.commit()

        return enriched_count, skipped_existing

    def _load_publishable_records(
        self,
        article_ids: list[str],
    ) -> tuple[list[EnrichedArticleRecord], datetime | None]:
        with self._session_factory() as session:
            articles = session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()

        watermark = max((article.ingested_at for article in articles), default=None)
        publishable = [
            self._enrichment_service.to_record(article)
            for article in articles
            if article.should_publish is True and article.enrichment_status == "done"
        ]
        return publishable, watermark
