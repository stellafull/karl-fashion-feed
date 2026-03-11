from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backend maintenance commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create the current PostgreSQL tables.")

    ingest_parser = subparsers.add_parser("ingest-documents", help="Collect articles and persist new documents.")
    ingest_parser.add_argument("--sources-file", type=Path, help="Optional override for the sources.yaml file.")

    return parser


def _run_init_db() -> int:
    from backend.app.db import create_all_tables

    create_all_tables()
    print("Database tables created.")
    return 0


def _run_ingest_documents(sources_file: Path | None = None) -> int:
    from backend.app.service.document_ingestion_service import DocumentIngestionService

    service = DocumentIngestionService()
    stats = service.collect_and_ingest(sources_file=sources_file)
    print(
        "Document ingestion complete. "
        f"collected={stats.collected_count} "
        f"existing={stats.existing_count} "
        f"inserted={stats.inserted_count}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        return _run_init_db()
    if args.command == "ingest-documents":
        return _run_ingest_documents(args.sources_file)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
