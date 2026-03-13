import dataclasses
import sys
import unittest
from pathlib import Path

from llama_index.core import VectorStoreIndex
from llama_index.core.vector_stores.types import BasePydanticVectorStore
from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.schema.retrieval import RetrievalIngestionStats, TextRetrievalUnit  # noqa: E402
from backend.app.service.milvus_service import TEXT_RETRIEVAL_COLLECTION_NAME  # noqa: E402
from backend.app.service.retrieval_search_service import (  # noqa: E402
    DEFAULT_TEXT_OUTPUT_FIELDS,
    RetrievalSearchService,
    SearchResultItem,
)


class FakeMilvusGateway:
    def __init__(self, *, search_results=None, query_results=None):
        self.search_results = [dict(item) for item in search_results or []]
        self.query_results = [dict(item) for item in query_results or []]
        self.search_calls = []
        self.query_calls = []
        self.upsert_calls = []

    def search_text_records(
        self,
        *,
        collection_name,
        query_text,
        limit,
        filter_expression=None,
        output_fields=None,
    ):
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "query_text": query_text,
                "limit": limit,
                "filter_expression": filter_expression,
                "output_fields": tuple(output_fields or ()),
            }
        )
        return [dict(item) for item in self.search_results]

    def query_records(self, *, collection_name, filter_expression=None, output_fields=None):
        self.query_calls.append(
            {
                "collection_name": collection_name,
                "filter_expression": filter_expression,
                "output_fields": tuple(output_fields or ()),
            }
        )
        return [dict(item) for item in self.query_results]

    def upsert_records(self, *, collection_name, records):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "records": [dict(record) for record in records],
            }
        )


class QueryOnlyMilvusGateway:
    def __init__(self, records=None):
        self.records = [dict(item) for item in records or []]
        self.query_calls = []
        self.upsert_calls = []

    def query_records(self, *, collection_name, filter_expression=None, output_fields=None):
        self.query_calls.append(
            {
                "collection_name": collection_name,
                "filter_expression": filter_expression,
                "output_fields": tuple(output_fields or ()),
            }
        )
        return [dict(item) for item in self.records]

    def upsert_records(self, *, collection_name, records):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "records": [dict(record) for record in records],
            }
        )


