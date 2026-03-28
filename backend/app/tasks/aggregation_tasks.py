"""Business-day aggregation Celery tasks for strict stories and digests."""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import ensure_article_storage_schema
from backend.app.models.runtime import (
    BATCH_STAGE_MAX_ATTEMPTS,
    PipelineRun,
    _utcnow_naive,
)
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.strict_story_packing_service import StrictStoryPackingService
from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="aggregation.pack_strict_stories_for_day")
def pack_strict_stories_for_day(business_day_iso: str, run_id: str) -> None:
    """Pack strict stories for one business day and update pipeline_run state."""
    business_day = date.fromisoformat(business_day_iso)
    service = StrictStoryPackingService()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = _load_run(session=session, run_id=run_id, business_day=business_day)
        run.strict_story_status = "running"
        run.strict_story_updated_at = _utcnow_naive()
        session.commit()

        try:
            asyncio.run(service.pack_business_day(session, business_day, run_id=run_id))
            run = _load_run(session=session, run_id=run_id, business_day=business_day)
            run.strict_story_status = "done"
            run.strict_story_error = None
            run.strict_story_updated_at = _utcnow_naive()
            session.commit()
        except Exception as exc:
            session.rollback()
            run = _load_run(session=session, run_id=run_id, business_day=business_day)
            run.strict_story_attempts += 1
            run.strict_story_status = (
                "abandoned" if run.strict_story_attempts >= BATCH_STAGE_MAX_ATTEMPTS else "failed"
            )
            run.strict_story_error = f"{exc.__class__.__name__}: {exc}"
            run.strict_story_updated_at = _utcnow_naive()
            session.commit()
            raise


@celery_app.task(name="aggregation.generate_digests_for_day")
def generate_digests_for_day(business_day_iso: str, run_id: str) -> None:
    """Generate digests for one business day and update pipeline_run state."""
    business_day = date.fromisoformat(business_day_iso)
    service = DigestGenerationService()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = _load_run(session=session, run_id=run_id, business_day=business_day)
        run.digest_status = "running"
        run.digest_updated_at = _utcnow_naive()
        session.commit()

        try:
            asyncio.run(service.generate_for_day(session, business_day, run_id=run_id))
            run = _load_run(session=session, run_id=run_id, business_day=business_day)
            run.digest_status = "done"
            run.digest_error = None
            run.digest_updated_at = _utcnow_naive()
            run.status = "done"
            run.finished_at = _utcnow_naive()
            session.commit()
        except Exception as exc:
            session.rollback()
            run = _load_run(session=session, run_id=run_id, business_day=business_day)
            run.digest_attempts += 1
            run.digest_status = "abandoned" if run.digest_attempts >= BATCH_STAGE_MAX_ATTEMPTS else "failed"
            run.digest_error = f"{exc.__class__.__name__}: {exc}"
            run.digest_updated_at = _utcnow_naive()
            run.status = "failed" if run.digest_status == "abandoned" else "running"
            session.commit()
            raise


def _load_run(*, session: Session, run_id: str, business_day: date) -> PipelineRun:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise RuntimeError(f"pipeline run not found for aggregation task: {run_id}")
    if run.business_date != business_day:
        raise RuntimeError(
            "pipeline run business day mismatch for aggregation task: "
            f"{run_id} expected {business_day.isoformat()} got {run.business_date.isoformat()}"
        )
    return run
