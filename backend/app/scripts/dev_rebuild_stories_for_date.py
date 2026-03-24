"""Rebuild category-lensed stories for one Beijing-local ingest date."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from backend.app.core.database import SessionLocal, engine
from backend.app.models import Article, Story, StoryArticle, ensure_article_storage_schema
from backend.app.service.article_parse_service import ArticleParseService
from backend.app.service.scheduler_service import (
    STORY_GROUPING_INCREMENTAL,
    SchedulerService,
)


BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild stories for one Beijing-local ingest date.",
    )
    parser.add_argument(
        "--beijing-date",
        type=date.fromisoformat,
        default=datetime.now(BEIJING_TIMEZONE).date(),
        help="Target ingest date in Beijing timezone, format: YYYY-MM-DD",
    )
    return parser.parse_args()


def _build_utc_range(target_date: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(target_date, time.min, tzinfo=BEIJING_TIMEZONE)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )


def _load_target_article_ids(
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> list[str]:
    with SessionLocal() as session:
        return session.scalars(
            select(Article.article_id)
            .where(
                Article.ingested_at >= start_utc,
                Article.ingested_at < end_utc,
                Article.parse_status == "done",
            )
            .order_by(Article.ingested_at.asc(), Article.article_id.asc())
        ).all()


def _reset_story_read_model() -> None:
    with SessionLocal() as session:
        session.execute(delete(StoryArticle))
        session.execute(delete(Story))
        session.commit()


def _reset_article_enrichment(article_ids: list[str]) -> None:
    with SessionLocal() as session:
        articles = session.scalars(
            select(Article).where(Article.article_id.in_(article_ids))
        ).all()
        for article in articles:
            article.should_publish = None
            article.reject_reason = None
            article.title_zh = None
            article.summary_zh = None
            article.tags_json = []
            article.brands_json = []
            article.categories_json = []
            article.cluster_text = None
            article.enrichment_status = "pending"
            article.enriched_at = None
            article.enrichment_error = None
            article.enrichment_attempts = 0
        session.commit()


async def main() -> None:
    args = _parse_args()
    ensure_article_storage_schema(engine)

    parse_result = await ArticleParseService().parse_articles()
    start_utc, end_utc = _build_utc_range(args.beijing_date)
    article_ids = _load_target_article_ids(start_utc=start_utc, end_utc=end_utc)
    if not article_ids:
        raise SystemExit(
            f"No parsed articles found for Beijing date {args.beijing_date.isoformat()}."
        )

    _reset_story_read_model()
    _reset_article_enrichment(article_ids)

    scheduler = SchedulerService()
    run_id = scheduler._create_run(
        skip_ingest=True,
        source_names=None,
        limit_sources=None,
        story_grouping_mode=STORY_GROUPING_INCREMENTAL,
    )
    workflow_result = await scheduler.run_story_workflow(article_ids)
    scheduler._persist_story_rows(
        run_id=run_id,
        story_drafts=list(workflow_result["story_drafts"]),
    )
    publishable_article_ids = [
        record.article_id for record in workflow_result["publishable_records"]
    ]
    post_result = await scheduler.run_post_story_workflow(publishable_article_ids)
    scheduler._mark_success(
        run_id,
        watermark_ingested_at=workflow_result["watermark_ingested_at"],
        metadata={
            "mode": "dev_rebuild_stories_for_date",
            "beijing_date": args.beijing_date.isoformat(),
            "candidates": len(article_ids),
            "parsed": parse_result.parsed,
            "parse_failed": parse_result.failed,
            "enriched": workflow_result["enriched_count"],
            "published": len(workflow_result["publishable_records"]),
            "stories_created": len(workflow_result["story_drafts"]),
            "rag_ingest": {
                "publishable_articles": post_result["rag_result"].publishable_articles,
                "text_units": post_result["rag_result"].text_units,
                "image_units": post_result["rag_result"].image_units,
                "upserted_units": post_result["rag_result"].upserted_units,
            },
        },
    )
    print(
        {
            "run_id": run_id,
            "beijing_date": args.beijing_date.isoformat(),
            "candidates": len(article_ids),
            "enriched": workflow_result["enriched_count"],
            "published": len(workflow_result["publishable_records"]),
            "stories_created": len(workflow_result["story_drafts"]),
            "rag_upserted_units": post_result["rag_result"].upserted_units,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
