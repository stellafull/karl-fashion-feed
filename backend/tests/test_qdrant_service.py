from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime

from qdrant_client import QdrantClient

from backend.app.service.RAG.qdrant_service import QdrantService


os.environ["DENSE_EMBEDDING_DIMENSION"] = "2"
os.environ["QDRANT_URL"] = "http://localhost:6333"


class QdrantServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = QdrantClient(":memory:")
        self.service = QdrantService(client=self.client)

    def test_create_collection_uses_named_dense_and_sparse_vectors(self) -> None:
        collection = self.service.create_collection("kff_retrieval")

        vectors = collection.config.params.vectors
        sparse_vectors = collection.config.params.sparse_vectors

        self.assertIsInstance(vectors, dict)
        self.assertIsInstance(sparse_vectors, dict)
        self.assertIn(self.service.DENSE_VECTOR_NAME, vectors)
        self.assertIn(self.service.SPARSE_VECTOR_NAME, sparse_vectors)
        self.assertEqual(vectors[self.service.DENSE_VECTOR_NAME].size, 2)

    def test_insert_data_rejects_existing_retrieval_unit_id(self) -> None:
        self.service.insert_data("kff_retrieval", [self._text_record()])

        with self.assertRaisesRegex(ValueError, "insert_data found existing"):
            self.service.insert_data("kff_retrieval", [self._text_record()])

    def test_update_data_rejects_missing_records(self) -> None:
        with self.assertRaisesRegex(ValueError, "update_data requires existing"):
            self.service.update_data("kff_retrieval", [self._text_record()])

    def test_update_data_upserts_when_record_exists(self) -> None:
        self.service.insert_data("kff_retrieval", [self._text_record()])
        updated_record = {
            **self._text_record(),
            "content": "updated content",
            "tags_json": ["tag-b"],
        }

        updated = self.service.update_data("kff_retrieval", [updated_record])

        self.assertEqual(updated, 1)
        stored = self.client.scroll(
            collection_name="kff_retrieval",
            limit=10,
            with_payload=True,
            with_vectors=True,
        )[0][0]
        self.assertEqual(stored.payload["retrieval_unit_id"], "text:article-1:0")
        self.assertEqual(stored.payload["content"], "updated content")
        self.assertEqual(stored.payload["tags_json"], ["tag-b"])
        self.assertEqual(stored.payload["ingested_at"], "2026-03-17T00:00:00Z")

    def test_upsert_data_writes_image_record(self) -> None:
        upserted = self.service.upsert_data("kff_retrieval", [self._image_record()])

        self.assertEqual(upserted, 1)
        stored = self.client.scroll(
            collection_name="kff_retrieval",
            limit=10,
            with_payload=True,
            with_vectors=True,
        )[0][0]
        self.assertEqual(stored.payload["retrieval_unit_id"], "image:image-1")
        self.assertEqual(stored.payload["article_image_id"], "image-1")
        self.assertEqual(stored.payload["chunk_index"], None)
        self.assertEqual(stored.payload["ingested_at"], "2026-03-17T00:00:00Z")

    def test_search_dense_returns_filtered_points(self) -> None:
        self.service.upsert_data(
            "kff_retrieval",
            [
                self._text_record(),
                self._image_record(),
            ],
        )

        results = self.service.search_dense(
            "kff_retrieval",
            [1.0, 0.0],
            limit=5,
            filters=self.service.build_metadata_filter(modality="text"),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].payload["retrieval_unit_id"], "text:article-1:0")

    def test_search_sparse_returns_expected_keyword_match(self) -> None:
        self.service.upsert_data(
            "kff_retrieval",
            [
                self._text_record(),
                {
                    **self._text_record(),
                    "retrieval_unit_id": "text:article-2:0",
                    "article_id": "article-2",
                    "content": "second content",
                    "sparse_vector": {9: 1.0},
                    "dense_vector": [0.0, 1.0],
                    "category": "culture",
                    "tags_json": ["tag-b"],
                    "brands_json": ["brand-b"],
                },
            ],
        )

        results = self.service.search_sparse(
            "kff_retrieval",
            {9: 1.0},
            limit=5,
            filters=self.service.build_metadata_filter(
                modality="text",
                categories=["culture"],
            ),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].payload["retrieval_unit_id"], "text:article-2:0")

    def test_search_hybrid_supports_time_range_filter(self) -> None:
        self.service.upsert_data(
            "kff_retrieval",
            [
                self._text_record(),
                {
                    **self._text_record(),
                    "retrieval_unit_id": "text:article-2:0",
                    "article_id": "article-2",
                    "content": "newer content",
                    "dense_vector": [1.0, 0.0],
                    "sparse_vector": {1: 1.0},
                    "ingested_at": datetime(2026, 3, 18, 0, 0, 0, tzinfo=UTC),
                },
            ],
        )

        results = self.service.search_hybrid(
            "kff_retrieval",
            [1.0, 0.0],
            {1: 1.0},
            limit=5,
            filters=self.service.build_metadata_filter(
                modality="text",
                start_at=datetime(2026, 3, 18, 0, 0, 0, tzinfo=UTC),
            ),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].payload["retrieval_unit_id"], "text:article-2:0")

    def test_search_hybrid_supports_combined_brands_and_time_range_filters(self) -> None:
        self.service.upsert_data(
            "kff_retrieval",
            [
                self._text_record(),
                {
                    **self._text_record(),
                    "retrieval_unit_id": "text:article-2:0",
                    "article_id": "article-2",
                    "brands_json": ["brand-b"],
                    "ingested_at": datetime(2026, 3, 18, 0, 0, 0, tzinfo=UTC),
                },
                {
                    **self._text_record(),
                    "retrieval_unit_id": "text:article-3:0",
                    "article_id": "article-3",
                    "brands_json": ["brand-b"],
                    "ingested_at": datetime(2026, 3, 16, 0, 0, 0, tzinfo=UTC),
                },
            ],
        )

        results = self.service.search_hybrid(
            "kff_retrieval",
            [1.0, 0.0],
            {1: 1.0},
            limit=5,
            filters=self.service.build_metadata_filter(
                modality="text",
                brands=["brand-b"],
                start_at=datetime(2026, 3, 17, 0, 0, 0, tzinfo=UTC),
                end_at=datetime(2026, 3, 19, 0, 0, 0, tzinfo=UTC),
            ),
        )

        self.assertEqual([point.payload["retrieval_unit_id"] for point in results], ["text:article-2:0"])

    def _text_record(self) -> dict[str, object]:
        return {
            "retrieval_unit_id": "text:article-1:0",
            "article_id": "article-1",
            "article_image_id": None,
            "content": "content",
            "chunk_index": 0,
            "modality": "text",
            "source_name": "source-a",
            "category": "fashion",
            "tags_json": ["tag-a"],
            "brands_json": ["brand-a"],
            "ingested_at": datetime(2026, 3, 17, 0, 0, 0, tzinfo=UTC),
            "dense_vector": [0.1, 0.2],
            "sparse_vector": {1: 0.5},
        }

    def _image_record(self) -> dict[str, object]:
        return {
            "retrieval_unit_id": "image:image-1",
            "article_id": "article-1",
            "article_image_id": "image-1",
            "content": "image content",
            "chunk_index": None,
            "modality": "image",
            "source_name": "source-a",
            "category": "fashion",
            "tags_json": ["tag-a"],
            "brands_json": ["brand-a"],
            "ingested_at": datetime(2026, 3, 17, 0, 0, 0, tzinfo=UTC),
            "dense_vector": [0.3, 0.4],
            "sparse_vector": {2: 0.8},
        }


if __name__ == "__main__":
    unittest.main()
