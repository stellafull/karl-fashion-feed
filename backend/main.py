"""Backend CLI entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config.source_config import load_source_configs
from backend.app.core.database import Base, engine
from backend.app.models import Article, ensure_article_storage_schema  # noqa: F401
from backend.app.service.article_ingestion_service import ArticleIngestionService
from backend.app.service.news_collection_service import NewsCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KARL Fashion Feed backend CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-sources", help="Validate sources.yaml")
    validate_parser.set_defaults(func=run_validate_sources)

    ingest_parser = subparsers.add_parser("ingest-articles", help="Collect and ingest articles")
    ingest_parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Collect only the named source. Can be passed multiple times.",
    )
    ingest_parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit how many configured sources are processed.",
    )
    ingest_parser.add_argument(
        "--source-concurrency",
        type=int,
        default=4,
        help="How many sources to collect concurrently.",
    )
    ingest_parser.add_argument(
        "--http-concurrency",
        type=int,
        default=16,
        help="Global concurrent HTTP request limit.",
    )
    ingest_parser.set_defaults(func=run_ingest_articles)

    return parser


def run_validate_sources(_: argparse.Namespace) -> int:
    sources = load_source_configs(include_disabled=True)
    rss_count = sum(1 for source in sources if source.type == "rss")
    web_count = sum(1 for source in sources if source.type == "web")
    enabled_count = sum(1 for source in sources if source.enabled)
    print(
        f"loaded {len(sources)} sources: {enabled_count} enabled, "
        f"{rss_count} rss, {web_count} web"
    )
    return 0


def run_ingest_articles(args: argparse.Namespace) -> int:
    ensure_article_storage_schema(engine)
    Base.metadata.create_all(bind=engine)
    collector = NewsCollectionService(
        source_concurrency=args.source_concurrency,
        global_http_concurrency=args.http_concurrency,
    )
    result = asyncio.run(
        ArticleIngestionService(collector=collector).collect_and_ingest(
            source_names=args.sources,
            limit_sources=args.limit_sources,
        )
    )
    print(
        "ingestion completed: "
        f"collected={result.total_collected} "
        f"unique_candidates={result.unique_candidates} "
        f"inserted={result.inserted} "
        f"skipped_existing={result.skipped_existing} "
        f"skipped_in_batch={result.skipped_in_batch}"
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
