"""Celery task package."""

from backend.app.tasks.aggregation_tasks import cluster_stories_for_day, generate_digests_for_day
from backend.app.tasks.celery_app import celery_app
from backend.app.tasks.content_tasks import collect_source, extract_event_frames, parse_article
from backend.app.tasks.scheduler_tasks import tick_daily_pipeline

__all__ = [
    "celery_app",
    "collect_source",
    "parse_article",
    "extract_event_frames",
    "cluster_stories_for_day",
    "generate_digests_for_day",
    "tick_daily_pipeline",
]
