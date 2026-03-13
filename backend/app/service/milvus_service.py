"""Milvus client helpers and storage gateway abstractions."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from pydantic import ValidationError

from backend.app.config.milvus import require_milvus_settings
from backend.app.schema.retrieval import TEXT_CHUNK_UNIT_TYPE, TextRetrievalUnit

TEXT_RETRIEVAL_COLLECTION_NAME = "content_text_unit"
IMAGE_RETRIEVAL_COLLECTION_NAME = "content_image_unit"
collection_name = TEXT_RETRIEVAL_COLLECTION_NAME

_NON_WORD_PATTERN = re.compile(r"[^\w\s]+", re.UNICODE)
_TEXT_INPUT_ALIASES = {"text", "text_content", "content_text"}
_TEXT_UNIT_STRING_FIELDS = (
    "unit_id",
    "article_id",
    "source_id",
    "source_url",
    "title",
    "content_version_hash",
    "unit_type",
    "author",
    "domain",
    "language",
)
_REPLICA_SYNC_OUTPUT_FIELDS = (
    "unit_id",
    "article_id",
    "source_id",
    "unit_type",
    "chunk_index",
    "title",
    "text_content",
    "source_url",
    "author",
    "domain",
    "language",
    "published_at_ts",
    "is_active",
    "tags",
    "metadata",
    "content_version_hash",
    "created_at_ts",
    "updated_at_ts",
)


class MilvusGateway(Protocol):
    def upsert_records(
        self,
        *,
        collection_name: str,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        """Persist normalized records into a Milvus collection."""

    def query_records(
        self,
        *,
        collection_name: str,
        filter_expression: str | None = None,
        output_fields: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch candidate records from a Milvus collection."""

    def search_text_records(
        self,
        *,
        collection_name: str,
        query_text: str,
        limit: int,
        filter_expression: str | None = None,
        output_fields: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search text retrieval records from a Milvus collection."""


def build_text_retrieval_replica_writer(
    gateway: MilvusGateway | None = None,
    *,
    collection_name: str = TEXT_RETRIEVAL_COLLECTION_NAME,
) -> Callable[[Sequence[TextRetrievalUnit], Sequence[str] | None], None]:
    milvus_gateway = gateway or DefaultMilvusGateway()

    def write_units(
        units: Sequence[TextRetrievalUnit],
        article_ids: Sequence[str] | None = None,
    ) -> None:
        records = [_record_from_unit(_validate_text_unit(unit)) for unit in units]
        sync_article_ids = _normalize_article_ids(article_ids) or _normalize_article_ids(
            record["article_id"] for record in records
        )
        stale_records = _stale_replica_records(
            gateway=milvus_gateway,
            collection_name=collection_name,
            article_ids=sync_article_ids,
            current_unit_ids={str(record["unit_id"]) for record in records},
        )
        records.extend(stale_records)
        if not records:
            return
        milvus_gateway.upsert_records(
            collection_name=collection_name,
            records=records,
        )

    return write_units


def get_milvus_client():
    from pymilvus import MilvusClient

    settings = require_milvus_settings()
    client_kwargs = {"uri": settings.uri}
    if settings.token:
        client_kwargs["token"] = settings.token
    return MilvusClient(**client_kwargs)


@dataclass(slots=True)
class DefaultMilvusGateway:
    """Runtime gateway that defers Milvus imports until first use.

    The official plugin lives at `llama_index.vector_stores.milvus`, but the
    current environment still lacks `llama-index-vector-stores-milvus`. Until
    that dependency-install step lands, text search uses this metadata-query
    plus application-side lexical-ranking fallback.
    """

    client: Any | None = None

    def upsert_records(
        self,
        *,
        collection_name: str,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        payload = [dict(record) for record in records]
        if not payload:
            return
        self._get_client().upsert(collection_name=collection_name, data=payload)

    def query_records(
        self,
        *,
        collection_name: str,
        filter_expression: str | None = None,
        output_fields: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        query_kwargs: dict[str, Any] = {"collection_name": collection_name}
        if filter_expression:
            query_kwargs["filter"] = filter_expression
        if output_fields is not None:
            query_kwargs["output_fields"] = list(output_fields)
        records = self._get_client().query(**query_kwargs)
        return [dict(record) for record in records or []]

    def search_text_records(
        self,
        *,
        collection_name: str,
        query_text: str,
        limit: int,
        filter_expression: str | None = None,
        output_fields: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        # This is the current fallback path, not native Milvus full-text search.
        candidates = self.query_records(
            collection_name=collection_name,
            filter_expression=filter_expression,
            output_fields=output_fields,
        )
        return _rank_text_candidates(
            candidates,
            query_text=query_text,
            limit=limit,
        )

    def _get_client(self) -> Any:
        if self.client is None:
            self.client = get_milvus_client()
        return self.client


def _rank_text_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    query_text: str,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_query = _normalize_search_text(query_text)
    if limit <= 0 or not normalized_query:
        return []

    ranked_results: list[dict[str, Any]] = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        if not _is_searchable_candidate(candidate):
            continue

        score = _score_text_candidate(
            normalized_query,
            title=candidate.get("title"),
            text_content=candidate.get("text_content") or candidate.get("content_text"),
        )
        if score <= 0:
            continue

        ranked_results.append({**candidate, "score": score})

    ranked_results.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("article_id") or ""),
            _candidate_chunk_index(item),
            str(item.get("unit_id") or ""),
        )
    )
    return ranked_results[:limit]


def _validate_text_unit(value: Any) -> TextRetrievalUnit:
    try:
        raw_payload = (
            dict(value)
            if isinstance(value, Mapping)
            else {
                field_name: getattr(value, field_name)
                for field_name in TextRetrievalUnit.model_fields
                if hasattr(value, field_name)
            }
        )
        return TextRetrievalUnit.model_validate(_normalize_text_unit_payload(raw_payload))
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _record_from_unit(unit: TextRetrievalUnit) -> dict[str, Any]:
    record: dict[str, Any] = {
        "unit_id": unit.unit_id,
        "article_id": unit.article_id,
        "source_id": unit.source_id,
        "unit_type": TEXT_CHUNK_UNIT_TYPE,
        "chunk_index": unit.chunk_index,
        "text_content": unit.text,
        "source_url": unit.source_url,
        "is_active": unit.is_active,
        "tags": list(unit.tags),
        "metadata": dict(unit.metadata),
        "created_at_ts": unit.created_at_ts,
        "updated_at_ts": unit.updated_at_ts,
    }

    optional_fields = {
        "title": unit.title,
        "author": unit.author,
        "domain": unit.domain,
        "language": unit.language,
        "published_at_ts": unit.published_at_ts,
    }
    for field_name, value in optional_fields.items():
        if value is not None:
            record[field_name] = value
    if unit.content_version_hash is not None:
        record["content_version_hash"] = unit.content_version_hash
    return record


def _stale_replica_records(
    *,
    gateway: MilvusGateway,
    collection_name: str,
    article_ids: Sequence[str],
    current_unit_ids: set[str],
) -> list[dict[str, Any]]:
    if not article_ids:
        return []
    query_records = getattr(gateway, "query_records", None)
    if not callable(query_records):
        return []

    stale_records: list[dict[str, Any]] = []
    for article_id in article_ids:
        candidates = query_records(
            collection_name=collection_name,
            filter_expression=_build_active_article_filter_expression(article_id),
            output_fields=_REPLICA_SYNC_OUTPUT_FIELDS,
        )
        for candidate in candidates:
            unit_id = _normalize_whitespace(candidate.get("unit_id"))
            if not unit_id or unit_id in current_unit_ids:
                continue
            stale_records.append(_build_inactive_record(candidate))
    return stale_records


def _normalize_text_unit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    metadata = dict(normalized.get("metadata") or {}) if isinstance(normalized.get("metadata"), Mapping) else {}

    for field_name in _TEXT_UNIT_STRING_FIELDS:
        if field_name in normalized:
            normalized[field_name] = _normalize_whitespace(normalized[field_name])
    for field_name in _TEXT_INPUT_ALIASES:
        if field_name in normalized:
            normalized[field_name] = _normalize_whitespace(normalized[field_name])

    if not normalized.get("unit_type"):
        normalized["unit_type"] = TEXT_CHUNK_UNIT_TYPE

    if "tags" in normalized:
        normalized["tags"] = _normalize_tags(normalized["tags"])

    for field_name in ("chunk_index", "published_at_ts", "created_at_ts", "updated_at_ts"):
        if normalized.get(field_name) in (None, ""):
            normalized.pop(field_name, None)
            continue
        normalized[field_name] = int(normalized[field_name])

    normalized["metadata"] = metadata
    return normalized


def _normalize_article_ids(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()

    normalized_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        article_id = _normalize_whitespace(value)
        if not article_id or article_id in seen:
            continue
        seen.add(article_id)
        normalized_ids.append(article_id)
    return tuple(normalized_ids)


def _build_active_article_filter_expression(article_id: str) -> str:
    escaped_article_id = article_id.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'unit_type == "{TEXT_CHUNK_UNIT_TYPE}" and is_active == true '
        f'and article_id == "{escaped_article_id}"'
    )


def _build_inactive_record(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_text_unit_payload(candidate)
    timestamp = int(time.time())
    record: dict[str, Any] = {
        "unit_id": normalized["unit_id"],
        "article_id": normalized["article_id"],
        "source_id": normalized["source_id"],
        "unit_type": TEXT_CHUNK_UNIT_TYPE,
        "chunk_index": normalized["chunk_index"],
        "text_content": normalized.get("text_content")
        or normalized.get("content_text")
        or normalized.get("text")
        or "",
        "source_url": normalized["source_url"],
        "is_active": False,
        "tags": _normalize_tags(normalized.get("tags")),
        "metadata": dict(normalized.get("metadata") or {}),
        "created_at_ts": int(normalized.get("created_at_ts") or timestamp),
        "updated_at_ts": timestamp,
    }

    optional_fields = (
        "title",
        "author",
        "domain",
        "language",
        "published_at_ts",
        "content_version_hash",
    )
    for field_name in optional_fields:
        value = normalized.get(field_name)
        if value is not None:
            record[field_name] = value
    return record


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = _normalize_whitespace(value)
        return [normalized] if normalized else []
    if not isinstance(value, Sequence):
        normalized = _normalize_whitespace(value)
        return [normalized] if normalized else []
    return [
        normalized
        for item in value
        if (normalized := _normalize_whitespace(item))
    ]


def _score_text_candidate(query_text: str, *, title: Any, text_content: Any) -> float:
    normalized_title = _normalize_search_text(title)
    normalized_text = _normalize_search_text(text_content)
    combined = " ".join(part for part in (normalized_title, normalized_text) if part)
    if not combined:
        return 0.0

    score = 0.0
    if query_text in combined:
        score += 8.0
    for token in query_text.split():
        if token in normalized_title:
            score += 4.0
        if token in normalized_text:
            score += 1.5
    return score


def _normalize_search_text(value: Any) -> str:
    compact = _normalize_whitespace(value).lower()
    if not compact:
        return ""
    return " ".join(_NON_WORD_PATTERN.sub(" ", compact).split())


def _normalize_whitespace(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _is_searchable_candidate(candidate: Mapping[str, Any]) -> bool:
    is_active = candidate.get("is_active")
    if is_active is not None and not _coerce_candidate_bool(is_active):
        return False
    unit_type = str(candidate.get("unit_type") or "").strip() or TEXT_CHUNK_UNIT_TYPE
    if unit_type != TEXT_CHUNK_UNIT_TYPE:
        return False
    return True


def _candidate_chunk_index(candidate: Mapping[str, Any]) -> int:
    value = candidate.get("chunk_index")
    if value in (None, ""):
        return -1
    return int(value)


def _coerce_candidate_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in {"", "0", "false", "no"}:
        return False
    if normalized in {"1", "true", "yes"}:
        return True
    return bool(normalized)
