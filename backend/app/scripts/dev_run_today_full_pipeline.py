"""Run one real same-day backend pipeline in dev from collection through RAG."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select
from sqlalchemy import update

from backend.app.core.database import SessionLocal
from backend.app.models import Article, Digest, PipelineRun, Story, ensure_article_storage_schema
from backend.app.models.event_frame import ArticleEventFrame
from backend.app.models.runtime import business_day_for_runtime, utc_bounds_for_business_day
from backend.app.service.RAG.article_rag_service import ArticleRagService, RagInsertResult
from backend.app.service.article_collection_service import ArticleCollectionService, CollectionResult
from backend.app.service.article_parse_service import ArticleParseService, ParseResult
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService
from backend.app.service.story_clustering_service import StoryClusteringService

RUN_TYPE_DEV_TODAY_FULL_PIPELINE = "dev_today_full_pipeline"
DEV_EVENT_FRAME_WORKERS = 4


class _NoopRateLimiter:
    def lease(self, *_args, **_kwargs):
        return nullcontext()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one live same-day dev pipeline from collection through RAG.",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Reuse current business-day ingested articles instead of collecting new ones",
    )
    parser.add_argument(
        "--source-name",
        action="append",
        dest="source_names",
        default=None,
        help="Only include selected source names",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit how many configured sources are enabled",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Review bundle output directory",
    )
    parser.add_argument(
        "--llm-artifact-dir",
        type=Path,
        default=None,
        help="Export KARL_LLM_DEBUG_ARTIFACT_DIR for this run",
    )
    return parser


def load_articles_by_ids(article_ids: list[str]) -> list[dict[str, Any]]:
    """Load articles as JSON-serializable dictionaries ordered by ingest time."""
    if not article_ids:
        return []

    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        rows = list(
            session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()
        )
    return [
        {
            "article_id": article.article_id,
            "source_name": article.source_name,
            "canonical_url": article.canonical_url,
            "title_raw": article.title_raw,
            "summary_raw": article.summary_raw,
            "published_at": article.published_at,
            "ingested_at": article.ingested_at,
            "parse_status": article.parse_status,
            "parse_error": article.parse_error,
            "event_frame_status": article.event_frame_status,
            "event_frame_error": article.event_frame_error,
            "markdown_rel_path": article.markdown_rel_path,
        }
        for article in rows
    ]


def load_day_ingested_articles(business_day: date) -> list[dict[str, Any]]:
    """Load all articles ingested during one Shanghai business day."""
    window_start, window_end = utc_bounds_for_business_day(business_day)
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        rows = list(
            session.scalars(
                select(Article)
                .where(Article.ingested_at >= window_start, Article.ingested_at < window_end)
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()
        )
    return [
        {
            "article_id": article.article_id,
            "source_name": article.source_name,
            "canonical_url": article.canonical_url,
            "title_raw": article.title_raw,
            "summary_raw": article.summary_raw,
            "published_at": article.published_at,
            "ingested_at": article.ingested_at,
            "parse_status": article.parse_status,
            "parse_error": article.parse_error,
            "event_frame_status": article.event_frame_status,
            "event_frame_error": article.event_frame_error,
            "markdown_rel_path": article.markdown_rel_path,
        }
        for article in rows
    ]


def load_run_digests(*, business_day: date, run_id: str) -> list[dict[str, Any]]:
    """Load digests produced by one run as JSON-serializable dictionaries."""
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
            "digest_key": digest.digest_key,
            "business_date": digest.business_date,
            "facet": digest.facet,
            "title_zh": digest.title_zh,
            "dek_zh": digest.dek_zh,
            "body_markdown": digest.body_markdown,
            "hero_image_url": digest.hero_image_url,
            "source_article_count": digest.source_article_count,
            "source_names_json": digest.source_names_json,
            "created_run_id": digest.created_run_id,
            "generation_status": digest.generation_status,
            "generation_error": digest.generation_error,
            "created_at": digest.created_at,
        }
        for digest in digests
    ]


def filter_articles_by_published_today(
    articles: list[dict[str, Any]],
    *,
    business_day: date,
) -> list[dict[str, Any]]:
    """Keep only articles whose published_at falls inside one Shanghai business day."""
    window_start, window_end = utc_bounds_for_business_day(business_day)
    return [
        row
        for row in articles
        if row.get("published_at") is not None and window_start <= row["published_at"] < window_end
    ]


def assert_clean_business_day(
    session,
    business_day: date,
    *,
    allow_existing_event_frames: bool = False,
) -> None:
    """Fail if current business day already has conflicting aggregation-stage data."""
    frame_count_today = int(
        session.scalar(
            select(func.count())
            .select_from(ArticleEventFrame)
            .where(ArticleEventFrame.business_date == business_day)
        )
        or 0
    )
    story_count_today = int(
        session.scalar(
            select(func.count())
            .select_from(Story)
            .where(Story.business_date == business_day)
        )
        or 0
    )
    digest_count_today = int(
        session.scalar(
            select(func.count())
            .select_from(Digest)
            .where(Digest.business_date == business_day)
        )
        or 0
    )
    if story_count_today or digest_count_today or (frame_count_today and not allow_existing_event_frames):
        raise RuntimeError(
            "current business day is not clean for full pipeline dev run: "
            f"business_day={business_day.isoformat()} "
            f"frame_count_today={frame_count_today} "
            f"story_count_today={story_count_today} "
            f"digest_count_today={digest_count_today}"
        )


def reclaim_running_event_frame_articles(article_ids: list[str]) -> int:
    """Reset interrupted running event-frame rows so a dev rerun can reclaim them."""
    if not article_ids:
        return 0

    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        result = session.execute(
            update(Article)
            .where(
                Article.article_id.in_(article_ids),
                Article.event_frame_status == "running",
            )
            .values(
                event_frame_status="failed",
                event_frame_error="RuntimeError: reclaimed interrupted dev full-pipeline event-frame run",
                event_frame_updated_at=_utcnow_naive(),
            )
        )
        session.commit()
        return int(result.rowcount or 0)


def run_extract_event_frames_dev(article_id: str) -> tuple[ArticleEventFrame, ...]:
    """Run one event-frame extraction without the shared Redis lease for this dev script."""
    service = EventFrameExtractionService(rate_limiter=_NoopRateLimiter())
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        claim_now = _utcnow_naive()
        claim_result = session.execute(
            update(Article)
            .where(
                Article.article_id == article_id,
                Article.parse_status == "done",
                Article.event_frame_status.in_(("pending", "failed", "queued")),
                Article.event_frame_attempts < 3,
            )
            .values(
                event_frame_status="running",
                event_frame_error=None,
                event_frame_updated_at=claim_now,
            )
        )
        if claim_result.rowcount == 1:
            session.commit()

        article = session.get(Article, article_id)
        if article is None:
            raise RuntimeError(f"article not found for frame extraction: {article_id}")
        if article.parse_status != "done":
            raise RuntimeError(f"parse must be done before frame extraction: {article_id}")
        if article.event_frame_attempts >= 3:
            raise RuntimeError(f"article already exhausted frame extraction retries: {article_id}")
        if article.event_frame_status != "running" or article.event_frame_updated_at != claim_now:
            raise RuntimeError(
                f"article is not runnable for frame extraction: {article_id} ({article.event_frame_status})"
            )

        frames = asyncio.run(
            service.extract_frames(
                session,
                article,
                claimed_updated_at=claim_now,
            )
        )
        session.commit()

        refreshed = session.get(Article, article_id)
        if refreshed is None:
            raise RuntimeError(f"article disappeared after frame extraction: {article_id}")
        if refreshed.event_frame_status != "done":
            raise RuntimeError(
                f"frame extraction did not complete for article {article_id}: {refreshed.event_frame_status}"
            )
        return frames


def extract_event_frames_for_articles(article_ids: list[str]) -> list[str]:
    """Run event-frame extraction in a small parallel pool for this dev script."""
    if not article_ids:
        return []

    failed_article_ids: list[str] = []
    worker_count = min(DEV_EVENT_FRAME_WORKERS, len(article_ids))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_by_article_id = {
            executor.submit(run_extract_event_frames_dev, article_id): article_id for article_id in article_ids
        }
        for future in as_completed(future_by_article_id):
            article_id = future_by_article_id[future]
            try:
                future.result()
            except Exception:
                failed_article_ids.append(article_id)
    return failed_article_ids


def create_dev_pipeline_run(*, business_day: date) -> str:
    """Insert one dedicated pipeline_run row for this dev script."""
    now = _utcnow_naive()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = PipelineRun(
            business_date=business_day,
            run_type=RUN_TYPE_DEV_TODAY_FULL_PIPELINE,
            status="running",
            story_status="pending",
            digest_status="pending",
            started_at=now,
            story_updated_at=now,
            digest_updated_at=now,
            metadata_json={},
        )
        session.add(run)
        session.commit()
        return run.run_id


def finalize_pipeline_run(*, run_id: str, story_done: bool, digest_done: bool) -> None:
    """Mark the dev run terminal after aggregation stages finish."""
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run missing for finalize: {run_id}")
        run.story_status = "done" if story_done else run.story_status
        run.digest_status = "done" if digest_done else run.digest_status
        run.status = "done" if story_done and digest_done else run.status
        run.finished_at = _utcnow_naive() if story_done and digest_done else run.finished_at
        run.story_updated_at = _utcnow_naive()
        run.digest_updated_at = _utcnow_naive()
        session.commit()


def mark_pipeline_run_failed(*, run_id: str, stage: str, exc: Exception) -> None:
    """Persist a failure marker for the dev run before re-raising."""
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        run = session.get(PipelineRun, run_id)
        if run is None:
            return
        if stage == "story":
            run.story_status = "failed"
        if stage == "digest":
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


def write_review_bundle(
    *,
    summary: dict[str, Any],
    digests: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    output_dir: Path | None,
) -> Path:
    """Write run summary, digests, and article details for review."""
    default_dir = (
        Path("backend/runtime_reviews")
        / f"{summary['business_day']}-full-pipeline-{summary['run_started_at'].replace(':', '').replace('-', '')}"
    )
    review_dir = output_dir or default_dir
    review_dir.mkdir(parents=True, exist_ok=True)

    summary_json_path = review_dir / "summary.json"
    summary_md_path = review_dir / "summary.md"
    digests_path = review_dir / "digests.json"
    articles_path = review_dir / "articles.json"

    summary_json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    summary_md_path.write_text(
        _build_summary_markdown(summary) + "\n",
        encoding="utf-8",
    )
    digests_path.write_text(
        json.dumps(digests, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    articles_path.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return review_dir


def run_full_pipeline(
    *,
    skip_collect: bool = False,
    source_names: list[str] | None = None,
    limit_sources: int | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Run one live same-day dev pipeline from collection through RAG."""
    run_started_at = datetime.now(UTC)
    business_day = business_day_for_runtime(run_started_at)

    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        assert_clean_business_day(
            session,
            business_day,
            allow_existing_event_frames=skip_collect,
        )

    collection_result: CollectionResult | None = None
    if skip_collect:
        candidate_articles = load_day_ingested_articles(business_day)
        candidate_article_ids = [str(row["article_id"]) for row in candidate_articles]
        if not candidate_article_ids:
            raise RuntimeError(
                f"skip-collect found zero ingested articles for business day {business_day.isoformat()}"
            )
    else:
        collection_result = asyncio.run(
            ArticleCollectionService().collect_articles(
                source_names=source_names,
                limit_sources=limit_sources,
            )
        )
        if collection_result.inserted == 0:
            raise RuntimeError("collection inserted zero new articles for full pipeline dev run")
        candidate_article_ids = list(collection_result.inserted_article_ids)

    parse_result = asyncio.run(ArticleParseService().parse_articles(article_ids=candidate_article_ids))
    candidate_articles = load_articles_by_ids(candidate_article_ids)
    parsed_articles = [row for row in candidate_articles if row["parse_status"] == "done"]
    failed_parse_ids = [
        str(row["article_id"]) for row in candidate_articles if row["parse_status"] in {"failed", "abandoned"}
    ]

    eligible_articles = filter_articles_by_published_today(parsed_articles, business_day=business_day)
    eligible_article_ids = [str(row["article_id"]) for row in eligible_articles]
    if not eligible_article_ids:
        raise RuntimeError(
            "no parse-complete articles resolved to current business day after parse: "
            f"business_day={business_day.isoformat()} candidate_article_ids={candidate_article_ids}"
        )

    reclaim_running_event_frame_articles(eligible_article_ids)
    event_frame_pending_ids = [
        str(row["article_id"]) for row in eligible_articles if row["event_frame_status"] != "done"
    ]
    failed_event_frame_ids = extract_event_frames_for_articles(event_frame_pending_ids)

    eligible_articles = filter_articles_by_published_today(
        load_articles_by_ids(candidate_article_ids),
        business_day=business_day,
    )
    event_frame_ready_article_ids = [
        str(row["article_id"]) for row in eligible_articles if row["event_frame_status"] == "done"
    ]
    if not event_frame_ready_article_ids:
        raise RuntimeError(
            "event frame extraction produced zero ready articles: "
            f"eligible_article_ids={eligible_article_ids} "
            f"failed_article_ids={failed_event_frame_ids}"
        )

    run_id = create_dev_pipeline_run(business_day=business_day)
    story_count = 0
    digest_count = 0
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
        mark_pipeline_run_failed(run_id=run_id, stage="story", exc=exc)
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
        mark_pipeline_run_failed(run_id=run_id, stage="digest", exc=exc)
        raise

    rag_result = ArticleRagService().upsert_articles(event_frame_ready_article_ids)
    if rag_result.upserted_units == 0:
        raise RuntimeError(
            "rag upsert produced zero retrieval units: "
            f"eligible_article_ids={event_frame_ready_article_ids}"
        )

    finalize_pipeline_run(run_id=run_id, story_done=True, digest_done=True)
    digests = load_run_digests(business_day=business_day, run_id=run_id)
    summary = _build_summary(
        business_day=business_day,
        run_started_at=run_started_at,
        skip_collect=skip_collect,
        source_names=source_names,
        limit_sources=limit_sources,
        run_id=run_id,
        collection_result=collection_result,
        parse_result=parse_result,
        eligible_article_ids=eligible_article_ids,
        failed_parse_ids=failed_parse_ids,
        event_frame_ready_article_ids=event_frame_ready_article_ids,
        failed_event_frame_ids=failed_event_frame_ids,
        story_count=story_count,
        digest_count=digest_count,
        rag_result=rag_result,
        digests=digests,
    )
    return write_review_bundle(
        summary=summary,
        digests=digests,
        articles=eligible_articles,
        output_dir=output_dir,
    )


