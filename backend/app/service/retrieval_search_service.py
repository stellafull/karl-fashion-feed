"""Text retrieval service backed by llama-index core vector-store abstractions."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from llama_index.core import VectorStoreIndex
from llama_index.core.embeddings.mock_embed_model import MockEmbedding
from llama_index.core.schema import BaseNode, NodeWithScore, TextNode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
    VectorStoreQueryResult,
)
from pydantic import ConfigDict, PrivateAttr, ValidationError

from backend.app.schema.retrieval import (
    DEFAULT_TEXT_OUTPUT_FIELDS,
    SearchResultItem,
    TEXT_CHUNK_UNIT_TYPE,
    TextRetrievalUnit,
)
from backend.app.service.milvus_service import (
    DefaultMilvusGateway,
    MilvusGateway,
    TEXT_RETRIEVAL_COLLECTION_NAME,
)

_NON_WORD_PATTERN = re.compile(r"[^\w\s]+", re.UNICODE)
_TEXT_SEARCH_EMBED_MODEL = MockEmbedding(
    embed_dim=1,
    model_name="retrieval-search-service-text-search",
)

_UNIT_METADATA_FIELDS = (
    "author",
    "domain",
    "language",
    "published_at_ts",
    "is_active",
    "tags",
    "created_at_ts",
    "updated_at_ts",
)
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


class LlamaIndexMilvusTextVectorStore(BasePydanticVectorStore):
    """Thin adapter that keeps retrieval orchestration inside llama-index core."""

    stores_text: bool = True
    is_embedding_query: bool = False
    collection_name: str = TEXT_RETRIEVAL_COLLECTION_NAME
    output_fields: tuple[str, ...] = DEFAULT_TEXT_OUTPUT_FIELDS

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _milvus_gateway: MilvusGateway = PrivateAttr()

    def __init__(
        self,
        *,
        milvus_gateway: MilvusGateway,
        collection_name: str = TEXT_RETRIEVAL_COLLECTION_NAME,
        output_fields: Sequence[str] = DEFAULT_TEXT_OUTPUT_FIELDS,
    ) -> None:
        super().__init__(
            collection_name=collection_name,
            output_fields=tuple(output_fields),
        )
        self._milvus_gateway = milvus_gateway

    @property
    def client(self) -> MilvusGateway:
        return self._milvus_gateway

    def add(self, nodes: Sequence[BaseNode], **kwargs: Any) -> list[str]:
        del kwargs
        records = [_record_from_text_node(node) for node in nodes]
        if not records:
            return []

        self._milvus_gateway.upsert_records(
            collection_name=self.collection_name,
            records=records,
        )
        return [record["unit_id"] for record in records]

    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        del kwargs
        raw_results = self._query_raw_results(query)
        nodes: list[TextNode] = []
        similarities: list[float] = []
        ids: list[str] = []

        for raw_result in raw_results:
            try:
                search_result_payload = _search_result_payload_from_raw_result(raw_result)
            except ValueError:
                continue
            node = _text_node_from_search_result_payload(search_result_payload)
            nodes.append(node)
            ids.append(node.node_id)
            similarities.append(float(search_result_payload["score"]))

        return VectorStoreQueryResult(nodes=nodes, similarities=similarities, ids=ids)

    def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        del ref_doc_id, delete_kwargs
        raise NotImplementedError("Delete is not implemented for the current Milvus gateway.")

    def _query_raw_results(self, query: VectorStoreQuery) -> list[dict[str, Any]]:
        query_text = _normalize_whitespace(query.query_str)
        filter_expression = _build_filter_expression(filters=query.filters)
        output_fields = list(query.output_fields or self.output_fields)

        search_records = getattr(self._milvus_gateway, "search_text_records", None)
        if callable(search_records):
            results = search_records(
                collection_name=self.collection_name,
                query_text=query_text,
                limit=query.similarity_top_k,
                filter_expression=filter_expression,
                output_fields=output_fields,
            )
            return [dict(result) for result in results or []]

        candidates = self._milvus_gateway.query_records(
            collection_name=self.collection_name,
            filter_expression=filter_expression,
            output_fields=output_fields,
        )
        return _rank_query_candidates(
            candidates,
            query_text=query_text,
            limit=query.similarity_top_k,
        )


class RetrievalSearchService:
    def __init__(
        self,
        milvus_gateway: MilvusGateway | None = None,
        *,
        collection_name: str = TEXT_RETRIEVAL_COLLECTION_NAME,
    ) -> None:
        self._vector_store = LlamaIndexMilvusTextVectorStore(
            milvus_gateway=milvus_gateway or DefaultMilvusGateway(),
            collection_name=collection_name,
            output_fields=DEFAULT_TEXT_OUTPUT_FIELDS,
        )
        self._index = VectorStoreIndex.from_vector_store(
            self._vector_store,
            embed_model=_TEXT_SEARCH_EMBED_MODEL,
        )

    def search(self, query: str, limit: int = 5) -> list[SearchResultItem]:
        return self.search_text(query_text=query, limit=limit)

    def search_retrieval_units(self, query: str, limit: int = 5) -> list[SearchResultItem]:
        return self.search(query=query, limit=limit)

    def search_text(
        self,
        query_text: str,
        *,
        limit: int = 5,
        article_id: str | None = None,
        source_id: str | None = None,
    ) -> list[SearchResultItem]:
        normalized_query = _normalize_whitespace(query_text)
        if limit <= 0 or not _normalize_search_text(normalized_query):
            return []

        retriever = self._index.as_retriever(
            similarity_top_k=limit,
            vector_store_query_mode=VectorStoreQueryMode.TEXT_SEARCH,
            filters=_build_metadata_filters(
                article_id=article_id,
                source_id=source_id,
            ),
        )
        items: list[SearchResultItem] = []
        for node_with_score in retriever.retrieve(normalized_query):
            if not isinstance(node_with_score.node, TextNode):
                continue
            try:
                items.append(_search_result_from_node(node_with_score))
            except ValueError:
                continue
        return items[:limit]

    def upsert_text_units(self, units: Iterable[Any]) -> int:
        nodes = [_text_node_from_unit(unit) for unit in units]
        if not nodes:
            return 0

        self._vector_store.add(nodes)
        return len(nodes)


def _text_node_from_unit(unit: Any) -> TextNode:
    text_unit = _validate_text_unit(unit)
    return TextNode(
        id_=text_unit.unit_id,
        text=text_unit.text,
        metadata=_node_metadata_from_unit(text_unit),
    )


def _record_from_text_node(node: BaseNode) -> dict[str, Any]:
    return _record_from_unit(_text_unit_from_text_node(node))


def _text_node_from_search_result_payload(payload: Mapping[str, Any]) -> TextNode:
    return _text_node_from_unit(_text_unit_from_result_payload(payload))


def _search_result_from_node(node_with_score: NodeWithScore) -> SearchResultItem:
    text_unit = _text_unit_from_text_node(node_with_score.node)
    return SearchResultItem.model_validate(
        _search_result_payload_from_unit(
            text_unit,
            score=float(node_with_score.score or 0.0),
        )
    )


def _rank_query_candidates(
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
        try:
            unit = _text_unit_from_result_payload(raw_candidate)
        except ValueError:
            continue

        score = _score_candidate(
            normalized_query,
            title=unit.title,
            text_content=unit.text,
        )
        if score <= 0 or not _is_searchable_unit(unit):
            continue

        ranked_results.append({**_record_from_unit(unit), "score": score})

    ranked_results.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("article_id") or ""),
            _candidate_chunk_index(item),
            str(item.get("unit_id") or ""),
        )
    )
    return ranked_results[:limit]


def _build_metadata_filters(
    *,
    article_id: str | None,
    source_id: str | None,
) -> MetadataFilters | None:
    filters: list[MetadataFilter] = []
    if article_id:
        filters.append(
            MetadataFilter(
                key="article_id",
                value=article_id,
                operator=FilterOperator.EQ,
            )
        )
    if source_id:
        filters.append(
            MetadataFilter(
                key="source_id",
                value=source_id,
                operator=FilterOperator.EQ,
            )
        )
    if not filters:
        return None
    return MetadataFilters(filters=filters, condition=FilterCondition.AND)


def _build_filter_expression(*, filters: MetadataFilters | None) -> str:
    conditions = [f'unit_type == "{TEXT_CHUNK_UNIT_TYPE}"', "is_active == true"]
    if filters is not None:
        conditions.extend(_metadata_filters_to_expressions(filters))
    return " and ".join(conditions)


def _metadata_filters_to_expressions(filters: MetadataFilters) -> list[str]:
    expressions: list[str] = []
    joiner = f" {filters.condition.value} "
    for filter_item in filters.filters:
        if isinstance(filter_item, MetadataFilters):
            nested = _metadata_filters_to_expressions(filter_item)
            if nested:
                expressions.append(f"({joiner.join(nested)})")
            continue

        if filter_item.operator != FilterOperator.EQ:
            raise ValueError(
                "Retrieval search only supports exact-match metadata filters with the current gateway."
            )

        value = filter_item.value
        if value is None:
            expressions.append(f"{filter_item.key} == null")
            continue
        if isinstance(value, str):
            rendered_value = f'"{_escape_filter_value(value)}"'
        elif isinstance(value, bool):
            rendered_value = "true" if value else "false"
        else:
            rendered_value = str(value)
        expressions.append(f"{filter_item.key} == {rendered_value}")
    return expressions


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _score_candidate(query_text: str, *, title: Any, text_content: Any) -> float:
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


def _text_unit_from_result_payload(payload: Mapping[str, Any]) -> TextRetrievalUnit:
    return _text_unit_from_flat_result_payload(_flatten_result_payload(payload))


def _text_unit_from_flat_result_payload(payload: Mapping[str, Any]) -> TextRetrievalUnit:
    normalized = dict(payload)
    metadata = dict(normalized.get("metadata") or {}) if isinstance(normalized.get("metadata"), Mapping) else {}

    normalized.pop("score", None)
    normalized.pop("distance", None)
    for key in list(normalized.keys()):
        if key in TextRetrievalUnit.model_fields or key in _TEXT_INPUT_ALIASES:
            continue
        metadata[key] = normalized.pop(key)

    for field_name in _UNIT_METADATA_FIELDS:
        if normalized.get(field_name) in (None, "") and field_name in metadata:
            normalized[field_name] = metadata[field_name]

    normalized["metadata"] = metadata
    return _validate_text_unit(normalized)


def _search_result_payload_from_raw_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    flattened = _flatten_result_payload(payload)
    text_unit = _text_unit_from_flat_result_payload(flattened)
    return _search_result_payload_from_unit(
        text_unit,
        score=_coerce_result_score(payload, flattened_payload=flattened),
    )


def _text_unit_from_text_node(node: BaseNode) -> TextRetrievalUnit:
    if not isinstance(node, TextNode):
        raise ValueError("Retrieval units must resolve to TextNode instances.")

    return _validate_text_unit(
        {
            "unit_id": node.node_id,
            "text": node.text,
            **dict(node.metadata or {}),
        }
    )


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
    return record


def _node_metadata_from_unit(unit: TextRetrievalUnit) -> dict[str, Any]:
    metadata = _result_metadata_from_unit(unit)
    metadata["article_id"] = unit.article_id
    metadata["source_id"] = unit.source_id
    metadata["unit_type"] = unit.unit_type
    metadata["chunk_index"] = unit.chunk_index
    metadata["source_url"] = unit.source_url
    if unit.title is not None:
        metadata["title"] = unit.title
    return metadata


def _result_metadata_from_unit(unit: TextRetrievalUnit) -> dict[str, Any]:
    metadata = dict(unit.metadata)
    for field_name in _UNIT_METADATA_FIELDS:
        value = getattr(unit, field_name)
        if value is not None:
            metadata[field_name] = list(value) if field_name == "tags" else value
    return metadata


def _search_result_payload_from_unit(
    unit: TextRetrievalUnit,
    *,
    score: float,
) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "article_id": unit.article_id,
        "source_id": unit.source_id,
        "unit_type": unit.unit_type,
        "chunk_index": unit.chunk_index,
        "title": unit.title,
        "text_content": unit.text,
        "source_url": unit.source_url,
        "score": score,
        "metadata": _result_metadata_from_unit(unit),
    }


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
    else:
        normalized["tags"] = _normalize_tags(metadata.get("tags"))

    for field_name in ("chunk_index", "published_at_ts", "created_at_ts", "updated_at_ts"):
        if normalized.get(field_name) in (None, ""):
            normalized.pop(field_name, None)
            continue
        normalized[field_name] = int(normalized[field_name])

    normalized["metadata"] = metadata
    return normalized


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


def _flatten_result_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    flattened = (
        dict(payload.get("entity") or {})
        if isinstance(payload.get("entity"), Mapping)
        else {}
    )
    for key, value in payload.items():
        if key != "entity":
            flattened[key] = value
    return flattened


def _coerce_result_score(
    payload: Mapping[str, Any],
    *,
    flattened_payload: Mapping[str, Any] | None = None,
) -> float:
    flattened = dict(flattened_payload) if flattened_payload is not None else _flatten_result_payload(payload)
    for candidate in (payload, flattened):
        for field_name in ("score", "distance"):
            value = candidate.get(field_name)
            if value is not None:
                return float(value)
    return 0.0


def _is_searchable_unit(unit: TextRetrievalUnit) -> bool:
    if not unit.is_active:
        return False
    if unit.unit_type != TEXT_CHUNK_UNIT_TYPE:
        return False
    return True


def _candidate_chunk_index(candidate: Mapping[str, Any]) -> int:
    value = candidate.get("chunk_index")
    if value is None or value == "":
        return -1
    return int(value)
