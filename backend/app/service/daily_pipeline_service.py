"""Offline daily pipeline entrypoint: collection/parse bookkeeping plus story workflow persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Article, PipelineRun, Story, StoryArticle, ensure_article_storage_schema
from backend.app.service.article_collection_service import ArticleCollectionService, CollectionResult
from backend.app.service.article_cluster_service import ArticleClusterService
from backend.app.service.article_enrichment_service import ArticleEnrichmentService
from backend.app.service.article_parse_service import ArticleParseService, ParseResult
from backend.app.service.story_generation_service import StoryGenerationService
from backend.app.service.story_workflow_service import (
    PROCESSING_STAGES,
    StoryWorkflowService,
)
from backend.app.service.story_pipeline_contracts import (
    DailyPipelineResult,
    StoryDraft,
)

RUN_TYPE_DAILY_STORY = "daily_story"
STORY_GROUPING_INCREMENTAL = "incremental_ingested_at"
STAGE_COLLECTION = "collection"
STAGE_PARSE = "parse"
STAGE_STORY_PERSIST = "story_persist"
ALL_PROCESSING_STAGES = (STAGE_COLLECTION, STAGE_PARSE) + PROCESSING_STAGES + (STAGE_STORY_PERSIST,)


class DailyPipelineService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        collection_service: ArticleCollectionService | None = None,
        parse_service: ArticleParseService | None = None,
        story_workflow_service: StoryWorkflowService | None = None,
        enrichment_service: ArticleEnrichmentService | None = None,
        embedding_service: Any | None = None,
        cluster_service: ArticleClusterService | None = None,
        story_generation_service: StoryGenerationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._collection_service = collection_service or ArticleCollectionService(
            session_factory=session_factory,
        )
        self._parse_service = parse_service or ArticleParseService(
            session_factory=session_factory,
        )
        self._story_workflow_service = story_workflow_service or StoryWorkflowService(
            session_factory=session_factory,
            enrichment_service=enrichment_service,
            embedding_service=embedding_service,
            cluster_service=cluster_service,
            story_generation_service=story_generation_service,
        )

    async def run(
        self,
        *,
        skip_ingest: bool = False,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
    ) -> DailyPipelineResult:
        story_grouping_mode = STORY_GROUPING_INCREMENTAL
        run_id = self._create_run(
            skip_ingest=skip_ingest,
            source_names=source_names,
            limit_sources=limit_sources,
            story_grouping_mode=story_grouping_mode,
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
                        "story_grouping_mode": story_grouping_mode,
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
                    story_date=None,
                    story_grouping_mode=story_grouping_mode,
                    stages_completed=tuple(stages_completed),
                    stages_skipped=tuple(stages_skipped),
                )

            candidate_ids = [article.article_id for article in candidate_articles]
            workflow_result = await self._story_workflow_service.run(candidate_ids)
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
                    "story_grouping_mode": story_grouping_mode,
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
                story_date=None,
                story_grouping_mode=story_grouping_mode,
                stages_completed=tuple(stages_completed),
                stages_skipped=tuple(stages_skipped),
                skipped_existing_enrichment=workflow_result.skipped_existing_enrichment,
            )
        except Exception as exc:
            self._mark_failed(run_id, exc)
            raise

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

    def _candidate_query(
        self,
        watermark_ingested_at: datetime | None,
    ) -> Select[tuple[Article]]:
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
        metadata: dict,
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
                    session.add(
                        StoryArticle(
                            story_key=story.story_key,
                            article_id=article_id,
                            rank=rank,
                        )
                    )

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
        metadata: dict,
    ) -> None:
        with self._session_factory() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                return
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


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
