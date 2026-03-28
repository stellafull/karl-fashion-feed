"""Run the digest runtime coordinator loop in a single process."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from backend.app.models.runtime import business_day_for_runtime
from backend.app.service.daily_run_coordinator_service import DailyRunCoordinatorService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the daily digest coordinator loop")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Coordinator tick interval in seconds",
    )
    parser.add_argument(
        "--source-name",
        action="append",
        dest="source_names",
        default=None,
        help="Only enable selected source names",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit the number of configured sources for this runtime",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start the coordinator loop and keep ticking forever."""
    args = _build_parser().parse_args(argv)
    coordinator = DailyRunCoordinatorService(
        source_names=args.source_names,
        limit_sources=args.limit_sources,
    )
    while True:
        now = datetime.now(UTC)
        run_id = coordinator.tick(now=now)
        business_day = business_day_for_runtime(now)
        print(
            f"[{now.isoformat()}] coordinator tick complete: "
            f"business_day={business_day.isoformat()} run_id={run_id}"
        )
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