def _build_summary(
    *,
    business_day: date,
    run_started_at: datetime,
    skip_collect: bool,
    source_names: list[str] | None,
    limit_sources: int | None,
    run_id: str,
    collection_result: CollectionResult | None,
    parse_result: ParseResult,
    eligible_article_ids: list[str],
    failed_parse_ids: list[str],
    event_frame_ready_article_ids: list[str],
    failed_event_frame_ids: list[str],
    story_count: int,
    digest_count: int,
    rag_result: RagInsertResult,
    digests: list[dict[str, Any]],
) -> dict[str, Any]:
    collection_summary = (
        {
            "skipped": False,
            "total_collected": collection_result.total_collected,
            "unique_candidates": collection_result.unique_candidates,
            "inserted": collection_result.inserted,
            "skipped_existing": collection_result.skipped_existing,
            "skipped_in_batch": collection_result.skipped_in_batch,
            "inserted_article_ids": list(collection_result.inserted_article_ids),
        }
        if collection_result is not None
        else {
            "skipped": True,
            "total_collected": 0,
            "unique_candidates": 0,
            "inserted": 0,
            "skipped_existing": 0,
            "skipped_in_batch": 0,
            "inserted_article_ids": [],
        }
    )
    return {
        "business_day": business_day.isoformat(),
        "run_started_at": run_started_at.isoformat(),
        "run_id": run_id,
        "run_type": RUN_TYPE_DEV_TODAY_FULL_PIPELINE,
        "skip_collect": skip_collect,
        "source_names": list(source_names or []),
        "limit_sources": limit_sources,
        "collection": collection_summary,
        "parse": {
            "candidates": parse_result.candidates,
            "parsed": parse_result.parsed,
            "failed": parse_result.failed,
            "parsed_article_ids": list(parse_result.parsed_article_ids),
            "failed_article_ids": failed_parse_ids,
        },
        "eligible_article_ids": eligible_article_ids,
        "event_frame": {
            "ready_article_ids": event_frame_ready_article_ids,
            "failed_article_ids": failed_event_frame_ids,
        },
        "story": {
            "count": story_count,
        },
        "digest": {
            "count": digest_count,
            "digest_keys": [str(digest["digest_key"]) for digest in digests],
        },
        "rag": {
            "indexed_articles": rag_result.indexed_articles,
            "text_units": rag_result.text_units,
            "image_units": rag_result.image_units,
            "upserted_units": rag_result.upserted_units,
        },
    }


