from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import patch

os.environ["MILVUS_URI"] = "http://localhost:19530"

from backend.app.service.RAG.milvus_service import MilvusService


class FakeIndex:
    def __init__(self, index_name: str) -> None:
        self.index_name = index_name


class FakeCollection:
    def __init__(self) -> None:
        self.indexes = [FakeIndex("legacy_dense"), FakeIndex("legacy_sparse")]
        self.drop_index_calls: list[str] = []
        self.create_index_calls: list[tuple[str, dict[str, object], str]] = []
        self.insert_payloads: list[list[list[object]]] = []
        self.upsert_payloads: list[list[list[object]]] = []
        self.query_result: list[dict[str, str]] = []
        self.flush_called = 0
        self.load_called = 0
        self.release_called = 0

    def create_index(
        self,
        field_name: str,
        index_params: dict[str, object],
        index_name: str,
    ) -> None:
        self.create_index_calls.append((field_name, index_params, index_name))

    def drop_index(self, *, index_name: str) -> None:
        self.drop_index_calls.append(index_name)

    def flush(self) -> None:
        self.flush_called += 1

    def insert(self, payload: list[list[object]]) -> None:
        self.insert_payloads.append(payload)

    def load(self) -> None:
        self.load_called += 1

    def query(self, *, expr: str, output_fields: list[str]) -> list[dict[str, str]]:
        self.last_query = (expr, output_fields)
        return self.query_result

    def release(self) -> None:
        self.release_called += 1

    def upsert(self, payload: list[list[object]]) -> None:
        self.upsert_payloads.append(payload)


class MilvusServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MilvusService()

    def test_create_collection_repairs_dense_and_sparse_indexes(self) -> None:
        collection = FakeCollection()

        with patch.object(self.service, "_connect"), patch(
            "backend.app.service.RAG.milvus_service.utility.has_collection",
            return_value=True,
        ), patch(
            "backend.app.service.RAG.milvus_service.Collection",
            return_value=collection,
        ):
            result = self.service.create_collection("kff_retrieval")

        self.assertIs(result, collection)
        self.assertEqual(collection.release_called, 1)
        self.assertEqual(collection.drop_index_calls, ["legacy_dense", "legacy_sparse"])
        self.assertEqual(
            collection.create_index_calls,
            [
                (
                    "dense_vector",
                    self.service.DENSE_INDEX_PARAMS,
                    self.service.DENSE_INDEX_NAME,
                ),
                (
                    "sparse_vector",
                    self.service.SPARSE_INDEX_PARAMS,
                    self.service.SPARSE_INDEX_NAME,
                ),
            ],
        )
        self.assertEqual(collection.load_called, 1)

    def test_insert_data_inserts_full_payload(self) -> None:
        collection = FakeCollection()
        collection.indexes = []

        with patch.object(self.service, "create_collection", return_value=collection):
            inserted = self.service.insert_data("kff_retrieval", [self._text_record()])

        self.assertEqual(inserted, 1)
        self.assertEqual(len(collection.insert_payloads), 1)
        self.assertEqual(
            collection.insert_payloads[0],
            [
                ["text:article-1:0"],
                ["article-1"],
                [None],
                ["content"],
                [0],
                ["text"],
                ["source-a"],
                ["fashion"],
                [["tag-a"]],
                [["brand-a"]],
                [1773820800000],
                [[0.1, 0.2]],
                [{1: 0.5}],
            ],
        )
        self.assertEqual(collection.flush_called, 1)

    def test_update_data_rejects_missing_records(self) -> None:
        collection = FakeCollection()
        collection.indexes = []
        collection.query_result = []

        with patch.object(self.service, "create_collection", return_value=collection):
            with self.assertRaisesRegex(ValueError, "update_data requires existing"):
                self.service.update_data("kff_retrieval", [self._text_record()])

        self.assertEqual(collection.upsert_payloads, [])

    def test_update_data_upserts_when_all_records_exist(self) -> None:
        collection = FakeCollection()
        collection.indexes = []
        collection.query_result = [{"retrieval_unit_id": "text:article-1:0"}]

        with patch.object(self.service, "create_collection", return_value=collection):
            updated = self.service.update_data("kff_retrieval", [self._text_record()])

        self.assertEqual(updated, 1)
        self.assertEqual(len(collection.upsert_payloads), 1)
        self.assertEqual(collection.flush_called, 1)

    def test_upsert_data_writes_image_record(self) -> None:
        collection = FakeCollection()
        collection.indexes = []

        with patch.object(self.service, "create_collection", return_value=collection):
            upserted = self.service.upsert_data("kff_retrieval", [self._image_record()])

        self.assertEqual(upserted, 1)
        self.assertEqual(
            collection.upsert_payloads[0],
            [
                ["image:image-1"],
                ["article-1"],
                ["image-1"],
                ["image content"],
                [None],
                ["image"],
                ["source-a"],
                ["fashion"],
                [["tag-a"]],
                [["brand-a"]],
                [1773820800000],
                [[0.3, 0.4]],
                [{2: 0.8}],
            ],
        )
        self.assertEqual(collection.flush_called, 1)

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
            "ingested_at": 1773820800000,
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
            "ingested_at": 1773820800000,
            "dense_vector": [0.3, 0.4],
            "sparse_vector": {2: 0.8},
        }


if __name__ == "__main__":
    unittest.main()
