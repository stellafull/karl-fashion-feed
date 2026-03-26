"""Daily scheduler and the single runtime story pipeline orchestration."""

from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.database import Base, SessionLocal, engine
from backend.app.models import Article, ArticleImage, PipelineRun, ensure_article_storage_schema
from backend.app.models.story import Story, StoryArticle
from backend.app.service.article_cluster_service import ArticleClusterService, EmbeddedArticle
from backend.app.service.article_collection_service import ArticleCollectionService, CollectionResult
from backend.app.service.article_enrichment_service import ArticleEnrichmentService, EnrichedArticle
from backend.app.service.article_parse_service import ArticleParseService, ParseResult
from backend.app.schemas.llm.story_taxonomy import ALLOWED_STORY_CATEGORIES, StoryCategory
from backend.app.service.image_analysis_service import ImageAnalysisService
from backend.app.service.RAG.article_rag_service import ArticleRagService, RagInsertResult
from backend.app.service.RAG.embedding_service import generate_article_summary_embeddings
from backend.app.service.story_generation_service import (
    CategoryScopedCluster,
    StoryDraft,
    StoryGenerationService,
)


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
STAGE_IMAGE_ANALYSIS = "image_analysis"
STAGE_RAG_INGEST = "rag_ingest"
STAGE_STORY_PERSIST = "story_persist"
ENRICHMENT_CONCURRENCY = 10
IMAGE_ANALYSIS_CONCURRENCY = 5
STORY_DRAFT_STAGES = (
    STAGE_STORY_EMBEDDING,
    STAGE_SEMANTIC_CLUSTER,
    STAGE_CLUSTER_REVIEW,
    STAGE_STORY_GENERATION,
)
POST_DRAFT_STAGES = (STAGE_IMAGE_ANALYSIS, STAGE_RAG_INGEST)
WORKFLOW_STAGES = (STAGE_ENRICHMENT, *STORY_DRAFT_STAGES, *POST_DRAFT_STAGES)


