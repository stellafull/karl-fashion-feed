"""Run today's digest runtime synchronously and emit a review bundle."""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models import Article, Digest, ensure_article_storage_schema
from backend.app.models.runtime import business_day_for_runtime, utc_bounds_for_business_day
from backend.app.service.daily_run_coordinator_service import DailyRunCoordinatorService
from backend.app.tasks.celery_app import celery_app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run today's digest runtime in eager mode and write a review bundle",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Skip source collection and only run downstream runtime stages",
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
    return parser


@contextmanager
def temporary_celery_eager_mode() -> Iterator[None]:
    """Run Celery tasks eagerly in-process for local script execution."""
    original_always_eager = celery_app.conf.task_always_eager
    original_eager_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = original_always_eager
        celery_app.conf.task_eager_propagates = original_eager_propagates


def load_day_digests(business_day: date) -> list[dict[str, Any]]:
    """Load one business day's digests as JSON-serializable dictionaries."""
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        digests = list(
            session.scalars(
                select(Digest)
                .where(Digest.business_date == business_day)
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


def load_day_articles(business_day: date) -> list[dict[str, Any]]:
    """Load one business day's articles as JSON-serializable dictionaries."""
    window_start, window_end = utc_bounds_for_business_day(business_day)
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        articles = list(
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
        }
        for article in articles
    ]


def write_review_bundle(
    *,
    business_day: date,
    digests: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    output_dir: Path | None,
) -> Path:
    """Write digest/article review artifacts for same-day verification."""
    review_dir = output_dir or Path("backend/runtime_reviews") / business_day.isoformat()
    review_dir.mkdir(parents=True, exist_ok=True)

    digests_path = review_dir / "digests.json"
    articles_path = review_dir / "articles.json"
    summary_path = review_dir / "summary.md"

    digests_path.write_text(
        json.dumps(digests, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    articles_path.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        "\n".join(
            [
                "# Today Digest Review",
                "",
                f"- business_day: {business_day.isoformat()}",
                f"- digest_count: {len(digests)}",
                f"- article_count: {len(articles)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return review_dir


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value type: {type(value)}")


def main(argv: list[str] | None = None) -> int:
    """Run today's runtime in eager mode and print bundle output path."""
    args = _build_parser().parse_args(argv)
    now = datetime.now(UTC)
    business_day = business_day_for_runtime(now)
    source_names = [] if args.skip_collect else args.source_names
    with temporary_celery_eager_mode():
        coordinator = DailyRunCoordinatorService(
            source_names=source_names,
            limit_sources=args.limit_sources,
        )
        run_id = coordinator.tick(now=now)
        coordinator.drain_until_idle(
            run_id=run_id,
            business_day=business_day,
            skip_collect=args.skip_collect,
        )

    review_dir = write_review_bundle(
        business_day=business_day,
        digests=load_day_digests(business_day),
        articles=load_day_articles(business_day),
        output_dir=args.output_dir,
    )
    print(f"review bundle: {review_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
