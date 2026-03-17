"""Story workflow orchestration for enrichment, embedding, clustering, and draft generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Article
from backend.app.service.article_cluster_service import ArticleClusterService
from backend.app.service.article_enrichment_service import ArticleEnrichmentService
from backend.app.service.embedding_service import StoryEmbeddingService
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
        embedding_service: StoryEmbeddingService | None = None,
        cluster_service: ArticleClusterService | None = None,
        story_generation_service: StoryGenerationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._enrichment_service = enrichment_service or ArticleEnrichmentService()
        self._embedding_service = embedding_service or StoryEmbeddingService()
        self._cluster_service = cluster_service or ArticleClusterService()
        self._story_generation_service = story_generation_service or StoryGenerationService()

    def run(
        self,
        article_ids: list[str],
    ) -> StoryWorkflowResult:
        enriched_count, skipped_existing = self._enrich_candidates(article_ids)
        publishable_records, watermark = self._load_publishable_records(article_ids)
        if not publishable_records:
            return StoryWorkflowResult(
                enriched_count=enriched_count,
                skipped_existing_enrichment=skipped_existing,
                publishable_records=tuple(),
                watermark_ingested_at=watermark,
                story_drafts=tuple(),
                stages_completed=(STAGE_ENRICHMENT,),
                stages_skipped=PROCESSING_STAGES[1:],
            )

        story_drafts = self._build_story_drafts(records=publishable_records)
        return StoryWorkflowResult(
            enriched_count=enriched_count,
            skipped_existing_enrichment=skipped_existing,
            publishable_records=tuple(publishable_records),
            watermark_ingested_at=watermark,
            story_drafts=tuple(story_drafts),
            stages_completed=PROCESSING_STAGES,
            stages_skipped=tuple(),
        )

    def _build_story_drafts(
        self,
        *,
        records: list[EnrichedArticleRecord],
    ) -> list[StoryDraft]:
        drafts: list[StoryDraft] = []
        embedded_articles = self._embedding_service.embed_articles(records)
        clusters = self._cluster_service.cluster_articles(embedded_articles)
        drafts.extend(self._generate_story_drafts(clusters))
        return drafts

    def _generate_story_drafts(self, clusters: list[list[EmbeddedArticle]]) -> list[StoryDraft]:
        if not clusters:
            return []

        if hasattr(self._story_generation_service, "generate_stories_batch"):
            return list(self._story_generation_service.generate_stories_batch(clusters))

        return [self._story_generation_service.generate_story(cluster) for cluster in clusters]

    def _enrich_candidates(self, article_ids: list[str]) -> tuple[int, int]:
        infer_batch = getattr(self._enrichment_service, "infer_batch", None)
        if callable(infer_batch):
            return self._enrich_candidates_batch(article_ids)

        enriched_count = 0
        skipped_existing = 0
        for article_id in article_ids:
            with self._session_factory() as session:
                article = session.get(Article, article_id)
                if article is None:
                    continue

                try:
                    changed = self._enrichment_service.enrich_article(session, article)
                    if changed:
                        enriched_count += 1
                    else:
                        skipped_existing += 1
                    session.commit()
                except Exception:
                    session.commit()
                    raise

        return enriched_count, skipped_existing

    def _enrich_candidates_batch(self, article_ids: list[str]) -> tuple[int, int]:
        with self._session_factory() as session:
            articles = session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()

        pending_articles: list[Article] = []
        skipped_existing = 0
        for article in articles:
            if self._article_enrichment_complete(article):
                skipped_existing += 1
                continue
            pending_articles.append(article)

        if not pending_articles:
            return 0, skipped_existing

        payloads = [self._enrichment_service.build_input(article) for article in pending_articles]
        batch_results = self._enrichment_service.infer_batch(payloads)

        enriched_count = 0
        with self._session_factory() as session:
            for article in pending_articles:
                stored = session.get(Article, article.article_id)
                if stored is None:
                    continue
                outcome = batch_results.get(article.article_id)
                if outcome is None or outcome.error or outcome.value is None:
                    error = RuntimeError(outcome.error if outcome is not None else "missing batch result")
                    self._enrichment_service.apply_failure(article=stored, error=error)
                    continue
                self._enrichment_service.apply_result(article=stored, result=outcome.value)
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

    def _article_enrichment_complete(self, article: Article) -> bool:
        checker = getattr(self._enrichment_service, "is_complete", None)
        if callable(checker):
            return bool(checker(article))
        return article.enrichment_status == "done" and bool((article.cluster_text or "").strip())