class SchedulerService:
    """Schedule and run the daily story pipeline at a fixed Beijing local time."""

    def __init__(self, *, cluster_distance_threshold: float | None = None) -> None:
        self._collection_service = ArticleCollectionService()
        self._parse_service = ArticleParseService()
        self._enrichment_service = ArticleEnrichmentService()
        self._embedding_service = None
        self._cluster_service = ArticleClusterService(
            distance_threshold=cluster_distance_threshold
        )
        self._story_generation_service = StoryGenerationService()
        self._image_analysis_service = ImageAnalysisService()
        self._article_rag_service = ArticleRagService()

    async def enrich_articles(self, article_ids: list[str]) -> tuple[int, int]:
        """Run enrichment for the provided article ids and persist results."""
        with SessionLocal() as session:
            articles = session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()
        pending_article_ids: list[str] = []
        skipped_existing = 0
        for article in articles:
            if (
                article.enrichment_status == "done"
                and bool((article.title_zh or "").strip())
                and bool((article.summary_zh or "").strip())
                and bool((article.cluster_text or "").strip())
                and self._enrichment_service.has_valid_categories(article)
            ):
                skipped_existing += 1
                continue
            if article.enrichment_status == "abandoned":
                skipped_existing += 1
                continue
            pending_article_ids.append(article.article_id)

        if not pending_article_ids:
            return 0, skipped_existing

        semaphore = asyncio.Semaphore(ENRICHMENT_CONCURRENCY)

        async def enrich_one(article_id: str) -> bool:
            async with semaphore:
                with SessionLocal() as worker_session:
                    article = worker_session.get(Article, article_id)
                    if article is None:
                        return False
                    enriched = await self._enrichment_service.enrich_article(
                        worker_session,
                        article,
                    )
                    worker_session.commit()
                    return enriched

        results = await asyncio.gather(*(enrich_one(article_id) for article_id in pending_article_ids))
        enriched_count = sum(1 for result in results if result)
        return enriched_count, skipped_existing

    async def analyze_article_images(self, publishable_article_ids: list[str]) -> tuple[int, int]:
        """Analyze images belonging to publishable articles with bounded concurrency."""
        if not publishable_article_ids:
            return 0, 0

        with SessionLocal() as session:
            image_ids = session.scalars(
                select(ArticleImage.image_id)
                .join(Article, Article.article_id == ArticleImage.article_id)
                .where(
                    Article.article_id.in_(publishable_article_ids),
                    Article.should_publish.is_(True),
                    Article.enrichment_status == "done",
                )
                .order_by(
                    Article.ingested_at.asc(),
                    Article.article_id.asc(),
                    ArticleImage.position.asc(),
                    ArticleImage.image_id.asc(),
                )
            ).all()

        semaphore = asyncio.Semaphore(IMAGE_ANALYSIS_CONCURRENCY)

        async def analyze_one(image_id: str) -> bool:
            async with semaphore:
                with SessionLocal() as worker_session:
                    image = worker_session.get(ArticleImage, image_id)
                    if image is None:
                        return False
                    article = worker_session.get(Article, image.article_id)
                    if article is None:
                        return False
                    analyzed = await self._image_analysis_service.analyze_image(
                        worker_session,
                        article=article,
                        image=image,
                    )
                    worker_session.commit()
                    return analyzed

        results = await asyncio.gather(*(analyze_one(image_id) for image_id in image_ids))
        analyzed_count = sum(1 for result in results if result)
        skipped_existing = len(results) - analyzed_count
        return analyzed_count, skipped_existing

    def ingest_articles_to_rag(self, article_ids: list[str]) -> RagInsertResult:
        """Upsert publishable article retrieval units into Qdrant."""
        return self._article_rag_service.upsert_articles(article_ids)

    async def build_story_drafts(
        self,
        article_ids: list[str],
    ) -> tuple[tuple[EnrichedArticle, ...], datetime | None, tuple[StoryDraft, ...]]:
        """Build story drafts from already-enriched publishable articles."""
        publishable_records, watermark = self._load_publishable_records(article_ids)
        if not publishable_records:
            return tuple(), watermark, tuple()

        try:
            embedded_articles = self._embed_articles(publishable_records)
        except Exception as exc:
            exc.add_note(
                f"stage=story_embedding publishable_records={len(publishable_records)}"
            )
            raise

        try:
            category_scoped_clusters = await self._cluster_publishable_articles(
                embedded_articles
            )
        except Exception as exc:
            exc.add_note(
                f"stage=semantic_cluster publishable_records={len(publishable_records)}"
            )
            raise

        try:
            story_drafts = await self._story_generation_service.generate_stories(
                category_scoped_clusters
            )
        except Exception as exc:
            exc.add_note(
                f"stage=story_generation clusters={len(category_scoped_clusters)}"
            )
            raise
        return tuple(publishable_records), watermark, tuple(story_drafts)

    async def run_story_workflow(self, article_ids: list[str]) -> dict[str, Any]:
        """Run enrichment plus story draft generation for the given articles."""
        enriched_count, skipped_existing = await self.enrich_articles(article_ids)
        publishable_records, watermark_ingested_at, story_drafts = await self.build_story_drafts(article_ids)
        if not publishable_records:
            return {
                "enriched_count": enriched_count,
                "skipped_existing_enrichment": skipped_existing,
                "publishable_records": tuple(),
                "watermark_ingested_at": watermark_ingested_at,
                "story_drafts": tuple(),
                "stages_completed": (STAGE_ENRICHMENT,),
                "stages_skipped": STORY_DRAFT_STAGES,
            }
        return {
            "enriched_count": enriched_count,
            "skipped_existing_enrichment": skipped_existing,
            "publishable_records": publishable_records,
            "watermark_ingested_at": watermark_ingested_at,
            "story_drafts": story_drafts,
            "stages_completed": (STAGE_ENRICHMENT, *STORY_DRAFT_STAGES),
            "stages_skipped": tuple(),
        }

    async def run_post_story_workflow(
        self,
        publishable_article_ids: list[str],
    ) -> dict[str, Any]:
        """Run post-story stages that should not block story persistence."""
        if not publishable_article_ids:
            return {
                "analyzed_images": 0,
                "skipped_existing_image_analysis": 0,
                "rag_result": RagInsertResult(
                    publishable_articles=0,
                    text_units=0,
                    image_units=0,
                    upserted_units=0,
                ),
                "stages_completed": tuple(),
                "stages_skipped": POST_DRAFT_STAGES,
            }

        analyzed_images, skipped_existing_image_analysis = await self.analyze_article_images(
            publishable_article_ids
        )
        rag_result = self.ingest_articles_to_rag(publishable_article_ids)
        return {
            "analyzed_images": analyzed_images,
            "skipped_existing_image_analysis": skipped_existing_image_analysis,
            "rag_result": rag_result,
            "stages_completed": POST_DRAFT_STAGES,
            "stages_skipped": tuple(),
        }

    async def run_pipeline_once(
        self,
        *,
        skip_ingest: bool = False,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
    ) -> dict[str, Any]:
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

            parse_result = await self._parse_service.parse_articles()
            if parse_result.candidates > 0:
                stages_completed.append(STAGE_PARSE)
            else:
                stages_skipped.append(STAGE_PARSE)

            with SessionLocal() as session:
                last_success_watermark = self._get_last_success_watermark(session, run_id=run_id)
                candidate_articles = session.scalars(self._candidate_query(last_success_watermark)).all()

            if not candidate_articles:
                stages_skipped.extend(WORKFLOW_STAGES + (STAGE_STORY_PERSIST,))
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
                        "image_analysis": {
                            "analyzed": 0,
                            "skipped_existing": 0,
                        },
                        "rag_ingest": {
                            "publishable_articles": 0,
                            "text_units": 0,
                            "image_units": 0,
                            "upserted_units": 0,
                        },
                        "story_date": None,
                        "story_grouping_mode": STORY_GROUPING_INCREMENTAL,
                        "stages_completed": stages_completed,
                        "stages_skipped": stages_skipped,
                    },
                )
                return {
                    "run_id": run_id,
                    "candidates": 0,
                    "enriched": 0,
                    "published": 0,
                    "stories_created": 0,
                    "watermark_ingested_at": last_success_watermark,
                    "story_grouping_mode": STORY_GROUPING_INCREMENTAL,
                    "stages_completed": tuple(stages_completed),
                    "stages_skipped": tuple(stages_skipped),
                    "skipped_existing_enrichment": 0,
                    "analyzed_images": 0,
                    "skipped_existing_image_analysis": 0,
                    "rag_upserted_units": 0,
                }

            candidate_ids = [article.article_id for article in candidate_articles]
            workflow_result = await self.run_story_workflow(candidate_ids)
            stages_completed.extend(workflow_result["stages_completed"])
            stages_skipped.extend(workflow_result["stages_skipped"])
            if STAGE_STORY_PERSIST not in stages_completed:
                stages_completed.append(STAGE_STORY_PERSIST)

            self._persist_story_rows(
                run_id=run_id,
                story_drafts=list(workflow_result["story_drafts"]),
            )

            publishable_article_ids = [
                record.article_id for record in workflow_result["publishable_records"]
            ]
            post_result = await self.run_post_story_workflow(publishable_article_ids)
            stages_completed.extend(post_result["stages_completed"])
            stages_skipped.extend(post_result["stages_skipped"])

            self._mark_success(
                run_id,
                watermark_ingested_at=workflow_result["watermark_ingested_at"],
                metadata={
                    "candidates": len(candidate_ids),
                    "enriched": workflow_result["enriched_count"],
                    "published": len(workflow_result["publishable_records"]),
                    "stories_created": len(workflow_result["story_drafts"]),
                    "collected": collection_result.inserted if collection_result else 0,
                    "parsed": parse_result.parsed if parse_result else 0,
                    "parse_failed": parse_result.failed if parse_result else 0,
                    "image_analysis": {
                        "analyzed": post_result["analyzed_images"],
                        "skipped_existing": post_result["skipped_existing_image_analysis"],
                    },
                    "rag_ingest": {
                        "publishable_articles": post_result["rag_result"].publishable_articles,
                        "text_units": post_result["rag_result"].text_units,
                        "image_units": post_result["rag_result"].image_units,
                        "upserted_units": post_result["rag_result"].upserted_units,
                    },
                    "story_date": None,
                    "story_grouping_mode": STORY_GROUPING_INCREMENTAL,
                    "stages_completed": stages_completed,
                    "stages_skipped": stages_skipped,
                },
            )
            return {
                "run_id": run_id,
                "candidates": len(candidate_ids),
                "enriched": workflow_result["enriched_count"],
                "published": len(workflow_result["publishable_records"]),
                "stories_created": len(workflow_result["story_drafts"]),
                "watermark_ingested_at": workflow_result["watermark_ingested_at"],
                "story_grouping_mode": STORY_GROUPING_INCREMENTAL,
                "stages_completed": tuple(stages_completed),
                "stages_skipped": tuple(stages_skipped),
                "skipped_existing_enrichment": workflow_result["skipped_existing_enrichment"],
                "analyzed_images": post_result["analyzed_images"],
                "skipped_existing_image_analysis": post_result[
                    "skipped_existing_image_analysis"
                ],
                "rag_upserted_units": post_result["rag_result"].upserted_units,
            }
        except Exception as exc:
            self._mark_failed(run_id, exc)
            raise

    def next_run_at(self, *, now: datetime | None = None) -> datetime:
        """Return the next scheduled Beijing-local run time."""
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        else:
            current_time = current_time.astimezone(UTC)
        current_time = current_time.astimezone(BEIJING_TIMEZONE)
        scheduled_time = current_time.replace(
            hour=DEFAULT_RUN_HOUR,
            minute=DEFAULT_RUN_MINUTE,
            second=0,
            microsecond=0,
        )
        if current_time >= scheduled_time:
            scheduled_time += timedelta(days=1)
        return scheduled_time

    def seconds_until_next_run(self, *, now: datetime | None = None) -> float:
        """Return the positive sleep duration until the next scheduled run."""
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        else:
            current_time = current_time.astimezone(UTC)
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
            await asyncio.sleep(self.seconds_until_next_run())
            await self.run_pipeline_once(
                skip_ingest=skip_ingest,
                source_names=source_names,
                limit_sources=limit_sources,
            )
            completed_cycles += 1

    def _embed_articles(self, records: list[EnrichedArticle]) -> list[EmbeddedArticle]:
        if self._embedding_service is not None:
            return list(self._embedding_service.embed_articles(records))

        cluster_texts: list[str] = []
        for record in records:
            cluster_text = record.cluster_text.strip()
            if not cluster_text:
                raise ValueError(f"cluster_text is required for story embedding: {record.article_id}")
            cluster_texts.append(cluster_text)

        embeddings = [
            tuple(float(value) for value in embedding)
            for embedding in generate_article_summary_embeddings(cluster_texts)
        ]
        return [
            EmbeddedArticle(article=record, embedding=embedding)
            for record, embedding in zip(records, embeddings, strict=True)
        ]

    async def _cluster_publishable_articles(
        self,
        embedded_articles: list[EmbeddedArticle],
    ) -> list[CategoryScopedCluster]:
        category_buckets: dict[StoryCategory, list[EmbeddedArticle]] = {
            category: [] for category in ALLOWED_STORY_CATEGORIES
        }
        for embedded_article in embedded_articles:
            for category in embedded_article.article.categories:
                category_buckets[category].append(embedded_article)

        category_scoped_clusters: list[CategoryScopedCluster] = []
        for category in ALLOWED_STORY_CATEGORIES:
            bucket_articles = category_buckets[category]
            if not bucket_articles:
                continue
            clusters = await self._cluster_service.cluster_articles(
                bucket_articles,
                category=category,
            )
            category_scoped_clusters.extend(
                CategoryScopedCluster(
                    category=category,
                    articles=tuple(cluster),
                )
                for cluster in clusters
            )

        return category_scoped_clusters

    def _load_publishable_records(
        self,
        article_ids: list[str],
    ) -> tuple[list[EnrichedArticle], datetime | None]:
        with SessionLocal() as session:
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
        with SessionLocal() as session:
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
        self._persist_story_rows(
            run_id=run_id,
            story_drafts=story_drafts,
        )
        self._mark_success(
            run_id,
            watermark_ingested_at=watermark_ingested_at,
            metadata=metadata,
        )

    def _persist_story_rows(
        self,
        *,
        run_id: str,
        story_drafts: list[StoryDraft],
    ) -> None:
        with SessionLocal() as session:
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
            session.commit()

    def _mark_success(
        self,
        run_id: str,
        *,
        watermark_ingested_at: datetime | None,
        metadata: dict[str, Any],
    ) -> None:
        with SessionLocal() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise ValueError(f"pipeline run not found: {run_id}")
            run.status = "success"
            run.finished_at = datetime.now(UTC).replace(tzinfo=None)
            run.watermark_ingested_at = watermark_ingested_at
            run.metadata_json = metadata
            run.error_message = None
            session.commit()

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        with SessionLocal() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                return
            run.status = "failed"
            run.finished_at = datetime.now(UTC).replace(tzinfo=None)
            run.error_message = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).strip()
            session.commit()
