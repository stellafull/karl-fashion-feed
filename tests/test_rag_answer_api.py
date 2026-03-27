"""Targeted tests for the single-entry RAG answer API."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.app_main import app
from backend.app.router.rag_router import get_rag_answer_service
from backend.app.schemas.rag_api import (
    RagAnswerResponse,
    RagQueryRequest,
    RagRequestContext,
    RequestImageInput,
)
from backend.app.schemas.rag_query import (
    QueryFilters,
    QueryPlan,
    QueryResult,
    REQUEST_IMAGE_REF,
)
from backend.app.service.RAG import query_service as query_module
from backend.app.service.RAG.query_service import QueryService
from backend.app.service.RAG.rag_answer_service import RagAnswerService
from backend.app.service.RAG.rag_tools import RagTools


class FakeQueryService:
    """Capture QueryPlan execution requests."""

    def __init__(self) -> None:
        self.calls: list[tuple[QueryPlan, list[RequestImageInput] | None]] = []

    def execute(
        self,
        query_plan: QueryPlan,
        *,
        request_images: list[RequestImageInput] | None = None,
    ) -> QueryResult:
        self.calls.append((query_plan, request_images))
        return QueryResult(query_plan=query_plan)


class FakeWebSearchService:
    """Return deterministic Brave search results for tests."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    async def search(self, *, query: str, limit: int):
        self.queries.append((query, limit))
        return []


