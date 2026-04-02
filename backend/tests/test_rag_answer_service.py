from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, AIMessageChunk

from backend.app.schemas.rag_api import (
    RagAnswerResponse,
    RagQueryRequest,
    RagRequestContext,
    RequestImageInput,
    WebSearchResult,
)
from backend.app.schemas.rag_query import (
    ArticlePackage,
    CitationLocator,
    GroundingText,
    QueryFilters,
    QueryPlan,
    QueryResult,
    RetrievalHit,
)
from backend.app.service.RAG.rag_answer_service import RagAnswerService
from backend.app.service.RAG.rag_tools import RagTools


def _build_text_hit(
    *,
    retrieval_unit_id: str,
    article_id: str,
    chunk_index: int,
    score: float,
    canonical_url: str = "https://example.com/articles/a1",
) -> RetrievalHit:
    return RetrievalHit(
        retrieval_unit_id=retrieval_unit_id,
        modality="text",
        article_id=article_id,
        content=f"text:{retrieval_unit_id}",
        score=score,
        citation_locator=CitationLocator(
            article_id=article_id,
            article_image_id=None,
            chunk_index=chunk_index,
            source_name="Vogue",
            canonical_url=canonical_url,
        ),
        title="秀场趋势",
        summary="本季轮廓摘要",
    )


def _build_image_hit(
    *,
    retrieval_unit_id: str,
    article_id: str,
    article_image_id: str,
    score: float,
    canonical_url: str = "https://example.com/articles/a1",
) -> RetrievalHit:
    return RetrievalHit(
        retrieval_unit_id=retrieval_unit_id,
        modality="image",
        article_id=article_id,
        article_image_id=article_image_id,
        content=f"image:{retrieval_unit_id}",
        score=score,
        citation_locator=CitationLocator(
            article_id=article_id,
            article_image_id=article_image_id,
            chunk_index=None,
            source_name="Vogue",
            canonical_url=canonical_url,
        ),
        grounding_texts=[
            GroundingText(
                chunk_index=3,
                content="look context",
                citation_locator=CitationLocator(
                    article_id=article_id,
                    article_image_id=None,
                    chunk_index=3,
                    source_name="Vogue",
                    canonical_url=canonical_url,
                ),
            )
        ],
        title="秀场趋势",
        summary="本季轮廓摘要",
    )


def _build_article_result() -> QueryResult:
    text_hit = _build_text_hit(
        retrieval_unit_id="text:a1:0",
        article_id="article-1",
        chunk_index=0,
        score=0.91,
    )
    return QueryResult(
        query_plan=QueryPlan(
            plan_type="text_only",
            text_query="silhouette",
            filters=QueryFilters(),
            output_goal="reference_lookup",
            limit=5,
        ),
        text_results=[text_hit],
        packages=[
            ArticlePackage(
                article_id="article-1",
                title="秀场趋势",
                summary="本季轮廓摘要",
                text_hits=[text_hit],
                image_hits=[],
                combined_score=0.91,
            )
        ],
        citation_locators=[text_hit.citation_locator],
    )


def _build_image_result() -> QueryResult:
    image_hit = _build_image_hit(
        retrieval_unit_id="image:image-1",
        article_id="article-1",
        article_image_id="image-1",
        score=0.87,
    )
    return QueryResult(
        query_plan=QueryPlan(
            plan_type="image_only",
            text_query="silhouette detail",
            filters=QueryFilters(),
            output_goal="inspiration",
            limit=5,
        ),
        image_results=[image_hit],
        packages=[
            ArticlePackage(
                article_id="article-1",
                title="秀场趋势",
                summary="本季轮廓摘要",
                text_hits=[],
                image_hits=[image_hit],
                combined_score=0.87,
            )
        ],
        citation_locators=[image_hit.citation_locator],
    )


class _FakeQueryService:
    def __init__(self, responses: dict[tuple[str, str | None, str | None], QueryResult]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str | None, str | None]] = []

    def execute(self, query_plan: QueryPlan, *, request_images: list[object]) -> QueryResult:
        key = (query_plan.plan_type, query_plan.text_query, query_plan.image_query)
        self.calls.append(key)
        return self._responses[key]


class _FakeWebSearchService:
    def __init__(self, responses: dict[str, list[WebSearchResult]]) -> None:
        self._responses = responses

    async def search(self, *, query: str, limit: int) -> list[WebSearchResult]:
        return self._responses[query][:limit]