class RetrievalSearchServiceTests(unittest.TestCase):
    def test_service_builds_llamaindex_core_index_and_vector_store(self):
        service = RetrievalSearchService(milvus_gateway=FakeMilvusGateway())

        self.assertIsInstance(service._index, VectorStoreIndex)
        self.assertIsInstance(service._vector_store, BasePydanticVectorStore)

    def test_retrieval_schema_models_are_pydantic_models(self):
        unit = TextRetrievalUnit.model_validate(
            {
                "unit_id": "unit_001",
                "article_id": "article_001",
                "source_id": "source-a",
                "chunk_index": "2",
                "text_content": "First line. Second line.",
                "source_url": "https://example.com/story",
                "title": "Fashion Brief",
                "domain": "example.com",
            }
        )
        stats = RetrievalIngestionStats(
            document_count=1,
            skipped_count=0,
            chunk_count=2,
            existing_count=0,
            inserted_count=2,
        )

        self.assertIsInstance(unit, BaseModel)
        self.assertIsInstance(stats, BaseModel)
        self.assertEqual(unit.text, "First line. Second line.")
        self.assertEqual(unit.title, "Fashion Brief")
        self.assertEqual(unit.chunk_index, 2)

    def test_search_result_item_model_accepts_canonical_payloads(self):
        item = SearchResultItem.model_validate(
            {
                "score": 0.91,
                "unit_id": "unit_001",
                "article_id": "article_001",
                "source_id": "source-a",
                "unit_type": "text_chunk",
                "chunk_index": 3,
                "title": "Fashion Brief",
                "text_content": "A concise retrieval chunk.",
                "source_url": "https://example.com/story",
                "metadata": {"domain": "example.com"},
            }
        )

        self.assertEqual(item.unit_id, "unit_001")
        self.assertEqual(item.text_content, "A concise retrieval chunk.")
        self.assertEqual(item.score, 0.91)

    def test_search_result_item_model_rejects_transport_payloads(self):
        with self.assertRaises(ValidationError):
            SearchResultItem.model_validate(
                {
                    "distance": 0.91,
                    "entity": {
                        "unit_id": "unit_001",
                        "article_id": "article_001",
                        "source_id": "source-a",
                        "unit_type": "text_chunk",
                        "chunk_index": 3,
                        "title": "Fashion Brief",
                        "text_content": "A concise retrieval chunk.",
                        "source_url": "https://example.com/story",
                    },
                }
            )

    def test_search_routes_nested_entity_payloads_into_search_result_items(self):
        gateway = FakeMilvusGateway(
            search_results=[
                {
                    "distance": 0.91,
                    "entity": {
                        "unit_id": "unit_001",
                        "article_id": "article_001",
                        "source_id": "source-a",
                        "unit_type": "text_chunk",
                        "chunk_index": 3,
                        "title": "Fashion Brief",
                        "text_content": "A concise retrieval chunk.",
                        "source_url": "https://example.com/story",
                        "metadata": {"domain": "example.com"},
                        "language": "en",
                    },
                }
            ]
        )
        service = RetrievalSearchService(milvus_gateway=gateway)
        results = service.search("concise retrieval", limit=1)

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertIsInstance(item, SearchResultItem)
        self.assertEqual(item.unit_id, "unit_001")
        self.assertEqual(item.chunk_index, 3)
        self.assertEqual(item.score, 0.91)
        self.assertEqual(item.metadata["domain"], "example.com")
        self.assertEqual(item.metadata["language"], "en")

    def test_search_service_promotes_canonical_fields_from_nested_payloads(self):
        gateway = FakeMilvusGateway(
            search_results=[
                {
                    "score": 0.67,
                    "entity": {
                        "unit_id": "unit_002",
                        "article_id": "article_002",
                        "source_id": "source-b",
                        "unit_type": "text_chunk",
                        "chunk_index": 1,
                        "title": "Retail pulse",
                        "text": "Structured metadata should stay aligned.",
                        "source_url": "https://example.com/retail",
                        "domain": "canonical.example.com",
                        "metadata": {
                            "domain": "stale.example.com",
                            "language": "en",
                        },
                    },
                }
            ]
        )
        service = RetrievalSearchService(milvus_gateway=gateway)

        results = service.search("structured metadata", limit=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].metadata["domain"], "canonical.example.com")
        self.assertEqual(results[0].metadata["language"], "en")

    def test_search_routes_through_llamaindex_vector_store_query(self):
        gateway = FakeMilvusGateway(
            search_results=[
                {
                    "score": 0.78,
                    "entity": {
                        "unit_id": "unit_001",
                        "article_id": "article_001",
                        "source_id": "source-a",
                        "unit_type": "text_chunk",
                        "chunk_index": 0,
                        "title": "Tailored coat outlook",
                        "text_content": "Tailored coats keep gaining share.",
                        "source_url": "https://example.com/story",
                    },
                }
            ]
        )
        service = RetrievalSearchService(milvus_gateway=gateway)

        results = service.search("tailored coats", limit=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].unit_id, "unit_001")
        self.assertEqual(results[0].score, 0.78)
        self.assertEqual(
            gateway.search_calls,
            [
                {
                    "collection_name": TEXT_RETRIEVAL_COLLECTION_NAME,
                    "query_text": "tailored coats",
                    "limit": 3,
                    "filter_expression": 'unit_type == "text_chunk" and is_active == true',
                    "output_fields": DEFAULT_TEXT_OUTPUT_FIELDS,
                }
            ],
        )

    def test_search_applies_metadata_filters_via_llamaindex_query(self):
        gateway = FakeMilvusGateway()
        service = RetrievalSearchService(milvus_gateway=gateway)

        service.search_text(
            "tailored coats",
            limit=2,
            article_id='article-"001',
            source_id="source-a",
        )

        self.assertEqual(len(gateway.search_calls), 1)
        self.assertEqual(
            gateway.search_calls[0]["filter_expression"],
            'unit_type == "text_chunk" and is_active == true and article_id == "article-\\"001" and source_id == "source-a"',
        )

    def test_upsert_text_units_normalizes_payload_before_writing(self):
        @dataclasses.dataclass
        class FakeTextUnit:
            unit_id: str
            article_id: str
            source_id: str
            chunk_index: int
            text: str
            source_url: str
            title: str

        gateway = FakeMilvusGateway()
        service = RetrievalSearchService(milvus_gateway=gateway)

        written_count = service.upsert_text_units(
            [
                FakeTextUnit(
                    unit_id="unit_001",
                    article_id="article_001",
                    source_id="business-of-fashion",
                    chunk_index=0,
                    text="  First line.\nSecond line.  ",
                    source_url="https://example.com/story",
                    title="  Fashion Market Briefing  ",
                )
            ]
        )

        self.assertEqual(written_count, 1)
        self.assertEqual(len(gateway.upsert_calls), 1)
        call = gateway.upsert_calls[0]
        self.assertEqual(call["collection_name"], TEXT_RETRIEVAL_COLLECTION_NAME)
        record = call["records"][0]
        self.assertEqual(record["unit_type"], "text_chunk")
        self.assertEqual(record["text_content"], "First line. Second line.")
        self.assertEqual(record["title"], "Fashion Market Briefing")
        self.assertEqual(record["tags"], [])
        self.assertEqual(record["metadata"], {})
        self.assertTrue(record["is_active"])
        self.assertIsInstance(record["created_at_ts"], int)
        self.assertIsInstance(record["updated_at_ts"], int)

    def test_upsert_text_units_normalizes_prebuilt_schema_models_before_writing(self):
        gateway = FakeMilvusGateway()
        service = RetrievalSearchService(milvus_gateway=gateway)

        written_count = service.upsert_text_units(
            [
                TextRetrievalUnit(
                    unit_id="unit_010",
                    article_id="article_010",
                    source_id="source-z",
                    chunk_index=0,
                    text="  First line.\nSecond line.  ",
                    source_url="https://example.com/story",
                    title="  Fashion Market Briefing  ",
                )
            ]
        )

        self.assertEqual(written_count, 1)
        self.assertEqual(len(gateway.upsert_calls), 1)
        record = gateway.upsert_calls[0]["records"][0]
        self.assertEqual(record["text_content"], "First line. Second line.")
        self.assertEqual(record["title"], "Fashion Market Briefing")

    def test_search_falls_back_to_gateway_query_records_when_search_api_is_unavailable(self):
        gateway = QueryOnlyMilvusGateway(
            records=[
                {
                    "unit_id": "unit_001",
                    "article_id": "article_001",
                    "source_id": "source-a",
                    "unit_type": "text_chunk",
                    "chunk_index": 0,
                    "title": "Luxury market watch",
                    "text_content": "Fashion market momentum improves in Europe.",
                    "source_url": "https://example.com/a",
                    "is_active": True,
                },
                {
                    "unit_id": "unit_002",
                    "article_id": "article_002",
                    "source_id": "source-a",
                    "unit_type": "text_chunk",
                    "chunk_index": 1,
                    "title": "Retail memo",
                    "text_content": "A market update without the core angle.",
                    "source_url": "https://example.com/b",
                    "is_active": True,
                },
            ]
        )
        service = RetrievalSearchService(milvus_gateway=gateway)

        results = service.search("fashion market", limit=1)

        self.assertEqual([item.unit_id for item in results], ["unit_001"])
        self.assertEqual(len(gateway.query_calls), 1)
        self.assertEqual(
            gateway.query_calls[0]["filter_expression"],
            'unit_type == "text_chunk" and is_active == true',
        )

    def test_search_fallback_uses_schema_text_aliases(self):
        gateway = QueryOnlyMilvusGateway(
            records=[
                {
                    "unit_id": "unit_003",
                    "article_id": "article_003",
                    "source_id": "source-c",
                    "unit_type": "text_chunk",
                    "chunk_index": 0,
                    "title": "Market note",
                    "text": "Fashion market momentum improves in Europe.",
                    "source_url": "https://example.com/c",
                    "is_active": True,
                }
            ]
        )
        service = RetrievalSearchService(milvus_gateway=gateway)

        results = service.search("fashion market", limit=1)

        self.assertEqual([item.unit_id for item in results], ["unit_003"])


if __name__ == "__main__":
    unittest.main()
