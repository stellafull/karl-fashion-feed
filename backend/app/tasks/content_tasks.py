"""Object-level Celery tasks for source collection and article content stages."""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.service.article_collection_service import ArticleCollectionService
from backend.app.service.article_parse_service import run_parse_article
from backend.app.service.event_frame_extraction_service import run_extract_event_frames
from backend.app.tasks.celery_app import celery_app


def run_collect_source(*, source_name: str, run_id: str) -> None:
    """Collect one configured source inside its own database session."""
    with SessionLocal() as session:
        _run_collect_source(session=session, source_name=source_name, run_id=run_id)


def _run_collect_source(
    *,
    session: Session,
    source_name: str,
    run_id: str,
) -> None:
    asyncio.run(
        ArticleCollectionService().collect_source(
            session,
            run_id=run_id,
            source_name=source_name,
        )
    )


@celery_app.task(name="content.collect_source")
def collect_source(source_name: str, run_id: str) -> None:
    """Run one source collection task."""
    run_collect_source(source_name=source_name, run_id=run_id)


@celery_app.task(name="content.parse_article")
def parse_article(article_id: str) -> None:
    """Run parse for one article."""
    run_parse_article(article_id=article_id)


@celery_app.task(name="content.extract_event_frames")
def extract_event_frames(article_id: str) -> None:
    """Run event-frame extraction for one parsed article."""
    run_extract_event_frames(article_id=article_id)
