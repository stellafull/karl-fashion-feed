import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.schema.retrieval import DEFAULT_TEXT_OUTPUT_FIELDS  # noqa: E402
from backend.app.service.milvus_service import (  # noqa: E402
    DefaultMilvusGateway,
    TEXT_RETRIEVAL_COLLECTION_NAME,
    build_text_retrieval_replica_writer,
)


class FakeGateway:
    def __init__(self, *, query_results_by_filter=None):
        self.query_results_by_filter = {
            key: [dict(record) for record in value]
            for key, value in (query_results_by_filter or {}).items()
        }
        self.query_calls = []
        self.upsert_calls = []

    def upsert_records(self, *, collection_name, records):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "records": [dict(record) for record in records],
            }
        )

    def query_records(self, *, collection_name, filter_expression=None, output_fields=None):
        self.query_calls.append(
            {
                "collection_name": collection_name,
                "filter_expression": filter_expression,
                "output_fields": tuple(output_fields or ()),
            }
        )
        return [
            dict(record)
            for record in self.query_results_by_filter.get(filter_expression, [])
        ]


class UpsertOnlyGateway:
    def __init__(self):
        self.upsert_calls = []

    def upsert_records(self, *, collection_name, records):
        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "records": [dict(record) for record in records],
            }
        )


class FakeMilvusClient:
    def __init__(self, records):
        self.records = [dict(record) for record in records]
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(dict(kwargs))
        return [dict(record) for record in self.records]


class MilvusServiceTests(unittest.TestCase):
    def test_build_text_retrieval_replica_writer_upserts_without_query_capability(self):
        gateway = UpsertOnlyGateway()
        writer = build_text_retrieval_replica_writer(gateway)

        writer(
            [
                {
                    "unit_id": "unit_001",
                    "article_id": "article_001",
                    "source_id": "source-a",
                    "chunk_index": 0,
                    "text_content": "  Tailored\ncoat demand keeps rising.  ",
                    "source_url": "https://example.com/story",
                    "title": "  Tailored Coat Outlook  ",
                }
            ]
        )

        self.assertEqual(len(gateway.upsert_calls), 1)
        call = gateway.upsert_calls[0]
        self.assertEqual(call["collection_name"], TEXT_RETRIEVAL_COLLECTION_NAME)
        self.assertEqual(call["records"][0]["text_content"], "Tailored coat demand keeps rising.")
        self.assertEqual(call["records"][0]["title"], "Tailored Coat Outlook")

    def test_replica_writer_marks_stale_active_records_inactive_when_chunks_shrink(self):
        filter_expression = (
            'unit_type == "text_chunk" and is_active == true '
            'and article_id == "article_001"'
        )
        gateway = FakeGateway(
            query_results_by_filter={
                filter_expression: [
                    {
                        "unit_id": "unit_001",
                        "article_id": "article_001",
                        "source_id": "source-a",
                        "unit_type": "text_chunk",
                        "chunk_index": 0,
                        "text_content": "Current chunk still belongs to SQL truth.",
                        "source_url": "https://example.com/story",
                        "title": "Tailored Coat Outlook",
                        "is_active": True,
                    },
                    {
                        "unit_id": "unit_002",
                        "article_id": "article_001",
                        "source_id": "source-a",
                        "unit_type": "text_chunk",
                        "chunk_index": 1,
                        "text_content": "Stale trailing chunk should be deactivated.",
                        "source_url": "https://example.com/story",
                        "title": "Tailored Coat Outlook",
                        "metadata": {"section": "tailoring"},
                        "is_active": True,
                    },
                ]
            }
        )
        writer = build_text_retrieval_replica_writer(gateway)

        writer(
            [
                {
                    "unit_id": "unit_001",
                    "article_id": "article_001",
                    "source_id": "source-a",
                    "chunk_index": 0,
                    "text_content": "Current chunk still belongs to SQL truth.",
                    "source_url": "https://example.com/story",
                    "title": "Tailored Coat Outlook",
                }
            ],
            ["article_001"],
        )

        self.assertEqual(len(gateway.query_calls), 1)
        self.assertEqual(gateway.query_calls[0]["filter_expression"], filter_expression)
        self.assertEqual(len(gateway.upsert_calls), 1)
        records = gateway.upsert_calls[0]["records"]
        self.assertEqual([record["unit_id"] for record in records], ["unit_001", "unit_002"])
        self.assertTrue(records[0]["is_active"])
        self.assertFalse(records[1]["is_active"])
        self.assertEqual(records[1]["text_content"], "Stale trailing chunk should be deactivated.")
        self.assertEqual(records[1]["metadata"], {"section": "tailoring"})

    def test_default_gateway_search_text_records_uses_query_aware_lexical_ranking(self):
        client = FakeMilvusClient(
            [
                {
                    "unit_id": "unit_002",
                    "article_id": "article_002",
                    "source_id": "source-a",
                    "unit_type": "text_chunk",
                    "chunk_index": 1,
                    "title": "Sneaker memo",
                    "text_content": "Sportswear keeps moving, but this is not about coats.",
                    "source_url": "https://example.com/b",
                    "is_active": True,
                },
                {
                    "unit_id": "unit_001",
                    "article_id": "article_001",
                    "source_id": "source-a",
                    "unit_type": "text_chunk",
                    "chunk_index": 0,
                    "title": "Tailored coat outlook",
                    "text_content": "Tailored coats keep gaining share in the luxury market.",
                    "source_url": "https://example.com/a",
                    "is_active": True,
                },
            ]
        )
        gateway = DefaultMilvusGateway(client=client)

        results = gateway.search_text_records(
            collection_name=TEXT_RETRIEVAL_COLLECTION_NAME,
            query_text="tailored coats",
            limit=1,
            filter_expression='unit_type == "text_chunk" and is_active == true',
            output_fields=DEFAULT_TEXT_OUTPUT_FIELDS,
        )

        self.assertEqual([item["unit_id"] for item in results], ["unit_001"])
        self.assertGreater(results[0]["score"], 0.0)
        self.assertEqual(
            client.query_calls,
            [
                {
                    "collection_name": TEXT_RETRIEVAL_COLLECTION_NAME,
                    "filter": 'unit_type == "text_chunk" and is_active == true',
                    "output_fields": list(DEFAULT_TEXT_OUTPUT_FIELDS),
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
