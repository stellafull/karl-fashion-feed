"""Run the daily story pipeline."""

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
from backend.app.service.daily_pipeline_service import DailyPipelineService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect, parse, enrich, cluster, and generate immutable stories"
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip article collection and only parse/process already stored articles.",
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
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    ensure_article_storage_schema(engine)
    Base.metadata.create_all(bind=engine)
    result = await DailyPipelineService().run(
        skip_ingest=args.skip_ingest,
        source_names=args.sources,
        limit_sources=args.limit_sources,
    )
    print(
        "daily pipeline completed: "
        f"run_id={result.run_id} "
        f"candidates={result.candidates} "
        f"enriched={result.enriched} "
        f"published={result.published} "
        f"stories_created={result.stories_created} "
        f"skipped_existing_enrichment={result.skipped_existing_enrichment} "
        f"story_grouping_mode={result.story_grouping_mode} "
        f"stages_completed={list(result.stages_completed)} "
        f"stages_skipped={list(result.stages_skipped)} "
        f"watermark_ingested_at={result.watermark_ingested_at}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
