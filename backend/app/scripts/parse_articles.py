"""Parse pending article seeds."""

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
from backend.app.service.article_parse_service import ArticleParseService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse pending article seeds")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit how many pending/failed articles are parsed.",
    )
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    ensure_article_storage_schema(engine)
    Base.metadata.create_all(bind=engine)
    result = await ArticleParseService().parse_articles(limit=args.limit)
    print(
        "parse completed: "
        f"candidates={result.candidates} "
        f"parsed={result.parsed} "
        f"failed={result.failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