class _ToolCallingResearchAgent:
    def __init__(
        self,
        tools: list[object],
        tool_calls: list[tuple[str, dict[str, Any]]],
        *,
        call_log: list[dict[str, object]] | None = None,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._tool_calls = list(tool_calls)
        self._call_log = call_log if call_log is not None else []

    async def ainvoke(self, payload: dict[str, object], config: dict[str, object] | None = None) -> dict[str, object]:
        self._call_log.append({"payload": payload, "config": config})
        for tool_name, tool_input in self._tool_calls:
            await self._tools[tool_name].ainvoke(tool_input)
        return {"messages": [AIMessage(content="research complete")]}


class _FakeSynthesisAgent:
    def __init__(self, *, answer: str = "", stream_chunks: list[str] | None = None) -> None:
        self._answer = answer
        self._stream_chunks = list(stream_chunks or [])
        self.invoke_payloads: list[dict[str, object]] = []
        self.stream_payloads: list[dict[str, object]] = []

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.invoke_payloads.append(payload)
        return {"messages": [AIMessage(content=self._answer)]}

    async def astream_events(self, payload: dict[str, object], **kwargs: object):
        self.stream_payloads.append({"payload": payload, "kwargs": kwargs})
        for chunk in self._stream_chunks:
            yield {
                "event": "on_chat_model_stream",
                "data": {
                    "chunk": AIMessageChunk(content=chunk),
                },
            }


class RagAnswerServiceTest(unittest.TestCase):
    def test_langchain_tools_collect_query_and_web_results(self) -> None:
        article_result = _build_article_result()
        query_service = _FakeQueryService(
            {
                ("text_only", "silhouette", None): article_result,
            }
        )
        web_results = [
            WebSearchResult(
                title="Latest runway note",
                url="https://news.example.com/latest",
                snippet="Fresh signal",
            )
        ]
        web_search_service = _FakeWebSearchService({"latest runway": web_results})
        rag_tools = RagTools(
            request_context=RagRequestContext(limit=5),
            query_service=query_service,
            web_search_service=web_search_service,
        )
        tools = {tool.name: tool for tool in rag_tools.build_langchain_tools()}

        article_payload = tools["search_fashion_articles"].invoke({"query": "silhouette"})
        web_payload = asyncio.run(tools["search_web"].ainvoke({"query": "latest runway"}))

        collected_rag_results, collected_web_results = rag_tools.get_collected_results()

        self.assertIn("article-1", article_payload)
        self.assertIn("news.example.com/latest", web_payload)
        self.assertEqual([article_result], collected_rag_results)
        self.assertEqual(web_results, collected_web_results)

    def test_answer_allows_multiple_tool_calls_in_single_turn_and_merges_results(self) -> None:
        article_result = _build_article_result()
        image_result = _build_image_result()
        research_call_log: list[dict[str, object]] = []
        synthesis_agent = _FakeSynthesisAgent(answer="整理后的答案 [c1] [c2] [c3] [w1]")
        request_context = RagRequestContext(limit=5)
        query_service = _FakeQueryService(
            {
                ("text_only", "silhouette", None): article_result,
                ("image_only", "silhouette detail", None): image_result,
            }
        )
        web_results = [
            WebSearchResult(
                title="External note",
                url="https://news.example.com/fashion",
                snippet="External snippet",
            )
        ]
        web_search_service = _FakeWebSearchService({"latest fashion": web_results})
        service = RagAnswerService(
            tools_factory=lambda context: RagTools(
                request_context=context,
                query_service=query_service,
                web_search_service=web_search_service,
            ),
            research_agent_factory=lambda rag_tools: _ToolCallingResearchAgent(
                rag_tools.build_langchain_tools(),
                [
                    ("search_fashion_articles", {"query": "silhouette"}),
                    ("search_fashion_images", {"text_query": "silhouette detail"}),
                    ("search_web", {"query": "latest fashion"}),
                ],
                call_log=research_call_log,
            ),
            synthesis_agent=synthesis_agent,
        )

        response = asyncio.run(
            service.answer(
                request=RagQueryRequest(query="帮我总结本季廓形"),
                request_context=request_context,
            )
        )

        self.assertEqual("整理后的答案 [C1] [C2] [C3] [W1]", response.answer)
        self.assertEqual(["article-1"], [package.article_id for package in response.packages])
        self.assertEqual(1, len(response.packages))
        self.assertEqual(1, len(response.packages[0].text_hits))
        self.assertEqual(1, len(response.packages[0].image_hits))
        self.assertEqual(["text_only", "image_only"], [plan.plan_type for plan in response.query_plans])
        self.assertEqual(["C1", "C2", "C3", "W1"], [citation.marker for citation in response.citations])
        self.assertEqual(web_results, response.web_results)
        self.assertEqual({"recursion_limit": 7}, research_call_log[0]["config"])
        self.assertEqual(
            [
                ("text_only", "silhouette", None),
                ("image_only", "silhouette detail", None),
            ],
            query_service.calls,
        )

        content_blocks = synthesis_agent.invoke_payloads[0]["messages"][0]["content"]
        self.assertIsInstance(content_blocks, list)
        self.assertEqual("text", content_blocks[0]["type"])
        self.assertIn('"user_query": "帮我总结本季廓形"', content_blocks[0]["text"])
        self.assertIn('"marker": "C1"', content_blocks[0]["text"])
        self.assertIn('"marker": "W1"', content_blocks[0]["text"])

    def test_answer_uses_three_iteration_recursion_limit(self) -> None:
        research_call_log: list[dict[str, object]] = []
        service = RagAnswerService(
            tools_factory=lambda context: RagTools(
                request_context=context,
                query_service=_FakeQueryService({}),
                web_search_service=_FakeWebSearchService({}),
            ),
            research_agent_factory=lambda rag_tools: _ToolCallingResearchAgent(
                rag_tools.build_langchain_tools(),
                [],
                call_log=research_call_log,
            ),
            synthesis_agent=_FakeSynthesisAgent(answer="无工具回答"),
        )

        response = asyncio.run(
            service.answer(
                request=RagQueryRequest(query="只验证迭代上限"),
                request_context=RagRequestContext(limit=5),
            )
        )

        self.assertEqual("无工具回答", response.answer)
        self.assertEqual({"recursion_limit": 7}, research_call_log[0]["config"])

    def test_answer_stream_forwards_deltas_and_returns_normalized_answer(self) -> None:
        article_result = _build_article_result()
        query_service = _FakeQueryService(
            {
                ("text_only", "silhouette", None): article_result,
            }
        )
        service = RagAnswerService(
            tools_factory=lambda context: RagTools(
                request_context=context,
                query_service=query_service,
                web_search_service=_FakeWebSearchService({}),
            ),
            research_agent_factory=lambda rag_tools: _ToolCallingResearchAgent(
                rag_tools.build_langchain_tools(),
                [("search_fashion_articles", {"query": "silhouette"})],
            ),
            synthesis_agent=_FakeSynthesisAgent(stream_chunks=["答案", " [c1]", " [c1]"]),
        )
        deltas: list[str] = []

        async def on_delta(delta: str) -> None:
            deltas.append(delta)

        response = asyncio.run(
            service.answer_stream(
                request=RagQueryRequest(query="给我一个结论"),
                request_context=RagRequestContext(limit=5),
                on_delta=on_delta,
            )
        )

        self.assertEqual(["答案", " [c1]", " [c1]"], deltas)
        self.assertEqual("答案 [C1]", response.answer)
        self.assertEqual(["C1"], [citation.marker for citation in response.citations])
        self.assertEqual("v2", service._synthesis_agent.stream_payloads[0]["kwargs"]["version"])

    def test_build_answer_tool_exposes_rag_answer_path_for_future_agents(self) -> None:
        service = RagAnswerService()
        request_context = RagRequestContext(limit=5)
        service.answer = AsyncMock(
            return_value=RagAnswerResponse(
                answer="工具答案",
                citations=[],
                packages=[],
                query_plans=[],
                web_results=[],
            )
        )

        tool = service.build_answer_tool(request_context=request_context)
        result = asyncio.run(tool.ainvoke({"query": "帮我总结一下"}))

        self.assertEqual("工具答案", result)
        self.assertEqual("content_and_artifact", tool.response_format)
        call_kwargs = service.answer.await_args.kwargs
        self.assertEqual("帮我总结一下", call_kwargs["request"].query)
        self.assertEqual(request_context, call_kwargs["request_context"])

    def test_build_answer_tool_rejects_blank_query_without_request_images(self) -> None:
        service = RagAnswerService()
        tool = service.build_answer_tool(request_context=RagRequestContext(limit=5))

        with self.assertRaisesRegex(ValueError, "rag query requires text query or uploaded images"):
            asyncio.run(tool.ainvoke({}))

    def test_build_answer_tool_allows_blank_query_when_request_images_exist(self) -> None:
        service = RagAnswerService()
        request_context = RagRequestContext(
            limit=5,
            request_images=[RequestImageInput(mime_type="image/png", base64_data="aGVsbG8=")],
        )
        service.answer = AsyncMock(
            return_value=RagAnswerResponse(
                answer="图片工具答案",
                citations=[],
                packages=[],
                query_plans=[],
                web_results=[],
            )
        )

        tool = service.build_answer_tool(request_context=request_context)
        result = asyncio.run(tool.ainvoke({}))

        self.assertEqual("图片工具答案", result)
        self.assertIsNone(service.answer.await_args.kwargs["request"].query)