def _build_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Today Full Pipeline Review",
        "",
        f"- business_day: {summary['business_day']}",
        f"- run_id: {summary['run_id']}",
        f"- run_type: {summary['run_type']}",
        f"- skip_collect: {summary['skip_collect']}",
        f"- inserted_articles: {summary['collection']['inserted']}",
        f"- parse_failed_articles: {len(summary['parse']['failed_article_ids'])}",
        f"- eligible_articles: {len(summary['eligible_article_ids'])}",
        f"- event_frame_ready_articles: {len(summary['event_frame']['ready_article_ids'])}",
        f"- event_frame_failed_articles: {len(summary['event_frame']['failed_article_ids'])}",
        f"- stories: {summary['story']['count']}",
        f"- digests: {summary['digest']['count']}",
        f"- rag_upserted_units: {summary['rag']['upserted_units']}",
    ]
    return "\n".join(lines)


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value type: {type(value)}")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args and run the full same-day dev pipeline."""
    args = _build_parser().parse_args(argv)
    if args.llm_artifact_dir is not None:
        os.environ["KARL_LLM_DEBUG_ARTIFACT_DIR"] = str(args.llm_artifact_dir)

    review_dir = run_full_pipeline(
        skip_collect=args.skip_collect,
        source_names=args.source_names,
        limit_sources=args.limit_sources,
        output_dir=args.output_dir,
    )
    print(f"review bundle: {review_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