class FakeChatCompletions:
    """Return a predefined sequence of chat completion messages."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("unexpected extra chat.completions.create call")
        return self._responses.pop(0)


class FakeTools:
    """Minimal tools facade for answer-loop tests."""

    def __init__(self, request_context: RagRequestContext) -> None:
        self.request_context = request_context
        self.executed_calls: list[tuple[str, dict[str, object]]] = []

    def build_tool_definitions(self) -> list[dict[str, object]]:
        return [{"type": "function", "function": {"name": "search_fashion_articles"}}]

    async def execute_tool(self, tool_name: str, arguments: dict[str, object]):
        self.executed_calls.append((tool_name, arguments))
        query_plan = QueryPlan(
            plan_type="text_only",
            text_query="query",
            filters=self.request_context.filters,
            limit=self.request_context.limit,
        )
        return QueryResult(query_plan=query_plan)

    @staticmethod
    def serialize_tool_result(result) -> str:
        return "tool-result"


class QueryPlanValidationTests(unittest.TestCase):
    """Validate updated request_image query-plan semantics."""

    def test_fusion_allows_optional_request_image(self) -> None:
        plan = QueryPlan(
            plan_type="fusion",
            text_query="runway references",
            image_query=REQUEST_IMAGE_REF,
        )
        self.assertEqual(plan.image_query, REQUEST_IMAGE_REF)

    def test_image_only_accepts_request_image_without_text(self) -> None:
        plan = QueryPlan(
            plan_type="image_only",
            image_query=REQUEST_IMAGE_REF,
        )
        self.assertEqual(plan.image_query, REQUEST_IMAGE_REF)


class RagToolsTests(unittest.TestCase):
    """Verify request-level constraints stay outside the tool surface."""

    def setUp(self) -> None:
        self.request_image = RequestImageInput(mime_type="image/png", base64_data="aGVsbG8=")
        self.request_context = RagRequestContext(
            filters=QueryFilters(brands=["Dior"]),
            limit=7,
            request_images=[self.request_image],
        )

    def test_tool_definitions_do_not_expose_filters_limit_or_image_base64(self) -> None:
        tools = RagTools(
            request_context=self.request_context,
            query_service=FakeQueryService(),
            web_search_service=FakeWebSearchService(),
        )
        serialized = str(tools.build_tool_definitions())
        self.assertNotIn("filters", serialized)
        self.assertNotIn("limit", serialized)
        self.assertNotIn("base64", serialized)

    def test_search_fashion_images_requires_uploaded_image_for_request_image_ref(self) -> None:
        tools = RagTools(
            request_context=RagRequestContext(filters=QueryFilters(), limit=5),
            query_service=FakeQueryService(),
            web_search_service=FakeWebSearchService(),
        )
        with self.assertRaisesRegex(ValueError, "uploaded request images"):
            tools.search_fashion_images(image_ref="request_image")

    def test_search_fashion_fusion_injects_request_constraints(self) -> None:
        fake_query_service = FakeQueryService()
        tools = RagTools(
            request_context=self.request_context,
            query_service=fake_query_service,
            web_search_service=FakeWebSearchService(),
        )

        result = tools.search_fashion_fusion(query="巴黎时装周", image_ref="request_image")

        self.assertEqual(result.query_plan.plan_type, "fusion")
        [(query_plan, request_images)] = fake_query_service.calls
        self.assertEqual(query_plan.image_query, REQUEST_IMAGE_REF)
        self.assertEqual(query_plan.filters.brands, ["Dior"])
        self.assertEqual(query_plan.limit, 7)
        self.assertEqual(request_images, [self.request_image])


class QueryServiceRequestImageTests(unittest.TestCase):
    """Ensure request_image retrieval uses request-scoped image content."""

    def test_image_lane_uses_request_image_data_url(self) -> None:
        captured_inputs: dict[str, object] = {}

        class FakeQdrantService:
            def search_dense(self, *args, **kwargs):
                return []

            def build_metadata_filter(self, **kwargs):
                return None

        service = QueryService.__new__(QueryService)
        service._markdown_service = None
        service._qdrant_service = FakeQdrantService()
        service._reranker_service = None
        service._collection_name = "kff_retrieval"

        request_image = RequestImageInput(
            mime_type="image/png",
            base64_data="aGVsbG8=",
        )
        query_plan = QueryPlan(
            plan_type="image_only",
            image_query=REQUEST_IMAGE_REF,
            limit=5,
        )

        def fake_generate_dense_embedding(texts, image_inputs=None):
            captured_inputs["texts"] = texts
            captured_inputs["image_inputs"] = image_inputs
            return [[0.1, 0.2]]

        with patch.object(query_module, "generate_dense_embedding", fake_generate_dense_embedding):
            hits = service._execute_image_lane(query_plan, request_images=[request_image])

        self.assertEqual(hits, [])
        self.assertEqual(captured_inputs["texts"], ["image query"])
        self.assertEqual(
            captured_inputs["image_inputs"],
            ["data:image/png;base64,aGVsbG8="],
        )

class RagAnswerServiceTests(unittest.IsolatedAsyncioTestCase):
    """Cover the answer-loop orchestration constraints."""

    async def test_answer_loop_stops_after_three_tool_calls(self) -> None:
        tool_call = lambda index: SimpleNamespace(  # noqa: E731
            id=f"tool-{index}",
            type="function",
            function=SimpleNamespace(
                name="search_fashion_articles",
                arguments='{"query":"dress"}',
            ),
        )
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call(1)]))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call(2)]))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call(3)]))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="final answer", tool_calls=None))]),
        ]
        fake_completions = FakeChatCompletions(responses)
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
        fake_tools_instances: list[FakeTools] = []

        def build_tools(request_context: RagRequestContext) -> FakeTools:
            tools = FakeTools(request_context)
            fake_tools_instances.append(tools)
            return tools

        service = RagAnswerService(client=fake_client, tools_factory=build_tools)
        response = await service.answer(
            request=RagQueryRequest(query="show me dresses", filters=QueryFilters(), limit=5),
            request_context=RagRequestContext(filters=QueryFilters(), limit=5),
        )

        self.assertEqual(response.answer, "final answer")
        [fake_tools] = fake_tools_instances
        self.assertEqual(len(fake_tools.executed_calls), 3)
        self.assertEqual(len(fake_completions.calls), 4)

    async def test_answer_loop_truncates_batched_tool_calls_at_budget(self) -> None:
        """Tool-call overflows should stop at the budget instead of raising."""
        tool_call = lambda index: SimpleNamespace(  # noqa: E731
            id=f"tool-{index}",
            type="function",
            function=SimpleNamespace(
                name="search_fashion_articles",
                arguments='{"query":"dress"}',
            ),
        )
        responses = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call(1), tool_call(2)],
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[tool_call(3), tool_call(4)],
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="final answer", tool_calls=None)
                    )
                ]
            ),
        ]
        fake_completions = FakeChatCompletions(responses)
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
        fake_tools_instances: list[FakeTools] = []

        def build_tools(request_context: RagRequestContext) -> FakeTools:
            tools = FakeTools(request_context)
            fake_tools_instances.append(tools)
            return tools

        service = RagAnswerService(client=fake_client, tools_factory=build_tools)
        response = await service.answer(
            request=RagQueryRequest(query="show me dresses", filters=QueryFilters(), limit=5),
            request_context=RagRequestContext(filters=QueryFilters(), limit=5),
        )

        self.assertEqual(response.answer, "final answer")
        [fake_tools] = fake_tools_instances
        self.assertEqual(len(fake_tools.executed_calls), 3)
        self.assertEqual(len(fake_completions.calls), 3)


class RagRouterTests(unittest.TestCase):
    """Validate request parsing and HTTP error handling."""

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_missing_query_and_image_returns_422(self) -> None:
        client = TestClient(app)
        response = client.post("/api/v1/rag/query", data={"limit": "10"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_time_range_returns_422(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/v1/rag/query",
            data={
                "query": "spring trends",
                "start_at": "2026-03-23T00:00:00Z",
                "end_at": "2026-03-22T00:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_router_passes_request_image_to_service(self) -> None:
        captured_contexts: list[RagRequestContext] = []

        class FakeAnswerService:
            async def answer(self, *, request: RagQueryRequest, request_context: RagRequestContext):
                captured_contexts.append(request_context)
                return RagAnswerResponse(answer="ok")

        app.dependency_overrides[get_rag_answer_service] = lambda: FakeAnswerService()
        client = TestClient(app)
        response = client.post(
            "/api/v1/rag/query",
            data={"query": "red dress"},
            files=[("images", ("dress.png", b"binary-image", "image/png"))],
        )

        self.assertEqual(response.status_code, 200)
        [request_context] = captured_contexts
        self.assertEqual(len(request_context.request_images), 1)
        self.assertEqual(request_context.request_images[0].mime_type, "image/png")


if __name__ == "__main__":
    unittest.main()
