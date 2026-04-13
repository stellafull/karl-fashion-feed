"""Run the scheduled daily pipeline loop in a single process."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.service.scheduler_service import SchedulerService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the daily scheduler loop")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=300.0,
        help="Scheduler tick interval in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    scheduler = SchedulerService()
    while True:
        now = datetime.now(UTC)
        run_id = scheduler.tick()
        print(f"[{now.isoformat()}] scheduler tick complete: run_id={run_id}")
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
