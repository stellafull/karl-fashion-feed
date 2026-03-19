"""Dev-only incremental ingest script: collect live articles and parse only newly inserted rows."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.database import Base, engine
from backend.app.models import ensure_article_storage_schema
from backend.app.service.article_collection_service import ArticleCollectionService
from backend.app.service.article_parse_service import ArticleParseService
from backend.app.service.news_collection_service import NewsCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dev incremental ingest: collect live articles and parse only rows inserted in this run"
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
        "--max-articles-per-source",
        type=int,
        default=None,
        help="Override source max article count for this run.",
    )
    parser.add_argument(
        "--max-pages-per-source",
        type=int,
        default=None,
        help="Override web source max page traversal for this run.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=12,
        help="Per-request timeout when fetching RSS or web pages.",
    )
    parser.add_argument(
        "--source-concurrency",
        type=int,
        default=4,
        help="How many sources to collect concurrently.",
    )
    parser.add_argument(
        "--http-concurrency",
        type=int,
        default=16,
        help="Global concurrent HTTP request limit.",
    )
    return parser


async def main() -> int:
    args = build_parser().parse_args()

    ensure_article_storage_schema(engine)
    Base.metadata.create_all(bind=engine)

    collector = NewsCollectionService(
        request_timeout_seconds=args.request_timeout_seconds,
        source_concurrency=args.source_concurrency,
        global_http_concurrency=args.http_concurrency,
    )
    collection_service = ArticleCollectionService(collector=collector)
    parse_service = ArticleParseService(collector=collector)

    collection_result = await collection_service.collect_articles(
        source_names=args.sources,
        limit_sources=args.limit_sources,
        max_articles_per_source=args.max_articles_per_source,
        max_pages_per_source=args.max_pages_per_source,
    )

    print(
        "dev collection completed: "
        f"collected={collection_result.total_collected} "
        f"unique_candidates={collection_result.unique_candidates} "
        f"inserted={collection_result.inserted} "
        f"skipped_existing={collection_result.skipped_existing} "
        f"skipped_in_batch={collection_result.skipped_in_batch}"
    )

    if not collection_result.inserted_article_ids:
        print("dev parse completed: candidates=0 parsed=0 failed=0")
        return 0

    parse_result = await parse_service.parse_articles(
        article_ids=list(collection_result.inserted_article_ids)
    )
    print(
        "dev parse completed: "
        f"candidates={parse_result.candidates} "
        f"parsed={parse_result.parsed} "
        f"failed={parse_result.failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
