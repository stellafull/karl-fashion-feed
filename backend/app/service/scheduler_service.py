"""Daily scheduler and the single runtime story pipeline orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.database import Base, SessionLocal, engine
from backend.app.models import Article, PipelineRun, Story, StoryArticle, ensure_article_storage_schema
from backend.app.service.article_cluster_service import ArticleClusterService, EmbeddedArticle
from backend.app.service.article_collection_service import ArticleCollectionService, CollectionResult
from backend.app.service.article_enrichment_service import ArticleEnrichmentService, EnrichedArticle
from backend.app.service.article_parse_service import ArticleParseService, ParseResult
from backend.app.service.RAG.embedding_service import generate_article_summary_embedding
from backend.app.service.story_generation_service import StoryDraft, StoryGenerationService


BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_RUN_HOUR = 8
DEFAULT_RUN_MINUTE = 0

RUN_TYPE_DAILY_STORY = "daily_story"
STORY_GROUPING_INCREMENTAL = "incremental_ingested_at"
STAGE_COLLECTION = "collection"
STAGE_PARSE = "parse"
STAGE_ENRICHMENT = "enrichment"
STAGE_STORY_EMBEDDING = "story_embedding"
STAGE_SEMANTIC_CLUSTER = "semantic_cluster"
STAGE_CLUSTER_REVIEW = "cluster_review"
STAGE_STORY_GENERATION = "story_generation"
STAGE_STORY_PERSIST = "story_persist"
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
    publishable_records: tuple[EnrichedArticle, ...]
    watermark_ingested_at: datetime | None
    story_drafts: tuple[StoryDraft, ...]
    stages_completed: tuple[str, ...] = field(default_factory=tuple)
    stages_skipped: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DailyPipelineResult:
    run_id: str
    candidates: int
    enriched: int
    published: int
    stories_created: int
    watermark_ingested_at: datetime | None
    story_date: datetime | None = None
    story_grouping_mode: str = STORY_GROUPING_INCREMENTAL
    stages_completed: tuple[str, ...] = field(default_factory=tuple)
    stages_skipped: tuple[str, ...] = field(default_factory=tuple)
    skipped_existing_enrichment: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


class SchedulerService:
    """Schedule and run the daily story pipeline at a fixed Beijing local time."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        collection_service: ArticleCollectionService | None = None,
        parse_service: ArticleParseService | None = None,
        enrichment_service: ArticleEnrichmentService | None = None,
        embedding_service: Any | None = None,
        cluster_service: ArticleClusterService | None = None,
        story_generation_service: StoryGenerationService | None = None,
        now_factory: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
        timezone: ZoneInfo = BEIJING_TIMEZONE,
        run_hour: int = DEFAULT_RUN_HOUR,
        run_minute: int = DEFAULT_RUN_MINUTE,
    ) -> None:
        if not 0 <= run_hour <= 23:
            raise ValueError("run_hour must be between 0 and 23")
        if not 0 <= run_minute <= 59:
            raise ValueError("run_minute must be between 0 and 59")

        self._session_factory = session_factory
        self._collection_service = collection_service or ArticleCollectionService(
            session_factory=session_factory,
        )
        self._parse_service = parse_service or ArticleParseService(
            session_factory=session_factory,
        )
        self._enrichment_service = enrichment_service or ArticleEnrichmentService()
        self._embedding_service = embedding_service
        self._cluster_service = cluster_service or ArticleClusterService()
        self._story_generation_service = story_generation_service or StoryGenerationService()
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._sleep_func = sleep_func or asyncio.sleep
        self._timezone = timezone
        self._run_hour = run_hour
        self._run_minute = run_minute

    async def enrich_articles(self, article_ids: list[str]) -> tuple[int, int]:
        """Run enrichment for the provided article ids and persist results."""
        enriched_count = 0
        skipped_existing = 0
        with self._session_factory() as session:
            articles = session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()
            for article in articles:
                if (
                    article.enrichment_status == "done"
                    and bool((article.title_zh or "").strip())
                    and bool((article.summary_zh or "").strip())
                    and bool((article.cluster_text or "").strip())
                ):
                    skipped_existing += 1
                    continue
                try:
                    if await self._enrichment_service.enrich_article(session, article):
                        enriched_count += 1
                except Exception:
                    session.commit()
                    raise
            session.commit()
        return enriched_count, skipped_existing

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

    async def run_story_workflow(self, article_ids: list[str]) -> StoryWorkflowResult:
        """Run enrichment plus story draft generation for the given articles."""
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

    async def run_pipeline_once(
        self,
        *,
        skip_ingest: bool = False,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
    ) -> DailyPipelineResult:
        """Run one full daily pipeline cycle immediately."""
        ensure_article_storage_schema(engine)
        Base.metadata.create_all(bind=engine)

        run_id = self._create_run(
            skip_ingest=skip_ingest,
            source_names=source_names,
            limit_sources=limit_sources,
            story_grouping_mode=STORY_GROUPING_INCREMENTAL,
        )
        collection_result: CollectionResult | None = None
        parse_result: ParseResult | None = None
        stages_completed: list[str] = []
        stages_skipped: list[str] = []

        try:
            if skip_ingest:
                stages_skipped.append(STAGE_COLLECTION)
            else:
                collection_result = await self._collection_service.collect_articles(
                    source_names=source_names,
                    limit_sources=limit_sources,
                )
                stages_completed.append(STAGE_COLLECTION)
        except Exception as exc:
            self._mark_failed(run_id, exc)
            raise

        try:
            parse_result = await self._parse_service.parse_articles()
            if parse_result.candidates > 0:
                stages_completed.append(STAGE_PARSE)
            else:
                stages_skipped.append(STAGE_PARSE)

            with self._session_factory() as session:
                last_success_watermark = self._get_last_success_watermark(session, run_id=run_id)
                candidate_articles = session.scalars(self._candidate_query(last_success_watermark)).all()

            if not candidate_articles:
                stages_skipped.extend(PROCESSING_STAGES + (STAGE_STORY_PERSIST,))
                self._mark_success(
                    run_id,
                    watermark_ingested_at=last_success_watermark,
                    metadata={
                        "candidates": 0,
                        "enriched": 0,
                        "published": 0,
                        "stories_created": 0,
                        "collected": collection_result.inserted if collection_result else 0,
                        "parsed": parse_result.parsed if parse_result else 0,
                        "parse_failed": parse_result.failed if parse_result else 0,
                        "story_date": None,
                        "story_grouping_mode": STORY_GROUPING_INCREMENTAL,
                        "stages_completed": stages_completed,
                        "stages_skipped": stages_skipped,
                    },
                )
                return DailyPipelineResult(
                    run_id=run_id,
                    candidates=0,
                    enriched=0,
                    published=0,
                    stories_created=0,
                    watermark_ingested_at=last_success_watermark,
                    story_grouping_mode=STORY_GROUPING_INCREMENTAL,
                    stages_completed=tuple(),
                    stages_skipped=tuple(stages_skipped),
                )

            candidate_ids = [article.article_id for article in candidate_articles]
            workflow_result = await self.run_story_workflow(candidate_ids)
            stages_completed.extend(workflow_result.stages_completed)
            stages_skipped.extend(workflow_result.stages_skipped)
            if STAGE_STORY_PERSIST not in stages_completed:
                stages_completed.append(STAGE_STORY_PERSIST)

            self._persist_stories(
                run_id=run_id,
                story_drafts=list(workflow_result.story_drafts),
                watermark_ingested_at=workflow_result.watermark_ingested_at,
                metadata={
                    "candidates": len(candidate_ids),
                    "enriched": workflow_result.enriched_count,
                    "published": len(workflow_result.publishable_records),
                    "stories_created": len(workflow_result.story_drafts),
                    "collected": collection_result.inserted if collection_result else 0,
                    "parsed": parse_result.parsed if parse_result else 0,
                    "parse_failed": parse_result.failed if parse_result else 0,
                    "story_date": None,
                    "story_grouping_mode": STORY_GROUPING_INCREMENTAL,
                    "stages_completed": stages_completed,
                    "stages_skipped": stages_skipped,
                },
            )
            return DailyPipelineResult(
                run_id=run_id,
                candidates=len(candidate_ids),
                enriched=workflow_result.enriched_count,
                published=len(workflow_result.publishable_records),
                stories_created=len(workflow_result.story_drafts),
                watermark_ingested_at=workflow_result.watermark_ingested_at,
                story_grouping_mode=STORY_GROUPING_INCREMENTAL,
                stages_completed=tuple(stages_completed),
                stages_skipped=tuple(stages_skipped),
                skipped_existing_enrichment=workflow_result.skipped_existing_enrichment,
            )
        except Exception as exc:
            self._mark_failed(run_id, exc)
            raise

    def next_run_at(self, *, now: datetime | None = None) -> datetime:
        """Return the next scheduled Beijing-local run time."""
        current_time = self._normalize_now(now or self._now_factory()).astimezone(self._timezone)
        scheduled_time = current_time.replace(
            hour=self._run_hour,
            minute=self._run_minute,
            second=0,
            microsecond=0,
        )
        if current_time >= scheduled_time:
            scheduled_time += timedelta(days=1)
        return scheduled_time

    def seconds_until_next_run(self, *, now: datetime | None = None) -> float:
        """Return the positive sleep duration until the next scheduled run."""
        current_time = self._normalize_now(now or self._now_factory())
        next_run = self.next_run_at(now=current_time).astimezone(UTC)
        delay_seconds = (next_run - current_time.astimezone(UTC)).total_seconds()
        return max(delay_seconds, 0.0)

    async def run_forever(
        self,
        *,
        skip_ingest: bool = False,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
        max_cycles: int | None = None,
    ) -> None:
        """Sleep until the configured Beijing run time and trigger the pipeline forever."""
        if max_cycles is not None and max_cycles <= 0:
            raise ValueError("max_cycles must be greater than 0 when provided")

        completed_cycles = 0
        while max_cycles is None or completed_cycles < max_cycles:
            await self._sleep_func(self.seconds_until_next_run())
            await self.run_pipeline_once(
                skip_ingest=skip_ingest,
                source_names=source_names,
                limit_sources=limit_sources,
            )
            completed_cycles += 1

    def _embed_articles(self, records: list[EnrichedArticle]) -> list[EmbeddedArticle]:
        if self._embedding_service is not None:
            return list(self._embedding_service.embed_articles(records))

        embeddings: list[tuple[float, ...]] = []
        for record in records:
            cluster_text = record.cluster_text.strip()
            if not cluster_text:
                raise ValueError(f"cluster_text is required for story embedding: {record.article_id}")
            embedding = generate_article_summary_embedding(cluster_text)
            embeddings.append(tuple(float(value) for value in embedding))
        return [
            EmbeddedArticle(article=record, embedding=embedding)
            for record, embedding in zip(records, embeddings, strict=True)
        ]

    def _load_publishable_records(
        self,
        article_ids: list[str],
    ) -> tuple[list[EnrichedArticle], datetime | None]:
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

    def _create_run(
        self,
        *,
        skip_ingest: bool,
        source_names: list[str] | None,
        limit_sources: int | None,
        story_grouping_mode: str,
    ) -> str:
        with self._session_factory() as session:
            ensure_article_storage_schema(session.get_bind())
            run = PipelineRun(
                run_type=RUN_TYPE_DAILY_STORY,
                status="running",
                metadata_json={
                    "skip_ingest": skip_ingest,
                    "source_names": source_names or [],
                    "limit_sources": limit_sources,
                    "story_date": None,
                    "story_grouping_mode": story_grouping_mode,
                },
            )
            session.add(run)
            session.commit()
            return run.run_id

    def _candidate_query(self, watermark_ingested_at: datetime | None) -> Select[tuple[Article]]:
        query = (
            select(Article)
            .where(Article.parse_status == "done")
            .order_by(Article.ingested_at.asc(), Article.article_id.asc())
        )
        if watermark_ingested_at is None:
            return query
        return query.where(Article.ingested_at > watermark_ingested_at)

    def _get_last_success_watermark(self, session: Session, *, run_id: str) -> datetime | None:
        successful_runs = session.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.run_type == RUN_TYPE_DAILY_STORY,
                PipelineRun.status == "success",
                PipelineRun.run_id != run_id,
            )
            .order_by(PipelineRun.finished_at.desc(), PipelineRun.started_at.desc())
        ).all()
        for run in successful_runs:
            if run.watermark_ingested_at is not None:
                return run.watermark_ingested_at
        return None

    def _persist_stories(
        self,
        *,
        run_id: str,
        story_drafts: list[StoryDraft],
        watermark_ingested_at: datetime | None,
        metadata: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise ValueError(f"pipeline run not found: {run_id}")

            for draft in story_drafts:
                story = Story(
                    created_run_id=run_id,
                    title_zh=draft.title_zh,
                    summary_zh=draft.summary_zh,
                    key_points_json=list(draft.key_points),
                    tags_json=list(draft.tags),
                    category=draft.category,
                    hero_image_url=draft.hero_image_url,
                    source_article_count=draft.source_article_count,
                )
                session.add(story)
                session.flush()

                for rank, article_id in enumerate(draft.article_ids, start=1):
                    session.add(StoryArticle(story_key=story.story_key, article_id=article_id, rank=rank))

            run.status = "success"
            run.finished_at = _utcnow_naive()
            run.watermark_ingested_at = watermark_ingested_at
            run.metadata_json = metadata
            run.error_message = None
            session.commit()

    def _mark_success(
        self,
        run_id: str,
        *,
        watermark_ingested_at: datetime | None,
        metadata: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise ValueError(f"pipeline run not found: {run_id}")
            run.status = "success"
            run.finished_at = _utcnow_naive()
            run.watermark_ingested_at = watermark_ingested_at
            run.metadata_json = metadata
            run.error_message = None
            session.commit()

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        with self._session_factory() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                return
            run.status = "failed"
            run.finished_at = _utcnow_naive()
            run.error_message = f"{exc.__class__.__name__}: {exc}"
            session.commit()

    def _normalize_now(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
