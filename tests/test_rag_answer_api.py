"""Targeted tests for the single-entry RAG answer API."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

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


class _FakeAnswerQueryService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, query_plan: QueryPlan, *, request_images=None) -> QueryResult:
        self.calls.append(query_plan.text_query or "")
        return QueryResult(query_plan=query_plan)


class _ToolCallingResearchAgent:
    def __init__(self, tools: list[object], tool_calls: list[tuple[str, dict[str, object]]]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._tool_calls = tool_calls
        self.call_log: list[dict[str, object]] = []

    async def ainvoke(self, payload: dict[str, object], config: dict[str, object] | None = None) -> dict[str, object]:
        self.call_log.append({"payload": payload, "config": config})
        for tool_name, tool_input in self._tool_calls:
            await self._tools[tool_name].ainvoke(tool_input)
        return {"messages": [AIMessage(content="research complete")]}


class _FakeSynthesisAgent:
    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        return {"messages": [AIMessage(content="final answer")]}

    async def astream_events(self, payload: dict[str, object], **kwargs: object):
        del payload, kwargs
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": AIMessageChunk(content="final answer")},
        }


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
        serialized = str(tools.build_langchain_tools())
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

    async def test_answer_uses_three_iteration_recursion_limit(self) -> None:
        research_agent = _ToolCallingResearchAgent([], [])
        service = RagAnswerService(
            tools_factory=lambda request_context: RagTools(
                request_context=request_context,
                query_service=_FakeAnswerQueryService(),
                web_search_service=FakeWebSearchService(),
            ),
            research_agent_factory=lambda rag_tools: research_agent,
            synthesis_agent=_FakeSynthesisAgent(),
        )
        response = await service.answer(
            request=RagQueryRequest(query="show me dresses", filters=QueryFilters(), limit=5),
            request_context=RagRequestContext(filters=QueryFilters(), limit=5),
        )

        self.assertEqual(response.answer, "final answer")
        self.assertEqual(research_agent.call_log[0]["config"], {"recursion_limit": 7})

    async def test_answer_stream_uses_three_iteration_recursion_limit(self) -> None:
        research_agent = _ToolCallingResearchAgent([], [])
        service = RagAnswerService(
            tools_factory=lambda request_context: RagTools(
                request_context=request_context,
                query_service=_FakeAnswerQueryService(),
                web_search_service=FakeWebSearchService(),
            ),
            research_agent_factory=lambda rag_tools: research_agent,
            synthesis_agent=_FakeSynthesisAgent(),
        )

        deltas: list[str] = []

        async def on_delta(delta: str) -> None:
            deltas.append(delta)

        response = await service.answer_stream(
            request=RagQueryRequest(query="show me dresses", filters=QueryFilters(), limit=5),
            request_context=RagRequestContext(filters=QueryFilters(), limit=5),
            on_delta=on_delta,
        )

        self.assertEqual(response.answer, "final answer")
        self.assertEqual(deltas, ["final answer"])
        self.assertEqual(research_agent.call_log[0]["config"], {"recursion_limit": 7})


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
