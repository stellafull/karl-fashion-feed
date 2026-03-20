from __future__ import annotations

import unittest
from datetime import UTC, datetime

from backend.app.schemas.rag_query import QueryResult
from backend.app.service.RAG.rag_tools import RagTools


class StubQueryService:
    def __init__(self) -> None:
        self.last_query_plan = None

    def execute(self, query_plan):
        self.last_query_plan = query_plan
        return QueryResult(query_plan=query_plan)


class RagToolsTest(unittest.TestCase):
    def test_search_fashion_articles_builds_text_only_plan(self) -> None:
        query_service = StubQueryService()
        tools = RagTools(query_service=query_service)

        result = tools.search_fashion_articles(
            query="  structured coat  ",
            brands=["Dior"],
            categories=["秀场"],
            start_at="2026-03-18T00:00:00Z",
            end_at="2026-03-19T00:00:00Z",
            include_images=False,
            limit=5,
        )

        self.assertEqual(result.query_plan.plan_type, "text_only")
        self.assertEqual(result.query_plan.text_query, "structured coat")
        self.assertEqual(result.query_plan.filters.brands, ["Dior"])
        self.assertEqual(result.query_plan.filters.categories, ["秀场"])
        assert result.query_plan.filters.time_range is not None
        self.assertEqual(
            result.query_plan.filters.time_range.start_at,
            datetime(2026, 3, 18, 0, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(result.query_plan.limit, 5)

    def test_search_fashion_articles_builds_fusion_plan(self) -> None:
        query_service = StubQueryService()
        tools = RagTools(query_service=query_service)

        result = tools.search_fashion_articles(
            query="lady gaga",
            include_images=True,
            limit=5,
        )

        self.assertEqual(result.query_plan.plan_type, "fusion")
        self.assertEqual(result.query_plan.text_query, "lady gaga")
        self.assertEqual(result.query_plan.output_goal, "reference_lookup")

    def test_search_fashion_images_builds_text_to_image_plan(self) -> None:
        query_service = StubQueryService()
        tools = RagTools(query_service=query_service)

        result = tools.search_fashion_images(
            text_query="  red coat  ",
            brands=["Prada"],
            categories=["秀场"],
            limit=3,
        )

        self.assertEqual(result.query_plan.plan_type, "image_only")
        self.assertEqual(result.query_plan.text_query, "red coat")
        self.assertIsNone(result.query_plan.image_query)
        self.assertEqual(result.query_plan.output_goal, "inspiration")
        self.assertEqual(result.query_plan.filters.brands, ["Prada"])
        self.assertEqual(result.query_plan.filters.categories, ["秀场"])

    def test_search_fashion_images_builds_image_to_image_plan(self) -> None:
        query_service = StubQueryService()
        tools = RagTools(query_service=query_service)

        result = tools.search_fashion_images(
            image_url="https://example.com/look.jpg",
            brands=["Prada"],
            limit=3,
        )

        self.assertEqual(result.query_plan.plan_type, "image_only")
        self.assertEqual(result.query_plan.image_query, "https://example.com/look.jpg")
        self.assertIsNone(result.query_plan.text_query)
        self.assertEqual(result.query_plan.output_goal, "similarity_search")
        self.assertEqual(result.query_plan.filters.brands, ["Prada"])

    def test_search_fashion_images_rejects_both_inputs(self) -> None:
        tools = RagTools(query_service=StubQueryService())

        with self.assertRaisesRegex(ValueError, "exactly one of text_query or image_url"):
            tools.search_fashion_images(
                text_query="red coat",
                image_url="https://example.com/look.jpg",
            )

    def test_search_fashion_images_rejects_empty_inputs(self) -> None:
        tools = RagTools(query_service=StubQueryService())

        with self.assertRaisesRegex(ValueError, "requires text_query or image_url"):
            tools.search_fashion_images(text_query="   ")

    def test_search_fashion_articles_rejects_empty_query(self) -> None:
        tools = RagTools(query_service=StubQueryService())

        with self.assertRaisesRegex(ValueError, "requires a non-empty query"):
            tools.search_fashion_articles(query="   ")

    def test_search_fashion_articles_rejects_invalid_time_range(self) -> None:
        tools = RagTools(query_service=StubQueryService())

        with self.assertRaisesRegex(ValueError, "time_range.start_at must be earlier"):
            tools.search_fashion_articles(
                query="structured coat",
                start_at="2026-03-19T00:00:00Z",
                end_at="2026-03-18T00:00:00Z",
            )

    def test_execute_tool_dispatches_search_fashion_images(self) -> None:
        query_service = StubQueryService()
        tools = RagTools(query_service=query_service)

        result = tools.execute_tool(
            "search_fashion_images",
            {
                "text_query": "red coat",
                "brands": ["Prada"],
                "categories": ["秀场"],
                "start_at": "2026-03-18T00:00:00Z",
                "end_at": "2026-03-19T00:00:00Z",
                "limit": 4,
            },
        )

        self.assertEqual(result.query_plan.plan_type, "image_only")
        self.assertEqual(result.query_plan.text_query, "red coat")
        self.assertEqual(result.query_plan.filters.brands, ["Prada"])
        self.assertEqual(result.query_plan.filters.categories, ["秀场"])
        self.assertEqual(result.query_plan.limit, 4)

    def test_execute_tool_rejects_unknown_tool(self) -> None:
        tools = RagTools(query_service=StubQueryService())

        with self.assertRaisesRegex(ValueError, "unsupported tool"):
            tools.execute_tool("web_search", {"query": "latest fashion"})


if __name__ == "__main__":
    unittest.main()
