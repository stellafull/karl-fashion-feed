"""Run the digest runtime Celery worker for content and aggregation queues."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.tasks.celery_app import celery_app


def main() -> None:
    """Start one Celery worker process for digest runtime tasks."""
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--queues=content,aggregation",
        ]
    )


if __name__ == "__main__":
    main()
