"""Inject recent demo digests into an empty runtime database."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.database import SessionLocal
from backend.app.models import Article, Digest, PipelineRun, Story, ensure_article_storage_schema
from backend.app.models.runtime import business_day_for_runtime, utc_bounds_for_business_day
from backend.app.scripts.dev_run_today_full_pipeline import (
    extract_event_frames_for_articles,
    load_articles_by_ids,
    reclaim_running_event_frame_articles,
)
from backend.app.service.article_collection_service import ArticleCollectionService, CollectionResult
from backend.app.service.article_parse_service import ArticleParseService, ParseResult
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.story_clustering_service import StoryClusteringService

RUN_TYPE_DEMO_DIGEST_INIT = "demo_digest_init"
DEFAULT_HISTORY_DAYS = 7


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect recent articles once and inject demo digests for the last complete business days.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_HISTORY_DAYS,
        help="How many complete business days to backfill. Defaults to 7.",
    )
    parser.add_argument(
        "--source-name",
        action="append",
        dest="source_names",
        default=None,
        help="Only collect selected source names.",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit how many configured sources are enabled for this demo init.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional summary output directory.",
    )
    return parser


def _target_business_days(*, now: datetime, days: int) -> list[date]:
    if days <= 0:
        raise ValueError("days must be greater than zero")
    today = business_day_for_runtime(now)
    return [today - timedelta(days=offset) for offset in range(days, 0, -1)]


def _assert_demo_state_is_empty() -> None:
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        digest_count = int(session.scalar(select(func.count()).select_from(Digest)) or 0)
        story_count = int(session.scalar(select(func.count()).select_from(Story)) or 0)
        run_count = int(session.scalar(select(func.count()).select_from(PipelineRun)) or 0)
    if digest_count or story_count or run_count:
        raise RuntimeError(
            "demo digest init requires empty runtime state: "
            f"digest_count={digest_count} story_count={story_count} pipeline_run_count={run_count}"
        )


def _assign_articles_to_business_days(
    *,
    article_ids: list[str],
    target_days: list[date],
) -> dict[date, list[str]]:
    target_day_set = set(target_days)
    grouped_article_ids: dict[date, list[str]] = defaultdict(list)

    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        articles = list(
            session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.published_at.asc(), Article.article_id.asc())
            ).all()
        )
        for article in articles:
            if article.published_at is None:
                continue
            article_business_day = business_day_for_runtime(article.published_at)
            if article_business_day not in target_day_set:
                continue
            article.ingested_at = article.published_at
            grouped_article_ids[article_business_day].append(str(article.article_id))
        session.commit()

    missing_days = [business_day.isoformat() for business_day in target_days if not grouped_article_ids.get(business_day)]
    if missing_days:
        raise RuntimeError(
            "demo digest init could not assign at least one collected article to every target business day: "
            f"missing_days={missing_days}"
        )
    return {business_day: grouped_article_ids[business_day] for business_day in target_days}


def _create_demo_pipeline_run(*, business_day: date) -> str:
    now = _utcnow_naive()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = PipelineRun(
            business_date=business_day,
            run_type=RUN_TYPE_DEMO_DIGEST_INIT,
            status="running",
            story_status="pending",
            digest_status="pending",
            started_at=now,
            story_updated_at=now,
            digest_updated_at=now,
            metadata_json={"mode": "demo_init"},
        )
        session.add(run)
        session.commit()
        return str(run.run_id)


def _mark_demo_pipeline_run_failed(*, run_id: str, stage: str, exc: Exception) -> None:
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = session.get(PipelineRun, run_id)
        if run is None:
            return
        if stage == "story":
            run.story_status = "failed"
        elif stage == "digest":
            run.story_status = "done"
            run.digest_status = "failed"
        run.status = "failed"
        run.finished_at = _utcnow_naive()
        run.metadata_json = {
            **(run.metadata_json or {}),
            "failed_stage": stage,
            "error": f"{exc.__class__.__name__}: {exc}",
        }
        session.commit()


def _finalize_demo_pipeline_run(*, run_id: str) -> None:
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run missing for finalize: {run_id}")
        finished_at = _utcnow_naive()
        run.story_status = "done"
        run.digest_status = "done"
        run.status = "done"
        run.story_updated_at = finished_at
        run.digest_updated_at = finished_at
        run.finished_at = finished_at
        session.commit()


def _load_run_digests(*, business_day: date, run_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        digests = list(
            session.scalars(
                select(Digest)
                .where(
                    Digest.business_date == business_day,
                    Digest.created_run_id == run_id,
                )
                .order_by(Digest.created_at.asc(), Digest.digest_key.asc())
            ).all()
        )
    return [
        {
            "digest_key": str(digest.digest_key),
            "business_date": digest.business_date.isoformat(),
            "facet": digest.facet,
            "title_zh": digest.title_zh,
            "source_article_count": digest.source_article_count,
            "generation_status": digest.generation_status,
            "created_run_id": str(digest.created_run_id),
        }
        for digest in digests
    ]


def _write_summary(*, summary: dict[str, Any], output_dir: Path | None) -> Path:
    default_dir = Path("backend/runtime_reviews") / f"demo-init-{summary['run_started_at'].replace(':', '').replace('-', '')}"
    review_dir = output_dir or default_dir
    review_dir.mkdir(parents=True, exist_ok=True)

    summary_json_path = review_dir / "summary.json"
    summary_md_path = review_dir / "summary.md"

    summary_json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    summary_md_path.write_text(_build_summary_markdown(summary) + "\n", encoding="utf-8")
    return review_dir


def _build_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Demo Digest Init Summary",
        "",
        f"- started_at: {summary['run_started_at']}",
        f"- target_days: {', '.join(summary['target_business_days'])}",
        f"- collected_inserted: {summary['collection']['inserted']}",
        f"- parse_parsed: {summary['parse']['parsed']}",
        f"- parse_failed: {summary['parse']['failed']}",
        f"- event_frame_failed: {len(summary['event_frame_failed_article_ids'])}",
        f"- total_digests: {summary['total_digest_count']}",
        "",
        "## Per Day",
        "",
    ]
    for item in summary["days"]:
        lines.extend(
            [
                f"### {item['business_day']}",
                f"- article_count: {item['article_count']}",
                f"- parsed_count: {item['parsed_count']}",
                f"- event_frame_ready_count: {item['event_frame_ready_count']}",
                f"- story_count: {item['story_count']}",
                f"- digest_count: {item['digest_count']}",
                f"- run_id: {item['run_id']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value type: {type(value)}")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    now = datetime.now(UTC)
    target_days = _target_business_days(now=now, days=args.days)
    _assert_demo_state_is_empty()

    earliest_window_start, _ = utc_bounds_for_business_day(target_days[0])
    collection_result = asyncio.run(
        ArticleCollectionService().collect_articles(
            source_names=args.source_names,
            limit_sources=args.limit_sources,
            published_after=earliest_window_start,
        )
    )
    if collection_result.inserted == 0:
        raise RuntimeError("demo digest init inserted zero articles")

    articles_by_day = _assign_articles_to_business_days(
        article_ids=list(collection_result.inserted_article_ids),
        target_days=target_days,
    )
    ordered_article_ids = [article_id for business_day in target_days for article_id in articles_by_day[business_day]]

    parse_result = asyncio.run(ArticleParseService().parse_articles(article_ids=ordered_article_ids))
    parsed_rows = load_articles_by_ids(ordered_article_ids)
    parsed_article_ids = [
        str(row["article_id"])
        for row in parsed_rows
        if row["parse_status"] == "done"
    ]
    failed_parse_article_ids = [
        str(row["article_id"])
        for row in parsed_rows
        if row["parse_status"] in {"failed", "abandoned"}
    ]
    if not parsed_article_ids:
        raise RuntimeError("demo digest init produced zero parse-complete articles")

    reclaim_running_event_frame_articles(parsed_article_ids)
    event_frame_pending_ids = [
        str(row["article_id"])
        for row in parsed_rows
        if row["parse_status"] == "done" and row["event_frame_status"] != "done"
    ]
    failed_event_frame_article_ids = extract_event_frames_for_articles(event_frame_pending_ids)

    refreshed_rows = {str(row["article_id"]): row for row in load_articles_by_ids(ordered_article_ids)}
    per_day_summary: list[dict[str, Any]] = []
    total_digest_count = 0

    for business_day in target_days:
        day_article_ids = list(articles_by_day[business_day])
        day_rows = [refreshed_rows[article_id] for article_id in day_article_ids]
        parsed_day_article_ids = [
            str(row["article_id"])
            for row in day_rows
            if row["parse_status"] == "done"
        ]
        ready_day_article_ids = [
            str(row["article_id"])
            for row in day_rows
            if row["event_frame_status"] == "done"
        ]
        if not ready_day_article_ids:
            raise RuntimeError(
                "demo digest init produced zero event-frame-ready articles for business day: "
                f"{business_day.isoformat()}"
            )

        run_id = _create_demo_pipeline_run(business_day=business_day)
        try:
            with SessionLocal() as session:
                ensure_article_storage_schema(session.get_bind())
                stories = asyncio.run(
                    StoryClusteringService().cluster_business_day(
                        session,
                        business_day,
                        run_id=run_id,
                    )
                )
                session.commit()
                story_count = len(stories)
            if story_count == 0:
                raise RuntimeError(
                    f"story clustering produced zero stories for business day {business_day.isoformat()}"
                )
        except Exception as exc:
            _mark_demo_pipeline_run_failed(run_id=run_id, stage="story", exc=exc)
            raise

        try:
            with SessionLocal() as session:
                ensure_article_storage_schema(session.get_bind())
                digests = asyncio.run(
                    DigestGenerationService().generate_for_day(
                        session,
                        business_day,
                        run_id=run_id,
                    )
                )
                session.commit()
                digest_count = len(digests)
            if digest_count == 0:
                raise RuntimeError(
                    f"digest generation produced zero digests for business day {business_day.isoformat()}"
                )
        except Exception as exc:
            _mark_demo_pipeline_run_failed(run_id=run_id, stage="digest", exc=exc)
            raise

        _finalize_demo_pipeline_run(run_id=run_id)
        day_digests = _load_run_digests(business_day=business_day, run_id=run_id)
        total_digest_count += len(day_digests)
        per_day_summary.append(
            {
                "business_day": business_day.isoformat(),
                "article_count": len(day_article_ids),
                "parsed_count": len(parsed_day_article_ids),
                "event_frame_ready_count": len(ready_day_article_ids),
                "story_count": story_count,
                "digest_count": len(day_digests),
                "run_id": run_id,
                "digests": day_digests,
            }
        )

    summary = {
        "run_started_at": now.isoformat(),
        "run_type": RUN_TYPE_DEMO_DIGEST_INIT,
        "target_business_days": [business_day.isoformat() for business_day in target_days],
        "collection": {
            "total_collected": collection_result.total_collected,
            "unique_candidates": collection_result.unique_candidates,
            "inserted": collection_result.inserted,
            "skipped_existing": collection_result.skipped_existing,
            "skipped_in_batch": collection_result.skipped_in_batch,
        },
        "parse": {
            "candidates": parse_result.candidates,
            "parsed": parse_result.parsed,
            "failed": parse_result.failed,
            "failed_article_ids": failed_parse_article_ids,
        },
        "event_frame_failed_article_ids": failed_event_frame_article_ids,
        "days": per_day_summary,
        "total_digest_count": total_digest_count,
    }
    review_dir = _write_summary(summary=summary, output_dir=args.output_dir)
    print(f"demo init summary: {review_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
