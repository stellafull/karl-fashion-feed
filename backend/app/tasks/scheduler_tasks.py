"""Celery Beat scheduled tasks for the daily pipeline."""

from __future__ import annotations

from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="scheduler.tick_daily_pipeline")
def tick_daily_pipeline() -> None:
    """Periodic tick: gate new runs to 07:00 Asia/Shanghai, drive runs, upsert RAG."""
    from backend.app.service.scheduler_service import SchedulerService

    SchedulerService().tick()
