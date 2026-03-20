"""Qdrant vector database service."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from qdrant_client import QdrantClient
from qdrant_client.http import models


QDRANT_POINT_NAMESPACE = UUID("6a73df27-6260-47a2-b66e-0d2f8605bc60")


class QdrantService:
    """Qdrant vector database service."""

    FIELD_NAMES = (
        "retrieval_unit_id",
        "article_id",
        "article_image_id",
        "content",
        "chunk_index",
        "modality",
        "source_name",
        "category",
        "tags_json",
        "brands_json",
        "ingested_at",
        "dense_vector",
        "sparse_vector",
    )
    DENSE_VECTOR_NAME = "dense_vector"
    SPARSE_VECTOR_NAME = "sparse_vector"
    DENSE_VECTOR_PARAMS = models.VectorParams(
        size=1,
        distance=models.Distance.COSINE,
        hnsw_config=models.HnswConfigDiff(m=32, ef_construct=200),
    )
    SPARSE_VECTOR_PARAMS = models.SparseVectorParams(
        index=models.SparseIndexParams(full_scan_threshold=0),
    )
    PAYLOAD_INDEX_SCHEMAS = {
        "retrieval_unit_id": models.PayloadSchemaType.KEYWORD,
        "article_id": models.PayloadSchemaType.KEYWORD,
        "article_image_id": models.PayloadSchemaType.KEYWORD,
        "chunk_index": models.PayloadSchemaType.INTEGER,
        "modality": models.PayloadSchemaType.KEYWORD,
        "source_name": models.PayloadSchemaType.KEYWORD,
        "category": models.PayloadSchemaType.KEYWORD,
        "tags_json": models.PayloadSchemaType.KEYWORD,
        "brands_json": models.PayloadSchemaType.KEYWORD,
        "ingested_at": models.PayloadSchemaType.DATETIME,
    }

    def __init__(self, *, client: QdrantClient | None = None) -> None:
        self.url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = os.getenv("QDRANT_API_KEY", "")
        self.vector_dim = int(os.getenv("DENSE_EMBEDDING_DIMENSION", "2560"))
        self._client = client or QdrantClient(
            url=self.url,
            api_key=self.api_key or None,
        )

    def create_collection(self, collection_name: str) -> models.CollectionInfo:
        """Create the shared retrieval collection and ensure its schema exists."""
        if not self._client.collection_exists(collection_name):
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    self.DENSE_VECTOR_NAME: self._build_dense_vector_params(),
                },
                sparse_vectors_config={
                    self.SPARSE_VECTOR_NAME: self.SPARSE_VECTOR_PARAMS,
                },
            )

        collection_info = self._client.get_collection(collection_name)
        self._validate_collection_schema(collection_info)
        self._ensure_payload_indexes(collection_name)
        return collection_info

    def insert_data(self, collection_name: str, records: list[dict[str, Any]]) -> int:
        """Insert new retrieval units into Qdrant."""
        if not records:
            return 0

        self._validate_unique_retrieval_unit_ids(records)
        self.create_collection(collection_name)
        retrieval_unit_ids = [str(record["retrieval_unit_id"]) for record in records]
        existing_ids = self._fetch_existing_ids(collection_name, retrieval_unit_ids)
        if existing_ids:
            raise ValueError(
                f"insert_data found existing retrieval_unit_id values: {sorted(existing_ids)}"
            )

        self._client.upsert(
            collection_name=collection_name,
            points=self._build_points(records),
            wait=True,
        )
        return len(records)

    def update_data(self, collection_name: str, records: list[dict[str, Any]]) -> int:
        """Update existing retrieval units in Qdrant."""
        if not records:
            return 0

        self._validate_unique_retrieval_unit_ids(records)
        self.create_collection(collection_name)
        retrieval_unit_ids = [str(record["retrieval_unit_id"]) for record in records]
        existing_ids = self._fetch_existing_ids(collection_name, retrieval_unit_ids)
        missing_ids = sorted(set(retrieval_unit_ids) - existing_ids)
        if missing_ids:
            raise ValueError(
                f"update_data requires existing retrieval_unit_id values: {missing_ids}"
            )

        self._client.upsert(
            collection_name=collection_name,
            points=self._build_points(records),
            wait=True,
        )
        return len(records)

    def upsert_data(self, collection_name: str, records: list[dict[str, Any]]) -> int:
        """Upsert retrieval units into Qdrant."""
        if not records:
            return 0

        self._validate_unique_retrieval_unit_ids(records)
        self.create_collection(collection_name)
        self._client.upsert(
            collection_name=collection_name,
            points=self._build_points(records),
            wait=True,
        )
        return len(records)

    def search_dense(
        self,
        collection_name: str,
        query_vector: list[float],
        *,
        limit: int,
        filters: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        """Run dense retrieval against the shared collection."""
        response = self._client.query_points(
            collection_name=collection_name,
            query=list(query_vector),
            using=self.DENSE_VECTOR_NAME,
            query_filter=filters,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return list(response.points)

    def search_sparse(
        self,
        collection_name: str,
        query_sparse_vector: dict[int, float],
        *,
        limit: int,
        filters: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        """Run sparse retrieval against the shared collection."""
        response = self._client.query_points(
            collection_name=collection_name,
            query=self._build_sparse_vector(query_sparse_vector),
            using=self.SPARSE_VECTOR_NAME,
            query_filter=filters,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return list(response.points)

    def search_hybrid(
        self,
        collection_name: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        *,
        limit: int,
        filters: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        """Run dense+sparse hybrid retrieval fused by Qdrant RRF."""
        response = self._client.query_points(
            collection_name=collection_name,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            prefetch=[
                models.Prefetch(
                    query=list(dense_vector),
                    using=self.DENSE_VECTOR_NAME,
                    limit=limit,
                    filter=filters,
                ),
                models.Prefetch(
                    query=self._build_sparse_vector(sparse_vector),
                    using=self.SPARSE_VECTOR_NAME,
                    limit=limit,
                    filter=filters,
                ),
            ],
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return list(response.points)

    def build_metadata_filter(
        self,
        *,
        modality: str,
        source_names: list[str] | None = None,
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        brands: list[str] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> models.Filter:
        """Build one shared-collection metadata filter."""
        must_conditions: list[models.Condition] = [
            models.FieldCondition(
                key="modality",
                match=models.MatchAny(any=[modality]),
            )
        ]
        self._append_match_any(must_conditions, "source_name", source_names)
        self._append_match_any(must_conditions, "category", categories)
        self._append_match_any(must_conditions, "tags_json", tags)
        self._append_match_any(must_conditions, "brands_json", brands)
        if start_at is not None or end_at is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="ingested_at",
                    range=models.DatetimeRange(
                        gte=self._normalize_datetime(start_at) if start_at is not None else None,
                        lt=self._normalize_datetime(end_at) if end_at is not None else None,
                    ),
                )
            )
        return models.Filter(must=must_conditions)

    def _build_dense_vector_params(self) -> models.VectorParams:
        return models.VectorParams(
            size=self.vector_dim,
            distance=self.DENSE_VECTOR_PARAMS.distance,
            hnsw_config=self.DENSE_VECTOR_PARAMS.hnsw_config,
        )

    def _validate_collection_schema(self, collection_info: models.CollectionInfo) -> None:
        vectors = collection_info.config.params.vectors
        sparse_vectors = collection_info.config.params.sparse_vectors
        dense_params = vectors.get(self.DENSE_VECTOR_NAME) if isinstance(vectors, dict) else None
        sparse_params = (
            sparse_vectors.get(self.SPARSE_VECTOR_NAME)
            if isinstance(sparse_vectors, dict)
            else None
        )
        if dense_params is None:
            raise ValueError(
                f"qdrant collection missing dense vector: {self.DENSE_VECTOR_NAME}"
            )
        if dense_params.size != self.vector_dim:
            raise ValueError(
                "qdrant dense vector dimension mismatch: "
                f"expected {self.vector_dim}, got {dense_params.size}"
            )
        if dense_params.distance != models.Distance.COSINE:
            raise ValueError(
                "qdrant dense vector distance mismatch: "
                f"expected {models.Distance.COSINE}, got {dense_params.distance}"
            )
        if sparse_params is None:
            raise ValueError(
                f"qdrant collection missing sparse vector: {self.SPARSE_VECTOR_NAME}"
            )

    def _ensure_payload_indexes(self, collection_name: str) -> None:
        for field_name, field_schema in self.PAYLOAD_INDEX_SCHEMAS.items():
            self._client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )

    def _validate_unique_retrieval_unit_ids(self, records: list[dict[str, Any]]) -> None:
        retrieval_unit_ids = [str(record["retrieval_unit_id"]) for record in records]
        if len(retrieval_unit_ids) != len(set(retrieval_unit_ids)):
            raise ValueError("qdrant records contain duplicate retrieval_unit_id values")

    def _build_points(self, records: list[dict[str, Any]]) -> list[models.PointStruct]:
        points: list[models.PointStruct] = []
        for record in records:
            missing_fields = [
                field_name for field_name in self.FIELD_NAMES if field_name not in record
            ]
            if missing_fields:
                raise ValueError(f"qdrant record missing fields: {missing_fields}")

            retrieval_unit_id = str(record["retrieval_unit_id"])
            points.append(
                models.PointStruct(
                    id=self._build_point_id(retrieval_unit_id),
                    vector={
                        self.DENSE_VECTOR_NAME: list(record["dense_vector"]),
                        self.SPARSE_VECTOR_NAME: self._build_sparse_vector(record["sparse_vector"]),
                    },
                    payload={
                        "retrieval_unit_id": retrieval_unit_id,
                        "article_id": str(record["article_id"]),
                        "article_image_id": record["article_image_id"],
                        "content": str(record["content"]),
                        "chunk_index": record["chunk_index"],
                        "modality": str(record["modality"]),
                        "source_name": str(record["source_name"]),
                        "category": str(record["category"]),
                        "tags_json": list(record["tags_json"]),
                        "brands_json": list(record["brands_json"]),
                        "ingested_at": self._normalize_datetime(record["ingested_at"]),
                    },
                )
            )
        return points

    def _build_sparse_vector(self, value: Any) -> models.SparseVector:
        if not isinstance(value, dict):
            raise ValueError("qdrant sparse_vector must be a dict[int, float]")

        sorted_items = sorted((int(index), float(weight)) for index, weight in value.items())
        return models.SparseVector(
            indices=[index for index, _ in sorted_items],
            values=[weight for _, weight in sorted_items],
        )

    def _append_match_any(
        self,
        must_conditions: list[models.Condition],
        field_name: str,
        values: list[str] | None,
    ) -> None:
        normalized_values = [value.strip() for value in values or [] if value and value.strip()]
        if not normalized_values:
            return
        must_conditions.append(
            models.FieldCondition(
                key=field_name,
                match=models.MatchAny(any=normalized_values),
            )
        )

    def _fetch_existing_ids(
        self,
        collection_name: str,
        retrieval_unit_ids: list[str],
    ) -> set[str]:
        if not retrieval_unit_ids:
            return set()

        records = self._client.retrieve(
            collection_name=collection_name,
            ids=[self._build_point_id(retrieval_unit_id) for retrieval_unit_id in retrieval_unit_ids],
            with_payload=["retrieval_unit_id"],
            with_vectors=False,
        )
        return {
            str(record.payload["retrieval_unit_id"])
            for record in records
            if isinstance(record.payload, dict) and "retrieval_unit_id" in record.payload
        }

    def _build_point_id(self, retrieval_unit_id: str) -> str:
        return str(uuid5(QDRANT_POINT_NAMESPACE, retrieval_unit_id))

    def _normalize_datetime(self, value: Any) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError("qdrant ingested_at must be a datetime")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
