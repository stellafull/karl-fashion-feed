from __future__ import annotations

import argparse
import dataclasses
import importlib
from pathlib import Path
from typing import Any, Mapping, Sequence


def _load_contract_class(module_path: str, class_name: str) -> type[Any]:
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name == module_path:
            raise RuntimeError(
                f"Required service module '{module_path}' is not available for '{class_name}'."
            ) from exc
        raise

    try:
        loaded_class = getattr(module, class_name)
    except AttributeError as exc:
        raise RuntimeError(
            f"Required service class '{class_name}' is not defined in '{module_path}'."
        ) from exc
    if not isinstance(loaded_class, type):
        raise RuntimeError(
            f"Resolved '{module_path}.{class_name}' is not a class and cannot be used by the CLI."
        )
    return loaded_class


def _public_fields(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "_asdict"):
        return value._asdict()
    if hasattr(value, "__dict__"):
        return {
            key: field_value
            for key, field_value in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _format_public_fields(value: Any) -> str:
    fields = _public_fields(value)
    if not fields:
        return str(value)
    return " ".join(f"{key}={field_value!r}" for key, field_value in fields.items())


def _invoke_service_method(service: Any, method_names: Sequence[str], **kwargs: Any) -> Any:
    for method_name in method_names:
        method = getattr(service, method_name, None)
        if callable(method):
            return method(**kwargs)
    available_methods = sorted(name for name in dir(service) if not name.startswith("_"))
    raise RuntimeError(
        f"Service '{type(service).__name__}' does not expose any of the expected methods "
        f"{tuple(method_names)}. Available public methods: {available_methods}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backend maintenance commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create the current PostgreSQL tables.")

    ingest_parser = subparsers.add_parser("ingest-documents", help="Collect articles and persist new documents.")
    ingest_parser.add_argument("--sources-file", type=Path, help="Optional override for the sources.yaml file.")

    ingest_retrieval_parser = subparsers.add_parser(
        "ingest-retrieval-units",
        help="Rebuild current retrieval chunks from SQL/Markdown and, by default, sync the Milvus replica.",
    )
    ingest_retrieval_parser.add_argument(
        "--skip-replica-sync",
        action="store_true",
        help="Refresh SQL retrieval_unit_ref rows only and skip Milvus replica upsert.",
    )

    search_parser = subparsers.add_parser(
        "search-retrieval-units",
        help="Search the current llama-index core plus gateway fallback path over the text retrieval replica.",
    )
    search_parser.add_argument("query", help="Query text used for retrieval.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of retrieval results to print.",
    )

    return parser


def _run_init_db() -> int:
    from backend.app.core.database import create_all_tables

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


def _run_ingest_retrieval_units(*, skip_replica_sync: bool = False) -> int:
    from backend.app.service.milvus_service import build_text_retrieval_replica_writer

    service_class = _load_contract_class(
        "backend.app.service.retrieval_ingestion_service",
        "RetrievalIngestionService",
    )
    service_kwargs: dict[str, Any] = {}
    if not skip_replica_sync:
        service_kwargs["writer"] = build_text_retrieval_replica_writer()
    service = service_class(**service_kwargs)
    stats = _invoke_service_method(
        service,
        ("ingest", "ingest_retrieval_units", "ingest_documents"),
    )
    summary = _format_public_fields(stats)
    replica_sync = "enabled" if not skip_replica_sync else "skipped"
    if summary:
        print(f"Retrieval unit ingestion complete. {summary} replica_sync={replica_sync}")
    else:
        print(f"Retrieval unit ingestion complete. replica_sync={replica_sync}")
    return 0


def _run_search_retrieval_units(query: str, *, limit: int) -> int:
    service_class = _load_contract_class(
        "backend.app.service.retrieval_search_service",
        "RetrievalSearchService",
    )
    service = service_class()
    results = list(
        _invoke_service_method(
            service,
            ("search", "search_retrieval_units"),
            query=query,
            limit=limit,
        )
    )
    print(f"Retrieval search complete. query={query!r} limit={limit} results={len(results)}")
    for index, item in enumerate(results, start=1):
        print(f"{index}. {_format_public_fields(item)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        return _run_init_db()
    if args.command == "ingest-documents":
        return _run_ingest_documents(args.sources_file)
    if args.command == "ingest-retrieval-units":
        return _run_ingest_retrieval_units(skip_replica_sync=args.skip_replica_sync)
    if args.command == "search-retrieval-units":
        return _run_search_retrieval_units(args.query, limit=args.limit)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
