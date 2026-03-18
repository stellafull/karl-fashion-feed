"""Collect article seeds and parse them."""

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
    parser = argparse.ArgumentParser(description="Collect article seeds and parse them")
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
    collection_result = await ArticleCollectionService(collector=collector).collect_articles(
        source_names=args.sources,
        limit_sources=args.limit_sources,
    )
    parse_result = await ArticleParseService(collector=collector).parse_articles()
    print(
        "ingestion completed: "
        f"collected={collection_result.total_collected} "
        f"unique_candidates={collection_result.unique_candidates} "
        f"inserted={collection_result.inserted} "
        f"skipped_existing={collection_result.skipped_existing} "
        f"skipped_in_batch={collection_result.skipped_in_batch} "
        f"parsed={parse_result.parsed} "
        f"parse_failed={parse_result.failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
