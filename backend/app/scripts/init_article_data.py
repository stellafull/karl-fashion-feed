"""Initialize article data by backfilling recent sources."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.database import Base, SessionLocal, engine
from backend.app.config.source_config import SourceConfig, load_source_configs
from backend.app.models import Article  # noqa: F401
from backend.app.service.article_ingestion_service import ArticleIngestionService
from backend.app.service.news_collection_service import (
    CollectedArticle,
    NewsCollectionService,
    SourceCollectionResult,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill recent article data")
    parser.add_argument("--days-back", type=int, default=30, help="Recent window in days")
    parser.add_argument(
        "--max-articles-per-source",
        type=int,
        default=200,
        help="Override source max article count for this bootstrap run",
    )
    parser.add_argument(
        "--max-pages-per-source",
        type=int,
        default=5,
        help="Override web source max page traversal for this bootstrap run",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Limit bootstrap to one or more named sources",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit how many configured sources are processed",
    )
    parser.add_argument(
        "--include-undated",
        action="store_true",
        help="Include articles without a parsed published_at value",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=8,
        help="Per-request timeout when fetching RSS or web pages",
    )
    parser.add_argument(
        "--source-concurrency",
        type=int,
        default=4,
        help="How many sources to collect concurrently",
    )
    parser.add_argument(
        "--http-concurrency",
        type=int,
        default=16,
        help="Global concurrent HTTP request limit",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=args.days_back)

    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        print(
            "database connection failed before bootstrap: "
            f"{exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    sources = _select_sources(args)
    collector = NewsCollectionService(
        source_configs=sources,
        request_timeout_seconds=args.request_timeout_seconds,
        source_concurrency=args.source_concurrency,
        global_http_concurrency=args.http_concurrency,
    )

    try:
        source_results = asyncio.run(
            collector.collect_source_results(
                published_after=cutoff,
                max_articles_per_source=args.max_articles_per_source,
                max_pages_per_source=args.max_pages_per_source,
                include_undated=args.include_undated,
            )
        )
    except Exception as exc:  # pragma: no cover - runtime guard
        print(f"collection failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    _print_collection_results(source_results)

    ingestion_service = ArticleIngestionService()
    all_articles: list[CollectedArticle] = []
    aggregate = Counter()
    source_counter = Counter()
    failed_sources: list[str] = []

    for result in source_results:
        if result.error is not None:
            failed_sources.append(result.source_name)
            continue

        source_counter[result.source_name] += len(result.articles)
        all_articles.extend(result.articles)
        try:
            ingestion_result = ingestion_service.ingest_articles(result.articles)
        except OperationalError as exc:
            print(
                "database write failed during bootstrap: "
                f"{exc.__class__.__name__}: {exc}",
                file=sys.stderr,
            )
            return 1

        aggregate["total_collected"] += ingestion_result.total_collected
        aggregate["unique_candidates"] += ingestion_result.unique_candidates
        aggregate["inserted"] += ingestion_result.inserted
        aggregate["skipped_existing"] += ingestion_result.skipped_existing
        aggregate["skipped_in_batch"] += ingestion_result.skipped_in_batch

    _print_summary(args, cutoff, all_articles, aggregate, source_counter, failed_sources)
    return 0


def _select_sources(args: argparse.Namespace) -> list[SourceConfig]:
    sources = load_source_configs()
    if args.sources:
        allowed = {name.strip().lower() for name in args.sources}
        sources = [source for source in sources if source.name.lower() in allowed]
    if args.limit_sources is not None:
        sources = sources[: args.limit_sources]
    return sources


def _print_summary(
    args: argparse.Namespace,
    cutoff: datetime,
    articles: list[CollectedArticle],
    aggregate: Counter,
    source_counter: Counter,
    failed_sources: list[str],
) -> None:
    dated_articles = [article.published_at for article in articles if article.published_at]
    with SessionLocal() as session:
        total_rows = session.scalar(select(func.count()).select_from(Article)) or 0

    print(
        "bootstrap completed: "
        f"days_back={args.days_back} "
        f"cutoff={cutoff.isoformat(sep=' ')} "
        f"collected={aggregate['total_collected']} "
        f"unique_candidates={aggregate['unique_candidates']} "
        f"inserted={aggregate['inserted']} "
        f"skipped_existing={aggregate['skipped_existing']} "
        f"skipped_in_batch={aggregate['skipped_in_batch']} "
        f"article_rows={total_rows}"
    )
    if dated_articles:
        print(
            "published_at range: "
            f"{min(dated_articles).isoformat(sep=' ')} -> "
            f"{max(dated_articles).isoformat(sep=' ')}"
        )
    else:
        print("published_at range: none")

    print("top sources:")
    for source_name, count in source_counter.most_common(15):
        print(f"- {source_name}: {count}")
    if failed_sources:
        print("failed sources:")
        for source_name in failed_sources:
            print(f"- {source_name}")


def _print_collection_results(results: list[SourceCollectionResult]) -> None:
    total = len(results)
    for index, result in enumerate(results, start=1):
        print(f"[{index}/{total}] collected {result.source_name} ({result.source_type})")
        if result.error is not None:
            print(
                "  collection failed: "
                f"{result.error.__class__.__name__}: {result.error}",
                file=sys.stderr,
            )
            continue
        print(f"  collected_articles={len(result.articles)}")


if __name__ == "__main__":
    raise SystemExit(main())
