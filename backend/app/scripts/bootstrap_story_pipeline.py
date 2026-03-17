"""Bootstrap story aggregation as an explicit script, not part of the incremental runtime pipeline."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.database import Base, SessionLocal, engine
from backend.app.models import Article, PipelineRun, Story, StoryArticle, ensure_article_storage_schema
from backend.app.service.article_ingestion_service import ArticleIngestionService
from backend.app.service.story_pipeline_contracts import StoryDraft
from backend.app.service.story_workflow_service import StoryWorkflowService

RUN_TYPE_BOOTSTRAP_STORY = "bootstrap_story_day"
STAGE_STORY_PERSIST = "story_persist"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap historical story aggregation")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip article ingestion and only process already ingested articles.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Collect only the named source. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit how many configured sources are processed.",
    )
    parser.add_argument(
        "--story-date",
        type=date.fromisoformat,
        default=None,
        help="Run bootstrap story aggregation for one published_at date only, e.g. 2026-03-16.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ensure_article_storage_schema(engine)
    Base.metadata.create_all(bind=engine)

    ingestion_result = None
    if not args.skip_ingest:
        ingestion_result = asyncio.run(
            ArticleIngestionService().collect_and_ingest(
                source_names=args.sources,
                limit_sources=args.limit_sources,
            )
        )
        print(
            "bootstrap ingestion completed: "
            f"collected={ingestion_result.total_collected} "
            f"unique_candidates={ingestion_result.unique_candidates} "
            f"inserted={ingestion_result.inserted} "
            f"skipped_existing={ingestion_result.skipped_existing} "
            f"skipped_in_batch={ingestion_result.skipped_in_batch}"
        )

    story_dates = [args.story_date] if args.story_date else _list_story_dates()
    if not story_dates:
        print("bootstrap story pipeline completed: story_dates=[] candidates=0 stories_created=0")
        return 0

    workflow_service = StoryWorkflowService()
    aggregate = {"candidates": 0, "enriched": 0, "published": 0, "stories_created": 0}
    for story_date in story_dates:
        article_ids = _load_story_date_article_ids(story_date)
        run_id = _create_run(
            skip_ingest=args.skip_ingest,
            source_names=args.sources,
            limit_sources=args.limit_sources,
            story_date=story_date,
        )
        try:
            result = workflow_service.run(article_ids)
            stages_completed = list(result.stages_completed)
            if STAGE_STORY_PERSIST not in stages_completed:
                stages_completed.append(STAGE_STORY_PERSIST)
            _persist_story_drafts(
                run_id=run_id,
                story_drafts=list(result.story_drafts),
                watermark_ingested_at=result.watermark_ingested_at,
                metadata={
                    "candidates": len(article_ids),
                    "enriched": result.enriched_count,
                    "published": len(result.publishable_records),
                    "stories_created": len(result.story_drafts),
                    "story_date": story_date.isoformat(),
                    "story_grouping_mode": "bootstrap_published_at_day",
                    "stages_completed": stages_completed,
                    "stages_skipped": list(result.stages_skipped),
                    "source_names": args.sources or [],
                    "limit_sources": args.limit_sources,
                },
            )
        except Exception as exc:
            _mark_failed(run_id, exc)
            raise

        aggregate["candidates"] += len(article_ids)
        aggregate["enriched"] += result.enriched_count
        aggregate["published"] += len(result.publishable_records)
        aggregate["stories_created"] += len(result.story_drafts)
        print(
            "bootstrap story day completed: "
            f"story_date={story_date.isoformat()} "
            f"run_id={run_id} "
            f"candidates={len(article_ids)} "
            f"enriched={result.enriched_count} "
            f"published={len(result.publishable_records)} "
            f"stories_created={len(result.story_drafts)} "
            f"stages_completed={stages_completed} "
            f"stages_skipped={list(result.stages_skipped)}"
        )

    print(
        "bootstrap story pipeline completed: "
        f"story_dates={[item.isoformat() for item in story_dates]} "
        f"candidates={aggregate['candidates']} "
        f"enriched={aggregate['enriched']} "
        f"published={aggregate['published']} "
        f"stories_created={aggregate['stories_created']} "
        f"ingested={0 if ingestion_result is None else ingestion_result.inserted}"
    )
    return 0


def _list_story_dates() -> list[date]:
    with SessionLocal() as session:
        published_values = session.scalars(
            select(Article.published_at)
            .where(Article.published_at.is_not(None))
            .order_by(Article.published_at.asc())
        ).all()
    return sorted({value.date() for value in published_values if value is not None})


def _load_story_date_article_ids(story_date: date) -> list[str]:
    start = datetime.combine(story_date, datetime.min.time())
    end = datetime.combine(story_date, datetime.max.time())
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Article.article_id)
                .where(
                    Article.published_at.is_not(None),
                    Article.published_at >= start,
                    Article.published_at <= end,
                )
                .order_by(Article.published_at.asc(), Article.ingested_at.asc(), Article.article_id.asc())
            )
        )


def _create_run(
    *,
    skip_ingest: bool,
    source_names: list[str] | None,
    limit_sources: int | None,
    story_date: date,
) -> str:
    with SessionLocal() as session:
        run = PipelineRun(
            run_type=RUN_TYPE_BOOTSTRAP_STORY,
            status="running",
            metadata_json={
                "skip_ingest": skip_ingest,
                "source_names": source_names or [],
                "limit_sources": limit_sources,
                "story_date": story_date.isoformat(),
                "story_grouping_mode": "bootstrap_published_at_day",
            },
        )
        session.add(run)
        session.commit()
        return run.run_id


def _persist_story_drafts(
    *,
    run_id: str,
    story_drafts: list[StoryDraft],
    watermark_ingested_at: datetime | None,
    metadata: dict,
) -> None:
    with SessionLocal() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise ValueError(f"pipeline run not found: {run_id}")

        for draft in story_drafts:
            story = Story(
                created_run_id=run_id,
                title_zh=draft.title_zh,
                summary_zh=draft.summary_zh,
                key_points_json=list(draft.key_points),
                tags_json=list(draft.tags),
                category=draft.category,
                hero_image_url=draft.hero_image_url,
                source_article_count=draft.source_article_count,
            )
            session.add(story)
            session.flush()

            for rank, article_id in enumerate(draft.article_ids, start=1):
                session.add(
                    StoryArticle(
                        story_key=story.story_key,
                        article_id=article_id,
                        rank=rank,
                    )
                )

        run.status = "success"
        run.finished_at = _utcnow_naive()
        run.watermark_ingested_at = watermark_ingested_at
        run.metadata_json = metadata
        run.error_message = None
        session.commit()


def _mark_failed(run_id: str, exc: Exception) -> None:
    with SessionLocal() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            return
        run.status = "failed"
        run.finished_at = _utcnow_naive()
        run.error_message = f"{exc.__class__.__name__}: {exc}"
        session.commit()


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


if __name__ == "__main__":
    raise SystemExit(main())
