"""Run the digest runtime Celery worker for content and aggregation queues."""

from __future__ import annotations

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
