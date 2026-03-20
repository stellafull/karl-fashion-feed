"""Run the daily scheduler that triggers the story pipeline at Beijing 08:00."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.service.scheduler_service import SchedulerService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the daily story scheduler at Beijing 08:00",
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
    await SchedulerService().run_forever(
        skip_ingest=args.skip_ingest,
        source_names=args.sources,
        limit_sources=args.limit_sources,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
