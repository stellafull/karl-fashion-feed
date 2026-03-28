"""Celery runtime configuration."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote


def build_celery_broker_url() -> str:
    """Build the Redis broker URL from environment variables."""
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "")
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


def build_celery_settings() -> dict[str, Any]:
    """Return the shared Celery settings for workers and eager tests."""
    broker_url = build_celery_broker_url()
    return {
        "broker_url": broker_url,
        "result_backend": broker_url,
        "task_default_queue": "content",
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "UTC",
        "enable_utc": True,
        "task_track_started": True,
        "imports": (
            "backend.app.tasks.content_tasks",
            "backend.app.tasks.aggregation_tasks",
        ),
    }
