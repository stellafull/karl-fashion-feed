"""Collect article seeds only."""

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
from backend.app.service.news_collection_service import NewsCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect article seeds only")
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
        source_concurrency=args.source_concurrency,
        global_http_concurrency=args.http_concurrency,
    )
    result = await ArticleCollectionService(collector=collector).collect_articles(
        source_names=args.sources,
        limit_sources=args.limit_sources,
    )
    print(
        "collection completed: "
        f"collected={result.total_collected} "
        f"unique_candidates={result.unique_candidates} "
        f"inserted={result.inserted} "
        f"skipped_existing={result.skipped_existing} "
        f"skipped_in_batch={result.skipped_in_batch}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
