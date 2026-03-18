"""Validate configured source definitions."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config.source_config import load_source_configs


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Validate sources.yaml")


async def main() -> int:
    build_parser().parse_args()
    sources = load_source_configs(include_disabled=True)
    rss_count = sum(1 for source in sources if source.type == "rss")
    web_count = sum(1 for source in sources if source.type == "web")
    enabled_count = sum(1 for source in sources if source.enabled)
    print(
        f"loaded {len(sources)} sources: {enabled_count} enabled, "
        f"{rss_count} rss, {web_count} web"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
