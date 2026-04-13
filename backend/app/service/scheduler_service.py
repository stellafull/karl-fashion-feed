"""Scheduler service — gates daily pipeline start to 07:00 Asia/Shanghai and handles RAG upsert."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models import Article, ensure_article_storage_schema
from backend.app.models.runtime import (
    ASIA_SHANGHAI,
    PipelineRun,
    business_day_for_runtime,
    utc_bounds_for_business_day,
)
from backend.app.service.daily_run_coordinator_service import DailyRunCoordinatorService, RUN_TYPE_DAILY_DIGEST
from backend.app.service.RAG.article_rag_service import ArticleRagService

PIPELINE_START_HOUR_SHANGHAI = 7

logger = logging.getLogger(__name__)


class SchedulerService:
    """Drive the daily pipeline on a scheduled cadence with RAG upsert."""

    def __init__(self) -> None:
        self._coordinator = DailyRunCoordinatorService()

    def tick(self) -> str | None:
        """Periodic entry point: gate new runs to 07:00 Asia/Shanghai, drive runs, upsert RAG.

        Returns the pipeline run_id when work was done, or None if skipped.
        """
        now = datetime.now(UTC)
        shanghai_now = now.astimezone(ASIA_SHANGHAI)
        business_day = business_day_for_runtime(now)

        with SessionLocal() as session:
            ensure_article_storage_schema(session.get_bind())
            existing_run = session.scalar(
                select(PipelineRun).where(
                    PipelineRun.business_date == business_day,
                    PipelineRun.run_type == RUN_TYPE_DAILY_DIGEST,
                )
            )

        # Don't create a new pipeline run before 07:00 Shanghai.
        if existing_run is None and shanghai_now.hour < PIPELINE_START_HOUR_SHANGHAI:
            logger.debug(
                "scheduler: skipping tick — before %02d:00 Asia/Shanghai (%s)",
                PIPELINE_START_HOUR_SHANGHAI,
                shanghai_now.strftime("%H:%M %Z"),
            )
            return None

        # Already terminal — only RAG might be pending
        if existing_run is not None and existing_run.status in {"done", "failed"}:
            if existing_run.status == "done":
                self._upsert_rag_if_ready(business_day)
            return existing_run.run_id

        run_id = self._coordinator.tick()
        logger.info("scheduler: coordinator tick completed, run_id=%s", run_id)

        self._upsert_rag_if_ready(business_day)
        return run_id

    def _upsert_rag_if_ready(self, business_day: date) -> None:
        """Upsert event-frame-ready articles to the vector store if the pipeline is done."""
        with SessionLocal() as session:
            ensure_article_storage_schema(session.get_bind())
            run = session.scalar(
                select(PipelineRun).where(
                    PipelineRun.business_date == business_day,
                    PipelineRun.run_type == RUN_TYPE_DAILY_DIGEST,
                    PipelineRun.status == "done",
                )
            )
            if run is None:
                return

            metadata = run.metadata_json or {}
            if metadata.get("rag_upserted"):
                return

            run_id = run.run_id

        window_start, window_end = utc_bounds_for_business_day(business_day)
        with SessionLocal() as session:
            ensure_article_storage_schema(session.get_bind())
            article_ids = [
                str(row[0])
                for row in session.execute(
                    select(Article.article_id).where(
                        Article.ingested_at >= window_start,
                        Article.ingested_at < window_end,
                        Article.event_frame_status == "done",
                    )
                ).all()
            ]

        if not article_ids:
            logger.warning("scheduler: no event-frame-ready articles for RAG on %s", business_day)
            return

        logger.info("scheduler: upserting %d articles to RAG for %s", len(article_ids), business_day)
        rag_result = ArticleRagService().upsert_articles(article_ids)
        logger.info(
            "scheduler: RAG upsert complete for %s — indexed=%d text=%d image=%d upserted=%d",
            business_day,
            rag_result.indexed_articles,
            rag_result.text_units,
            rag_result.image_units,
            rag_result.upserted_units,
        )

        with SessionLocal() as session:
            ensure_article_storage_schema(session.get_bind())
            run = session.get(PipelineRun, run_id)
            if run is not None:
                run.metadata_json = {
                    **(run.metadata_json or {}),
                    "rag_upserted": True,
                    "rag_indexed_articles": rag_result.indexed_articles,
                    "rag_upserted_units": rag_result.upserted_units,
                }
                session.commit()
