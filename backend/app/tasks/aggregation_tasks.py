"""Business-day aggregation Celery tasks for stories and digests."""

from __future__ import annotations

from collections import Counter
import asyncio
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import ensure_article_storage_schema
from backend.app.models.digest import Digest
from backend.app.models.runtime import (
    BATCH_STAGE_MAX_ATTEMPTS,
    PipelineRun,
    _utcnow_naive,
)
from backend.app.models.story import Story
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.story_clustering_service import StoryClusteringService
from backend.app.tasks.celery_app import celery_app

_RUNTIME_FAILURE_SUMMARY_KEYS = frozenset(
    {
        "sources",
        "parse",
        "event_frame",
        "story",
        "digest",
    }
)


@celery_app.task(name="aggregation.cluster_stories_for_day")
def cluster_stories_for_day(business_day_iso: str, run_id: str, ownership_token: int) -> None:
    """Cluster stories for one business day and update pipeline_run state."""
    business_day = date.fromisoformat(business_day_iso)
    service = StoryClusteringService()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        _claim_batch_stage(
            session=session,
            run_id=run_id,
            business_day=business_day,
            stage="story",
            ownership_token=ownership_token,
        )

        try:
            asyncio.run(service.cluster_business_day(session, business_day, run_id=run_id))
            _finalize_batch_stage_success(
                session=session,
                run_id=run_id,
                business_day=business_day,
                stage="story",
                ownership_token=ownership_token,
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            _finalize_batch_stage_failure(
                session=session,
                run_id=run_id,
                business_day=business_day,
                stage="story",
                ownership_token=ownership_token,
                exc=exc,
            )
            raise


@celery_app.task(name="aggregation.generate_digests_for_day")
def generate_digests_for_day(business_day_iso: str, run_id: str, ownership_token: int) -> None:
    """Generate digests for one business day and update pipeline_run state."""
    business_day = date.fromisoformat(business_day_iso)
    service = DigestGenerationService()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        _claim_batch_stage(
            session=session,
            run_id=run_id,
            business_day=business_day,
            stage="digest",
            ownership_token=ownership_token,
        )

        try:
            asyncio.run(service.generate_for_day(session, business_day, run_id=run_id))
            _finalize_batch_stage_success(
                session=session,
                run_id=run_id,
                business_day=business_day,
                stage="digest",
                ownership_token=ownership_token,
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            _finalize_batch_stage_failure(
                session=session,
                run_id=run_id,
                business_day=business_day,
                stage="digest",
                ownership_token=ownership_token,
                exc=exc,
            )
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


def _claim_batch_stage(
    *,
    session: Session,
    run_id: str,
    business_day: date,
    stage: str,
    ownership_token: int,
) -> None:
    _load_run(session=session, run_id=run_id, business_day=business_day)
    status_field = getattr(PipelineRun, f"{stage}_status")
    updated_at_field = getattr(PipelineRun, f"{stage}_updated_at")
    token_field = getattr(PipelineRun, f"{stage}_token")
    claim_result = session.execute(
        update(PipelineRun)
        .where(
            PipelineRun.run_id == run_id,
            PipelineRun.business_date == business_day,
            token_field == ownership_token,
            status_field == "queued",
        )
        .values(
            {
                f"{stage}_status": "running",
                f"{stage}_updated_at": _utcnow_naive(),
            }
        )
    )
    if claim_result.rowcount != 1:
        session.rollback()
        raise RuntimeError(
            f"batch stage ownership lost before start: {stage} run={run_id} token={ownership_token}"
        )
    session.commit()


def _finalize_batch_stage_success(
    *,
    session: Session,
    run_id: str,
    business_day: date,
    stage: str,
    ownership_token: int,
) -> None:
    if stage == "digest":
        _assert_non_empty_final_digest_set(session=session, run_id=run_id, business_day=business_day)
    values = {
        f"{stage}_status": "done",
        f"{stage}_error": None,
        f"{stage}_updated_at": _utcnow_naive(),
    }
    if stage == "digest":
        values["status"] = "done"
        values["finished_at"] = _utcnow_naive()

    complete_result = session.execute(
        update(PipelineRun)
        .where(
            PipelineRun.run_id == run_id,
            PipelineRun.business_date == business_day,
            getattr(PipelineRun, f"{stage}_token") == ownership_token,
            getattr(PipelineRun, f"{stage}_status") == "running",
        )
        .values(values)
    )
    if complete_result.rowcount != 1:
        raise RuntimeError(
            f"batch stage ownership lost before finalize: {stage} run={run_id} token={ownership_token}"
        )
    session.flush()
    run = _load_run(session=session, run_id=run_id, business_day=business_day)
    session.refresh(run)
    _merge_batch_metadata(run)


def _assert_non_empty_final_digest_set(
    *,
    session: Session,
    run_id: str,
    business_day: date,
) -> None:
    story_count = len(
        session.execute(
            select(Story.story_key).where(
                Story.business_date == business_day,
                Story.created_run_id == run_id,
            )
        ).all()
    )
    if story_count == 0:
        return
    digest_count = len(
        session.execute(
            select(Digest.digest_key).where(
                Digest.business_date == business_day,
                Digest.created_run_id == run_id,
            )
        ).all()
    )
    if digest_count == 0:
        raise RuntimeError(
            "unexpectedly empty final digest set: "
            f"run_id={run_id} business_day={business_day.isoformat()} "
            f"story_count={story_count} digest_count={digest_count}"
        )


def _finalize_batch_stage_failure(
    *,
    session: Session,
    run_id: str,
    business_day: date,
    stage: str,
    ownership_token: int,
    exc: Exception,
) -> None:
    run = _load_run(session=session, run_id=run_id, business_day=business_day)
    if getattr(run, f"{stage}_token") != ownership_token or getattr(run, f"{stage}_status") != "running":
        raise RuntimeError(
            f"batch stage ownership lost before failure finalize: {stage} run={run_id} token={ownership_token}"
        )

    attempts_field = f"{stage}_attempts"
    status_field = f"{stage}_status"
    error_field = f"{stage}_error"
    updated_at_field = f"{stage}_updated_at"
    attempts = int(getattr(run, attempts_field)) + 1
    setattr(run, attempts_field, attempts)
    setattr(run, status_field, "abandoned" if attempts >= BATCH_STAGE_MAX_ATTEMPTS else "failed")
    setattr(run, error_field, f"{exc.__class__.__name__}: {exc}")
    setattr(run, updated_at_field, _utcnow_naive())
    if stage == "story":
        run.status = "failed" if run.story_status == "abandoned" else "running"
        run.finished_at = _utcnow_naive() if run.story_status == "abandoned" else None
    if stage == "digest":
        run.status = "failed" if run.digest_status == "abandoned" else "running"
        run.finished_at = _utcnow_naive() if run.digest_status == "abandoned" else None
    _merge_batch_metadata(run)
    session.commit()


def _merge_batch_metadata(run: PipelineRun) -> None:
    metadata_json = dict(run.metadata_json or {})
    existing_failure_summary = _normalize_failure_summary_keys(metadata_json.get("failure_summary"))
    metadata_json["batch_status_counts"] = dict(
        sorted(Counter((run.story_status, run.digest_status)).items())
    )
    metadata_json["batch_stage_summary"] = {
        "story": {
            "status": run.story_status,
            "attempts": run.story_attempts,
            "error": run.story_error,
        },
        "digest": {
            "status": run.digest_status,
            "attempts": run.digest_attempts,
            "error": run.digest_error,
        },
    }
    metadata_json["failure_summary"] = {
        **existing_failure_summary,
        "story": run.story_error,
        "digest": run.digest_error,
    }
    run.metadata_json = metadata_json


def _normalize_failure_summary_keys(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload[key]
        for key in _RUNTIME_FAILURE_SUMMARY_KEYS
        if key in payload
    }
