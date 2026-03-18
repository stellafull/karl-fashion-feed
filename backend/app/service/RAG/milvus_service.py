"""Milvus vector database service."""

from __future__ import annotations

import os
from typing import Any

from pymilvus import (  # noqa: E402
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)


class MilvusService:
    """Milvus vector database service."""

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
    DENSE_INDEX_NAME = "dense_vector_hnsw"
    SPARSE_INDEX_NAME = "sparse_vector_inverted"
    DENSE_INDEX_PARAMS = {
        "metric_type": "COSINE",
        "index_type": "HNSW",
        "params": {"M": 32, "efConstruction": 200},
    }
    SPARSE_INDEX_PARAMS = {
        "metric_type": "IP",
        "index_type": "SPARSE_INVERTED_INDEX",
        "params": {"drop_ratio_build": 0.0},
    }

    def __init__(self) -> None:
        self.uri = os.getenv("MILVUS_URI", "http://localhost:19530")
        self.token = os.getenv("MILVUS_TOKEN", "")
        self.vector_dim = int(os.getenv("DENSE_EMBEDDING_DIMENSION", "2560"))

    def _connect(self) -> None:
        """Connect to Milvus."""
        connections.connect(alias="default", uri=self.uri, token=self.token)

    def create_collection(self, collection_name: str) -> Collection:
        """Create the shared retrieval collection and repair its indexes."""
        self._connect()

        if utility.has_collection(collection_name):
            collection = Collection(collection_name)
            self._rebuild_indexes(collection)
            collection.load()
            return collection

        fields = [
            FieldSchema(name="retrieval_unit_id", dtype=DataType.VARCHAR, is_primary=True, max_length=128,),
            FieldSchema(name="article_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="article_image_id", dtype=DataType.VARCHAR, max_length=64, nullable=True,),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="chunk_index", dtype=DataType.INT64, nullable=True),
            FieldSchema(name="modality", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="source_name", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="tags_json", dtype=DataType.JSON, nullable=True),
            FieldSchema(name="brands_json", dtype=DataType.JSON, nullable=True),
            FieldSchema(name="ingested_at", dtype=DataType.INT64),
            FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=self.vector_dim),
            FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
        ]
        schema = CollectionSchema(
            fields=fields,
            description="Shared collection for article and image retrieval units.",
        )
        collection = Collection(name=collection_name, schema=schema)
        self._rebuild_indexes(collection)
        collection.load()
        return collection

    def insert_data(self, collection_name: str, records: list[dict[str, Any]]) -> int:
        """Insert new retrieval units into Milvus."""
        if not records:
            return 0

        collection = self.create_collection(collection_name)
        payload = self._build_payload(records)
        retrieval_unit_ids = payload[0]
        existing_ids = self._fetch_existing_ids(collection, retrieval_unit_ids)
        if existing_ids:
            raise ValueError(
                f"insert_data found existing retrieval_unit_id values: {sorted(existing_ids)}"
            )

        collection.insert(payload)
        collection.flush()
        return len(records)

    def update_data(self, collection_name: str, records: list[dict[str, Any]]) -> int:
        """Update existing retrieval units in Milvus."""
        if not records:
            return 0

        collection = self.create_collection(collection_name)
        payload = self._build_payload(records)
        retrieval_unit_ids = payload[0]
        existing_ids = self._fetch_existing_ids(collection, retrieval_unit_ids)
        missing_ids = sorted(set(retrieval_unit_ids) - existing_ids)
        if missing_ids:
            raise ValueError(
                f"update_data requires existing retrieval_unit_id values: {missing_ids}"
            )

        collection.upsert(payload)
        collection.flush()
        return len(records)

    def upsert_data(self, collection_name: str, records: list[dict[str, Any]]) -> int:
        """Upsert retrieval units into Milvus."""
        if not records:
            return 0

        collection = self.create_collection(collection_name)
        payload = self._build_payload(records)
        collection.upsert(payload)
        collection.flush()
        return len(records)

    def _rebuild_indexes(self, collection: Collection) -> None:
        if collection.indexes:
            collection.release()

        for index in collection.indexes:
            collection.drop_index(index_name=index.index_name)

        collection.create_index(
            field_name="dense_vector",
            index_params=self.DENSE_INDEX_PARAMS,
            index_name=self.DENSE_INDEX_NAME,
        )
        collection.create_index(
            field_name="sparse_vector",
            index_params=self.SPARSE_INDEX_PARAMS,
            index_name=self.SPARSE_INDEX_NAME,
        )

    def _build_payload(self, records: list[dict[str, Any]]) -> list[list[Any]]:
        for record in records:
            missing_fields = [
                field_name for field_name in self.FIELD_NAMES if field_name not in record
            ]
            if missing_fields:
                raise ValueError(f"milvus record missing fields: {missing_fields}")

        return [
            [record[field_name] for record in records]
            for field_name in self.FIELD_NAMES
        ]

    def _fetch_existing_ids(
        self,
        collection: Collection,
        retrieval_unit_ids: list[str],
    ) -> set[str]:
        if not retrieval_unit_ids:
            return set()

        quoted_ids = ", ".join(self._quote_expr_string(value) for value in retrieval_unit_ids)
        rows = collection.query(
            expr=f"retrieval_unit_id in [{quoted_ids}]",
            output_fields=["retrieval_unit_id"],
        )
        return {
            row["retrieval_unit_id"]
            for row in rows
            if isinstance(row, dict) and "retrieval_unit_id" in row
        }

    def _quote_expr_string(self, value: str) -> str:
        return f'"{value}"'
