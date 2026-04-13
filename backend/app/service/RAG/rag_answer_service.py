"""Answer-layer orchestration over internal RAG tools and Brave search."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from backend.app.config.llm_config import Configuration
from backend.app.prompts.rag_answer_synthesis_prompt import RAG_ANSWER_SYNTHESIS_PROMPT
from backend.app.prompts.rag_tool_loop_prompt import RAG_TOOL_LOOP_PROMPT
from backend.app.schemas.rag_api import (
    AnswerVisibleEvidence,
    AnswerVisiblePackage,
    AnswerCitation,
    AssistantImageResult,
    ExternalVisualResult,
    RagAnswerResponse,
    RagQueryRequest,
    RagRequestContext,
    WebSearchResult,
)
from backend.app.schemas.rag_query import ArticlePackage, QueryPlan, QueryResult, RetrievalHit
from backend.app.service.RAG.rag_tools import RagTools
from backend.app.service.langchain_model_factory import build_rag_model

CITATION_MARKER_PATTERN = re.compile(r"\[([A-Za-z]\d+)\]")
AsyncDeltaHandler = Callable[[str], Awaitable[None]]
VISUAL_QUERY_TERMS = (
    "类似风格",
    "相似风格",
    "同款",
    "参考图",
    "look",
    "outfit",
    "眼镜",
    "墨镜",
    "太阳镜",
    "包",
    "手袋",
    "鞋",
    "靴",
    "凉鞋",
    "高跟鞋",
    "穿搭",
    "造型",
    "配饰",
)
VISUAL_DESCRIPTION_TERMS = (
    "sunglass",
    "glasses",
    "eyewear",
    "frame",
    "rectangle",
    "rectangular",
    "square",
    "cat-eye",
    "cat eye",
    "oval",
    "aviator",
    "acetate",
    "metal",
    "green",
    "black",
    "white",
    "red",
    "blue",
    "pink",
    "yellow",
    "bag",
    "shoe",
    "heel",
    "loafer",
    "boot",
    "dress",
    "jacket",
    "coat",
    "skirt",
    "top",
    "look",
    "outfit",
    "glove",
    "hat",
    "rectangular",
    "chunky",
    "oversized",
    "bold",
    "太阳镜",
    "墨镜",
    "眼镜",
    "镜框",
    "方形",
    "矩形",
    "猫眼",
    "绿色",
    "黑色",
    "白色",
    "红色",
    "蓝色",
    "粉色",
    "黄色",
    "包",
    "鞋",
    "靴",
    "凉鞋",
    "高跟鞋",
    "连衣裙",
    "夹克",
    "大衣",
    "裙",
    "上衣",
    "穿搭",
    "造型",
    "帽",
)
BOILERPLATE_CONTEXT_TERMS = (
    "celebrity news",
    "home fashion trends",
    "when you purchase through links",
    "published",
    "image by",
    "share",
    "top ニュース",
    "culture celebrity news",
    "runway every major trend",
)
MAX_ASSISTANT_IMAGE_RESULTS = 5
MAX_NON_VISUAL_ASSISTANT_IMAGE_RESULTS = 3
NON_VISUAL_IMAGE_PACKAGE_SCORE_RATIO = 0.75
MIN_CONTEXT_ONLY_IMAGE_SCORE_FOR_STRONG = 0.35
EDITORIAL_VISUAL_SOURCE_TERMS = (
    "vogue",
    "elle",
    "harpersbazaar",
    "whowhatwear",
    "wwd",
    "hypebeast",
    "highsnobiety",
    "fashionsnap",
    "fashionnetwork",
    "thezoereport",
    "anothermagazine",
    "dazed",
    "i-d",
)
MARKETPLACE_VISUAL_SOURCE_TERMS = (
    "amazon",
    "ebay",
    "etsy",
    "walmart",
    "aliexpress",
    "temu",
    "taobao",
    "mercari",
    "poshmark",
)
VISUAL_FOCUS_GROUPS = {
    "eyewear": ("眼镜", "墨镜", "太阳镜", "glasses", "sunglasses", "eyewear", "frame"),
    "bag": ("包", "手袋", "tote", "bag", "handbag", "purse", "clutch"),
    "shoe": ("鞋", "靴", "凉鞋", "高跟鞋", "shoe", "boot", "loafer", "heel", "sandal"),
    "dress": ("连衣裙", "裙", "dress", "gown", "skirt"),
    "outerwear": ("夹克", "大衣", "外套", "jacket", "coat", "blazer"),
    "hat": ("帽", "hat", "cap", "beanie"),
}
LEADING_META_PREFIXES = (
    "我理解用户的需求",
    "根据检索结果",
    "根据内部 rag 检索结果",
    "根据内部rag检索结果",
    "根据内部 r a g 检索结果",
    "基于检索结果",
    "我已经收集到了",
    "我已经收集到足够",
    "我已收集到",
    "我已获取到",
    "我找到了相关证据",
    "让我再搜索",
    "让我进行检索",
    "检索已完成",
    "由于这是一个",
    "由于内部图片证据较弱",
    "根据内部 rag 和外部搜索的综合结果",
    "根据内部 rag 检索和外部搜索结果",
)
META_ONLY_HEADINGS = {
    "检索结果摘要",
    "检索结果总结",
    "检索证据总结",
    "主要发现",
    "检索证据摘要",
}
AUDIT_BLOCK_MARKERS = (
    "文本证据",
    "图片证据",
    "关键发现",
)
FORMAL_ANSWER_RESET_MARKERS = (
    "让我为您整理",
    "以下是近期",
    "以下是最近",
    "按品牌拆开看",
    "按品牌来看",
    "直接给结论",
)


class _RagAnswerToolArgs(BaseModel):
    query: str | None = Field(
        default=None,
        description="The user question to answer with grounded fashion evidence.",
    )


class _WebSearchToolArgs(BaseModel):
    query: str = Field(description="External web search query used after rag_search.")


class _RagSearchArtifact(BaseModel):
    query: str | None = None
    packages: list[ArticlePackage] = Field(default_factory=list)
    query_plans: list[QueryPlan] = Field(default_factory=list)
    answer_visible_evidence: AnswerVisibleEvidence = Field(
        default_factory=AnswerVisibleEvidence
    )
    external_visual_results: list[ExternalVisualResult] = Field(default_factory=list)
    citations: list[AnswerCitation] = Field(default_factory=list)
    image_results: list[AssistantImageResult] = Field(default_factory=list)
    strong_image_hit_count: int = 0
    weak_image_hit_count: int = 0


@dataclass(slots=True)
class _ChatRunState:
    rag_artifact: _RagSearchArtifact | None = None
    web_results: list[WebSearchResult] = field(default_factory=list)


class RagAnswerService:
    """Run retrieval agents and synthesize one final grounded answer."""

    def __init__(
        self,
        *,
        configuration: Configuration | None = None,
        tools_factory: Callable[[RagRequestContext], RagTools] | None = None,
        research_agent_factory: Callable[[RagTools], Any] | None = None,
        chat_agent_factory: Callable[[list[StructuredTool]], Any] | None = None,
        synthesis_agent: Any | None = None,
    ) -> None:
        self._configuration = configuration or Configuration.from_runnable_config()
        self._tools_factory = (
            (lambda request_context: RagTools(request_context=request_context))
            if tools_factory is None
            else tools_factory
        )
        self._research_agent_factory = research_agent_factory
        self._chat_agent_factory = chat_agent_factory
        self._chat_agent = synthesis_agent

    async def answer(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None = None,
        recent_messages: list[dict] | None = None,
        user_memories: list[dict] | None = None,
    ) -> RagAnswerResponse:
        """Run the outer chat agent over rag_search and optional web_search."""
        state = await self._run_chat_agent(
            request=request,
            request_context=request_context,
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        answer = self._normalize_answer_citation_markers(
            state["answer"],
            state["citations"],
        )
        answer = self._sanitize_answer_style(answer)
        image_results = self._filter_image_results_for_answer(
            answer=answer,
            image_results=state["image_results"],
            citations=state["citations"],
            query=request.query,
            has_request_images=request_context.has_request_images,
        )
        return RagAnswerResponse(
            answer=answer,
            citations=state["citations"],
            packages=state["packages"],
            query_plans=state["query_plans"],
            web_results=state["web_results"],
            external_visual_results=state["external_visual_results"],
            image_results=image_results,
        )

    async def answer_stream(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None = None,
        recent_messages: list[dict] | None = None,
        user_memories: list[dict] | None = None,
        on_delta: AsyncDeltaHandler,
    ) -> RagAnswerResponse:
        """Run the outer chat agent and stream the final answer deltas."""
        state = await self._run_chat_agent_stream(
            request=request,
            request_context=request_context,
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
            on_delta=on_delta,
        )
        answer = self._normalize_answer_citation_markers(
            state["answer"],
            state["citations"],
        )
        answer = self._sanitize_answer_style(answer)
        image_results = self._filter_image_results_for_answer(
            answer=answer,
            image_results=state["image_results"],
            citations=state["citations"],
            query=request.query,
            has_request_images=request_context.has_request_images,
        )
        return RagAnswerResponse(
            answer=answer,
            citations=state["citations"],
            packages=state["packages"],
            query_plans=state["query_plans"],
            web_results=state["web_results"],
            external_visual_results=state["external_visual_results"],
            image_results=image_results,
        )

    def build_answer_tool(
        self,
        *,
        request_context: RagRequestContext,
        conversation_compact: str | None = None,
        recent_messages: list[dict] | None = None,
        user_memories: list[dict] | None = None,
    ) -> StructuredTool:
        """Expose the full RAG answer path as a reusable LangChain tool."""

        async def _run(query: str | None = None) -> tuple[str, dict[str, Any]]:
            normalized_query = self._normalize_optional_query(query)
            self._ensure_query_or_request_images(
                query=normalized_query,
                request_context=request_context,
            )
            response = await self.answer(
                request=RagQueryRequest(
                    query=normalized_query,
                    filters=request_context.filters,
                    limit=request_context.limit,
                ),
                request_context=request_context,
                conversation_compact=conversation_compact,
                recent_messages=recent_messages,
                user_memories=user_memories,
            )
            return response.answer, response.model_dump(mode="json")

        return StructuredTool.from_function(
            coroutine=_run,
            name="answer_fashion_question",
            description=(
                "Answer fashion intelligence questions with grounded internal RAG evidence "
                "and optional external web citations."
            ),
            args_schema=_RagAnswerToolArgs,
            response_format="content_and_artifact",
        )

    async def _run_chat_agent(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
    ) -> dict[str, Any]:
        self._ensure_query_or_request_images(
            query=request.query,
            request_context=request_context,
        )
        state = _ChatRunState()
        chat_agent = self._build_chat_agent(
            self._build_chat_agent_tools(
                request=request,
                request_context=request_context,
                conversation_compact=conversation_compact,
                recent_messages=recent_messages,
                user_memories=user_memories,
                state=state,
            )
        )
        result = await chat_agent.ainvoke(
            {
                "messages": self._build_chat_messages(
                    request=request,
                    request_context=request_context,
                    conversation_compact=conversation_compact,
                    recent_messages=recent_messages,
                    user_memories=user_memories,
                )
            },
            config={"recursion_limit": self._research_recursion_limit()},
        )
        answer = self._extract_agent_answer(result).strip()
        if not answer:
            raise ValueError("chat agent returned empty content")
        return self._build_response_state(answer=answer, state=state)

    async def _run_chat_agent_stream(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
        on_delta: AsyncDeltaHandler,
    ) -> dict[str, Any]:
        self._ensure_query_or_request_images(
            query=request.query,
            request_context=request_context,
        )
        state = _ChatRunState()
        chat_agent = self._build_chat_agent(
            self._build_chat_agent_tools(
                request=request,
                request_context=request_context,
                conversation_compact=conversation_compact,
                recent_messages=recent_messages,
                user_memories=user_memories,
                state=state,
            )
        )
        answer_parts: list[str] = []
        async for event in chat_agent.astream_events(
            {
                "messages": self._build_chat_messages(
                    request=request,
                    request_context=request_context,
                    conversation_compact=conversation_compact,
                    recent_messages=recent_messages,
                    user_memories=user_memories,
                )
            },
            config={"recursion_limit": self._research_recursion_limit()},
            version="v2",
        ):
            delta_text = self._extract_stream_delta_text(event)
            if not delta_text:
                continue
            answer_parts.append(delta_text)
            await on_delta(delta_text)

        answer = "".join(answer_parts).strip()
        if not answer:
            raise ValueError("chat agent returned empty content")
        return self._build_response_state(answer=answer, state=state)

    def _build_response_state(
        self,
        *,
        answer: str,
        state: _ChatRunState,
    ) -> dict[str, Any]:
        if state.rag_artifact is None:
            raise ValueError("chat agent must call rag_search before answering")
        return {
            "answer": answer,
            "citations": state.rag_artifact.citations,
            "packages": state.rag_artifact.packages,
            "query_plans": state.rag_artifact.query_plans,
            "web_results": list(state.web_results),
            "external_visual_results": state.rag_artifact.external_visual_results,
            "image_results": state.rag_artifact.image_results,
        }

    def _build_chat_agent_tools(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
        state: _ChatRunState,
    ) -> list[StructuredTool]:
        return [
            self._build_rag_search_tool(
                request=request,
                request_context=request_context,
                conversation_compact=conversation_compact,
                recent_messages=recent_messages,
                user_memories=user_memories,
                state=state,
            ),
            self._build_web_search_tool(
                request=request,
                request_context=request_context,
                state=state,
            ),
        ]

    def _build_chat_agent(self, tools: list[StructuredTool]):
        if self._chat_agent_factory is not None:
            return self._chat_agent_factory(tools)
        if self._chat_agent is not None:
            return self._chat_agent
        return create_agent(
            model=build_rag_model(self._configuration),
            tools=tools,
            system_prompt=RAG_ANSWER_SYNTHESIS_PROMPT,
        )

    def _build_rag_agent(self, rag_tools: RagTools):
        if self._research_agent_factory is not None:
            return self._research_agent_factory(rag_tools)
        return create_agent(
            model=build_rag_model(self._configuration),
            tools=rag_tools.build_langchain_tools(),
            system_prompt=RAG_TOOL_LOOP_PROMPT,
        )

    def _build_rag_search_tool(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
        state: _ChatRunState,
    ) -> StructuredTool:
        async def _run(query: str | None = None) -> tuple[str, dict[str, Any]]:
            if state.rag_artifact is not None:
                return (
                    "rag_search 已完成；不要重复调用。请基于现有内部证据直接回答，"
                    "或者只在确实缺最新外部信息时调用一次 web_search。",
                    state.rag_artifact.model_dump(mode="json"),
                )

            effective_query = self._normalize_optional_query(query)
            if effective_query is None:
                effective_query = request.query
            state.rag_artifact = await self._run_rag_search(
                request=RagQueryRequest(
                    query=effective_query,
                    filters=request.filters,
                    limit=request.limit,
                ),
                user_query=request.query,
                request_context=request_context,
                conversation_compact=conversation_compact,
                recent_messages=recent_messages,
                user_memories=user_memories,
            )
            return (
                self._build_rag_search_summary(state.rag_artifact),
                state.rag_artifact.model_dump(mode="json"),
            )

        return StructuredTool.from_function(
            coroutine=_run,
            name="rag_search",
            description=(
                "Inspect internal fashion RAG evidence first. This tool already handles "
                "text, image, fusion retrieval, citations, and assistant-visible image results."
            ),
            args_schema=_RagAnswerToolArgs,
            response_format="content_and_artifact",
        )

    def _build_web_search_tool(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        state: _ChatRunState,
    ) -> StructuredTool:
        async def _run(query: str) -> tuple[str, dict[str, Any]]:
            if state.rag_artifact is None:
                raise ValueError("web_search requires rag_search first")
            if state.web_results:
                return (
                    "web_search 已完成；不要重复调用。请基于当前内外部证据直接回答。",
                    {
                        "web_results": [
                            result.model_dump() for result in state.web_results
                        ],
                        "external_visual_results": [
                            result.model_dump()
                            for result in state.rag_artifact.external_visual_results
                        ],
                        "citations": [
                            citation.model_dump()
                            for citation in state.rag_artifact.citations
                        ],
                        "image_results": [
                            result.model_dump()
                            for result in state.rag_artifact.image_results
                        ],
                    },
                )

            normalized_query = self._require_non_empty_query(query)
            rag_tools = self._tools_factory(request_context)
            next_results = await rag_tools.search_web(query=normalized_query)
            state.web_results = self._deduplicate_web_results(
                [*state.web_results, *next_results]
            )
            if self._should_fetch_external_visuals(
                query=normalized_query,
                request=request,
                request_context=request_context,
                artifact=state.rag_artifact,
            ):
                next_visual_results = await rag_tools.search_external_visuals(
                    query=self._build_external_visual_query(normalized_query)
                )
                state.rag_artifact.external_visual_results = self._deduplicate_external_visual_results(
                    [
                        *state.rag_artifact.external_visual_results,
                        *next_visual_results,
                    ]
                )

            state.rag_artifact.answer_visible_evidence = self._build_answer_visible_evidence(
                packages=state.rag_artifact.packages,
                query=state.rag_artifact.query,
                external_visual_results=state.rag_artifact.external_visual_results,
            )
            state.rag_artifact.citations = self._build_citations(
                packages=state.rag_artifact.packages,
                web_results=state.web_results,
                external_visual_results=state.rag_artifact.external_visual_results,
            )
            state.rag_artifact.image_results = self._build_image_results(
                query=state.rag_artifact.query,
                has_request_images=request_context.has_request_images,
                answer_visible_evidence=state.rag_artifact.answer_visible_evidence,
                external_visual_results=state.rag_artifact.external_visual_results,
                citations=state.rag_artifact.citations,
            )
            return (
                self._build_web_search_summary(state.web_results, state.rag_artifact.citations),
                {
                    "web_results": [
                        result.model_dump() for result in state.web_results
                    ],
                    "external_visual_results": [
                        result.model_dump()
                        for result in state.rag_artifact.external_visual_results
                    ],
                    "citations": [
                        citation.model_dump()
                        for citation in state.rag_artifact.citations
                    ],
                    "image_results": [
                        result.model_dump()
                        for result in state.rag_artifact.image_results
                    ],
                },
            )

        return StructuredTool.from_function(
            coroutine=_run,
            name="web_search",
            description=(
                "Search the external web only after rag_search when the internal RAG "
                "evidence is insufficient, the user explicitly needs fresh external updates, "
                "or a visual query needs external reference images after internal RAG inspection."
            ),
            args_schema=_WebSearchToolArgs,
            response_format="content_and_artifact",
        )

    def _build_chat_messages(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        context_message = self._build_optional_context_message(
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        if context_message is not None:
            messages.append(context_message)
        messages.append(
            {
                "role": "user",
                "content": self._build_chat_user_content(
                    request=request,
                    request_context=request_context,
                ),
            }
        )
        return messages

    def _build_rag_search_messages(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        context_message = self._build_optional_context_message(
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        if context_message is not None:
            messages.append(context_message)
        messages.append(
            {
                "role": "user",
                "content": self._build_tool_loop_user_content(
                    request=request,
                    request_context=request_context,
                ),
            }
        )
        return messages

    def _build_chat_user_content(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
    ) -> list[dict[str, object]]:
        text_parts = [
            "请先调用 rag_search 检查内部时尚资料，再决定是否需要 web_search。",
            f"用户文本问题：{request.query or '（无文本，仅图片）'}",
        ]
        content: list[dict[str, object]] = [
            {"type": "text", "text": "\n".join(text_parts)},
        ]
        for request_image in request_context.request_images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": request_image.to_data_url()},
                }
            )
        return content

    async def _run_rag_search(
        self,
        *,
        request: RagQueryRequest,
        user_query: str | None,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
    ) -> _RagSearchArtifact:
        forced_results = self._collect_forced_rag_results(
            request=request,
            request_context=request_context,
        )
        if forced_results is None:
            rag_tools = self._tools_factory(request_context)
            rag_agent = self._build_rag_agent(rag_tools)
            await rag_agent.ainvoke(
                {
                    "messages": self._build_rag_search_messages(
                        request=request,
                        request_context=request_context,
                        conversation_compact=conversation_compact,
                        recent_messages=recent_messages,
                        user_memories=user_memories,
                    ),
                },
                config={"recursion_limit": self._research_recursion_limit()},
            )
            rag_results, _unused_web_results = rag_tools.get_collected_results()
        else:
            rag_results = forced_results

        packages = self._merge_rag_packages(rag_results)
        query_plans = [result.query_plan for result in rag_results]
        external_visual_results: list[ExternalVisualResult] = []
        answer_visible_evidence = self._build_answer_visible_evidence(
            packages=packages,
            query=user_query,
            external_visual_results=external_visual_results,
        )
        citations = self._build_citations(
            packages=packages,
            web_results=[],
            external_visual_results=external_visual_results,
        )
        strong_image_hit_count, weak_image_hit_count = self._summarize_image_hit_strength(
            packages,
            query=user_query,
        )
        return _RagSearchArtifact(
            query=user_query,
            packages=packages,
            query_plans=query_plans,
            answer_visible_evidence=answer_visible_evidence,
            external_visual_results=external_visual_results,
            citations=citations,
            image_results=self._build_image_results(
                query=user_query,
                has_request_images=request_context.has_request_images,
                answer_visible_evidence=answer_visible_evidence,
                external_visual_results=external_visual_results,
                citations=citations,
            ),
            strong_image_hit_count=strong_image_hit_count,
            weak_image_hit_count=weak_image_hit_count,
        )

    def _collect_forced_rag_results(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
    ) -> list[QueryResult] | None:
        if request_context.has_request_images:
            rag_tools = self._tools_factory(request_context)
            if request.query is not None:
                return [
                    rag_tools.search_fashion_fusion(
                        query=request.query,
                        image_ref="request_image",
                    )
                ]
            return [rag_tools.search_fashion_images(image_ref="request_image")]

        normalized_query = self._normalize_optional_query(request.query)
        if normalized_query is None or not self._is_visual_query(normalized_query):
            return None

        rag_tools = self._tools_factory(request_context)
        return [rag_tools.search_fashion_fusion(query=normalized_query)]

    def _build_external_visual_query(self, query: str) -> str:
        simplified_query = (
            query.replace("根据这张图", "")
            .replace("这张图", "")
            .replace("图里", "")
            .replace("图片里", "")
            .replace("请根据", "")
            .strip("，。 ")
        )
        if simplified_query.startswith("请"):
            simplified_query = simplified_query.removeprefix("请").strip("，。 ")
        return simplified_query or query

    def _should_fetch_external_visuals(
        self,
        *,
        query: str,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        artifact: _RagSearchArtifact,
    ) -> bool:
        if artifact.strong_image_hit_count > 0:
            return False
        if request_context.has_request_images:
            return True

        normalized_query = self._normalize_optional_query(query)
        if normalized_query is None:
            normalized_query = request.query
        if normalized_query is None:
            return False
        return self._is_visual_query(normalized_query)

    @staticmethod
    def _is_visual_query(query: str) -> bool:
        normalized_query = query.strip()
        lower_query = normalized_query.lower()
        return any(
            term in normalized_query or term in lower_query
            for term in VISUAL_QUERY_TERMS
        )

    def _build_optional_context_message(
        self,
        *,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
    ) -> dict[str, str] | None:
        if not (conversation_compact or recent_messages or user_memories):
            return None

        context_parts: list[str] = []
        if conversation_compact:
            context_parts.append(f"历史对话摘要：{conversation_compact}")
        if user_memories:
            memories_text = "\n".join(
                [f"- {mem['type']}/{mem['key']}: {mem['value']}" for mem in user_memories]
            )
            context_parts.append(f"用户记忆：\n{memories_text}")
        if recent_messages:
            messages_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_messages])
            context_parts.append(f"最近对话：\n{messages_text}")
        return {
            "role": "system",
            "content": "\n\n".join(context_parts),
        }

    def _build_tool_loop_user_content(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
    ) -> list[dict[str, object]]:
        text_parts = [
            "用户请求如下。先理解需求，再决定是否调用检索工具。",
            f"用户文本问题：{request.query or '（无文本，仅图片）'}",
        ]
        content: list[dict[str, object]] = [
            {"type": "text", "text": "\n".join(text_parts)},
        ]
        for request_image in request_context.request_images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": request_image.to_data_url()},
                }
            )
        return content

    def _build_rag_search_summary(self, artifact: _RagSearchArtifact) -> str:
        summary_payload = {
            "query": artifact.query or "",
            "query_plan_types": [plan.plan_type for plan in artifact.query_plans],
            "package_count": len(artifact.packages),
            "strong_image_hit_count": artifact.strong_image_hit_count,
            "weak_image_hit_count": artifact.weak_image_hit_count,
            "image_result_count": len(artifact.image_results),
            "citation_markers": [citation.marker for citation in artifact.citations],
            "visual_external_fallback_triggered": bool(artifact.external_visual_results),
        }
        return json.dumps(summary_payload, ensure_ascii=False, sort_keys=True)

    def _build_web_search_summary(
        self,
        web_results: list[WebSearchResult],
        citations: list[AnswerCitation],
    ) -> str:
        summary_payload = {
            "web_result_count": len(web_results),
            "web_titles": [result.title for result in web_results],
            "citation_markers": [
                citation.marker for citation in citations if citation.source_type == "web"
            ],
            "visual_result_count": len(
                [
                    citation
                    for citation in citations
                    if citation.source_type == "web" and citation.marker.startswith("V")
                ]
            ),
        }
        return json.dumps(summary_payload, ensure_ascii=False, sort_keys=True)

    def _build_answer_visible_evidence(
        self,
        *,
        packages: list[ArticlePackage],
        query: str | None,
        external_visual_results: list[ExternalVisualResult],
    ) -> AnswerVisibleEvidence:
        strong_image_hit_count, _weak_image_hit_count = self._summarize_image_hit_strength(
            packages,
            query=query,
        )
        suppress_weak_image_hits = bool(external_visual_results) and strong_image_hit_count == 0

        filtered_packages: list[AnswerVisiblePackage] = []
        suppressed_image_hits: list[RetrievalHit] = []
        for package in packages:
            visible_image_hits: list[RetrievalHit] = []
            for hit in package.image_hits:
                if suppress_weak_image_hits and not self._is_strong_image_hit(hit, query=query):
                    suppressed_image_hits.append(hit.model_copy(deep=True))
                    continue
                visible_image_hits.append(hit.model_copy(deep=True))

            if not package.text_hits and not visible_image_hits:
                continue

            filtered_packages.append(
                AnswerVisiblePackage(
                    article_id=package.article_id,
                    title=package.title,
                    summary=package.summary,
                    text_hits=[hit.model_copy(deep=True) for hit in package.text_hits],
                    image_hits=visible_image_hits,
                    combined_score=package.combined_score,
                )
            )

        return AnswerVisibleEvidence(
            packages=filtered_packages,
            suppressed_image_hits=suppressed_image_hits,
            external_visual_results=[result.model_copy(deep=True) for result in external_visual_results],
        )

    def _build_image_results(
        self,
        *,
        query: str | None,
        has_request_images: bool,
        answer_visible_evidence: AnswerVisibleEvidence,
        external_visual_results: list[ExternalVisualResult],
        citations: list[AnswerCitation],
    ) -> list[AssistantImageResult]:
        image_results: list[AssistantImageResult] = []
        seen_keys: set[str] = set()
        seen_rag_articles: set[str] = set()
        seen_rag_source_pages: set[str] = set()
        visual_request = has_request_images or (
            query is not None and self._is_visual_query(query)
        )
        top_package_score = (
            answer_visible_evidence.packages[0].combined_score
            if answer_visible_evidence.packages
            else 0.0
        )
        max_results = (
            MAX_ASSISTANT_IMAGE_RESULTS
            if visual_request
            else MAX_NON_VISUAL_ASSISTANT_IMAGE_RESULTS
        )

        for package in answer_visible_evidence.packages:
            if not visual_request:
                if not package.text_hits:
                    continue
                if (
                    top_package_score > 0
                    and package.combined_score
                    < top_package_score * NON_VISUAL_IMAGE_PACKAGE_SCORE_RATIO
                ):
                    continue
            for hit in package.image_hits:
                source_page_url = (hit.citation_locator.canonical_url or "").strip()
                if not visual_request and (
                    hit.article_id in seen_rag_articles
                    or (source_page_url and source_page_url in seen_rag_source_pages)
                ):
                    continue
                image_url = (hit.source_url or "").strip()
                if not image_url:
                    continue
                result_key = f"rag:{hit.article_image_id or hit.retrieval_unit_id}"
                if result_key in seen_keys:
                    continue
                seen_keys.add(result_key)
                image_results.append(
                    AssistantImageResult(
                        id=result_key,
                        source_type="rag",
                        image_url=image_url,
                        preview_url=image_url,
                        title=(
                            (hit.caption_raw or "").strip()
                            or (hit.alt_text or "").strip()
                            or package.title
                            or hit.title
                        ),
                        source_name=hit.citation_locator.source_name,
                        source_page_url=hit.citation_locator.canonical_url,
                        snippet=(
                            (hit.caption_raw or "").strip()
                            or (hit.alt_text or "").strip()
                            or (hit.context_snippet or "").strip()
                            or None
                        ),
                        article_id=hit.article_id,
                        article_image_id=hit.article_image_id,
                        citation_marker=self._find_citation_marker_for_rag_hit(
                            hit=hit,
                            citations=citations,
                        ),
                    )
                )
                seen_rag_articles.add(hit.article_id)
                if source_page_url:
                    seen_rag_source_pages.add(source_page_url)
                if len(image_results) >= max_results:
                    return image_results

        for visual_result in self._sort_external_visual_results(
            external_visual_results,
            query=query,
        ):
            image_url = (visual_result.image_url or visual_result.url).strip()
            if not image_url:
                continue
            result_key = f"external:{visual_result.source_page_url or visual_result.url}"
            if result_key in seen_keys:
                continue
            seen_keys.add(result_key)
            image_results.append(
                AssistantImageResult(
                    id=result_key,
                    source_type="external",
                    image_url=image_url,
                    preview_url=(visual_result.thumbnail_url or image_url).strip() or None,
                    title=visual_result.title,
                    source_name=visual_result.source_name,
                    source_page_url=visual_result.source_page_url or visual_result.url,
                    snippet=(visual_result.snippet or visual_result.content or "").strip() or None,
                    citation_marker=self._find_citation_marker_for_external_visual(
                        visual_result=visual_result,
                        citations=citations,
                    ),
                )
            )
            if len(image_results) >= max_results:
                return image_results

        return image_results

    def _filter_image_results_for_answer(
        self,
        *,
        answer: str,
        image_results: list[AssistantImageResult],
        citations: list[AnswerCitation],
        query: str | None,
        has_request_images: bool,
    ) -> list[AssistantImageResult]:
        visual_request = has_request_images or (
            query is not None and self._is_visual_query(query)
        )
        if visual_request or not image_results:
            return image_results

        used_markers = {
            match.group(1).upper()
            for match in CITATION_MARKER_PATTERN.finditer(answer)
        }
        if not used_markers:
            return image_results

        cited_urls = {
            citation.url
            for citation in citations
            if citation.marker in used_markers and citation.url
        }
        filtered_results = [
            result
            for result in image_results
            if (
                result.citation_marker in used_markers
                or (
                    result.source_page_url is not None
                    and result.source_page_url in cited_urls
                )
            )
        ]
        return filtered_results or image_results

    def _sanitize_answer_style(self, answer: str) -> str:
        normalized_answer = answer.strip()
        if not normalized_answer:
            return normalized_answer

        lines = normalized_answer.splitlines()
        sanitized_lines: list[str] = []
        skipping_leading_meta = True

        for line in lines:
            stripped_line = line.strip()
            lowered_line = stripped_line.casefold()

            if skipping_leading_meta:
                if not stripped_line:
                    continue
                if any(lowered_line.startswith(prefix) for prefix in LEADING_META_PREFIXES):
                    continue
                heading_text = stripped_line.lstrip("#").strip()
                if heading_text in META_ONLY_HEADINGS:
                    continue
                if (
                    "strong_image_hit_count" in lowered_line
                    or "weak_image_hit_count" in lowered_line
                    or "web_search" in lowered_line
                    or "调用外部搜索" in stripped_line
                ):
                    continue
                skipping_leading_meta = False

            sanitized_lines.append(line)

        sanitized_answer = "\n".join(sanitized_lines).strip()
        if not sanitized_answer:
            return normalized_answer

        separator_matches = list(re.finditer(r"\n-{3,}\n", sanitized_answer))
        if separator_matches:
            separator_match = separator_matches[-1]
            leading_block = sanitized_answer[: separator_match.start()]
            if "检索结果" in leading_block or any(
                marker in leading_block for marker in AUDIT_BLOCK_MARKERS
            ):
                sanitized_answer = sanitized_answer[separator_match.end() :].lstrip()

        reset_offsets = [
            sanitized_answer.rfind(marker) for marker in FORMAL_ANSWER_RESET_MARKERS
        ]
        reset_offset = (
            max(offset for offset in reset_offsets if offset >= 0)
            if any(offset >= 0 for offset in reset_offsets)
            else -1
        )
        if reset_offset >= 0:
            leading_block = sanitized_answer[:reset_offset]
            if any(marker in leading_block for marker in AUDIT_BLOCK_MARKERS):
                sanitized_answer = sanitized_answer[reset_offset:].lstrip()

        sanitized_answer = re.sub(r"^(?:---\s*)+", "", sanitized_answer).lstrip()
        sanitized_answer = re.sub(
            r"^根据内部\s*r\s*a\s*g\s*检索结果[，,:：]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^根据内部\s*rag\s*检索结果[，,:：]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^根据检索结果[，,:：]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^根据内部时尚资料检索[，,:：]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^根据内部时尚资料[^：:\n]*[：:]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^让我为您整理[^：:\n]*[：:]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^以下是近期[^：:\n]*[：:]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        sanitized_answer = re.sub(
            r"^以下是最近[^：:\n]*[：:]\s*",
            "",
            sanitized_answer,
            flags=re.IGNORECASE,
        )
        return sanitized_answer.strip()

    def _sort_external_visual_results(
        self,
        external_visual_results: list[ExternalVisualResult],
        *,
        query: str | None,
    ) -> list[ExternalVisualResult]:
        return sorted(
            external_visual_results,
            key=lambda result: self._score_external_visual_result(result, query=query),
            reverse=True,
        )

    def _score_external_visual_result(
        self,
        result: ExternalVisualResult,
        *,
        query: str | None,
    ) -> tuple[int, int, int, int]:
        source_text = self._normalize_text_for_match(
            " ".join(
                value
                for value in (
                    result.source_name,
                    result.source_page_url,
                    result.url,
                )
                if isinstance(value, str)
            )
        )
        content_text = self._normalize_text_for_match(
            " ".join(
                value
                for value in (
                    result.title,
                    result.snippet,
                    result.content,
                )
                if isinstance(value, str)
            )
        )
        quality_score = 0
        if any(term in source_text for term in EDITORIAL_VISUAL_SOURCE_TERMS):
            quality_score += 4
        if any(term in source_text for term in MARKETPLACE_VISUAL_SOURCE_TERMS):
            quality_score -= 8
        if result.source_page_url:
            quality_score += 1
        if result.thumbnail_url:
            quality_score += 1
        if "image 1 of" in content_text or "image 1 of 4" in content_text:
            quality_score -= 3
        if "&#" in (result.title or ""):
            quality_score -= 1

        focus_score = 0
        focus_terms = self._extract_visual_focus_terms(query)
        if focus_terms and any(term in content_text for term in focus_terms):
            focus_score += 2
        elif query is not None and self._normalize_text_for_match(query) in content_text:
            focus_score += 1

        text_quality_score = 0
        if len((result.title or "").strip()) <= 90:
            text_quality_score += 1
        if (result.snippet or "").strip():
            text_quality_score += 1

        return (
            quality_score,
            focus_score,
            text_quality_score,
            1 if result.image_url else 0,
        )

    def _find_citation_marker_for_rag_hit(
        self,
        *,
        hit: RetrievalHit,
        citations: list[AnswerCitation],
    ) -> str | None:
        for citation in citations:
            if citation.source_type != "rag":
                continue
            if citation.article_id != hit.citation_locator.article_id:
                continue
            if citation.article_image_id != hit.citation_locator.article_image_id:
                continue
            if citation.chunk_index != hit.citation_locator.chunk_index:
                continue
            return citation.marker
        return None

    def _find_citation_marker_for_external_visual(
        self,
        *,
        visual_result: ExternalVisualResult,
        citations: list[AnswerCitation],
    ) -> str | None:
        citation_url = visual_result.source_page_url or visual_result.url
        for citation in citations:
            if citation.source_type == "web" and citation.url == citation_url:
                return citation.marker
        return None

    def _deduplicate_external_visual_results(
        self,
        results: list[ExternalVisualResult],
    ) -> list[ExternalVisualResult]:
        unique_by_url: dict[str, ExternalVisualResult] = {}
        for result in results:
            key = (result.source_page_url or result.url).strip()
            if not key:
                continue
            unique_by_url.setdefault(key, result)
        return list(unique_by_url.values())

    def _summarize_image_hit_strength(
        self,
        packages: list[ArticlePackage],
        *,
        query: str | None,
    ) -> tuple[int, int]:
        strong_image_hit_count = 0
        weak_image_hit_count = 0
        for package in packages:
            for hit in package.image_hits:
                if self._is_strong_image_hit(hit, query=query):
                    strong_image_hit_count += 1
                else:
                    weak_image_hit_count += 1
        return strong_image_hit_count, weak_image_hit_count

    def _is_strong_image_hit(
        self,
        hit: RetrievalHit,
        *,
        query: str | None,
    ) -> bool:
        combined_support_text = self._normalize_text_for_match(
            " ".join(
                value.strip()
                for value in (
                    hit.caption_raw or "",
                    hit.alt_text or "",
                    hit.credit_raw or "",
                    hit.context_snippet or "",
                )
                if value and value.strip()
            )
        )
        if not combined_support_text:
            return False

        if self._extract_visual_focus_terms(query) and not self._supports_query_focus(
            combined_support_text,
            query=query,
        ):
            return False

        if (hit.caption_raw or "").strip() or (hit.alt_text or "").strip():
            return True
        if self._is_descriptive_support_text(hit.credit_raw):
            return True
        if float(hit.score) < MIN_CONTEXT_ONLY_IMAGE_SCORE_FOR_STRONG:
            return False
        return self._is_descriptive_context_snippet(
            context_snippet=hit.context_snippet,
            title=hit.title,
        )

    def _is_descriptive_context_snippet(
        self,
        *,
        context_snippet: str | None,
        title: str | None,
    ) -> bool:
        normalized_context = self._normalize_text_for_match(context_snippet)
        if not normalized_context:
            return False
        if any(term in normalized_context for term in BOILERPLATE_CONTEXT_TERMS):
            return False
        normalized_title = self._normalize_text_for_match(title)
        if normalized_title and normalized_title in normalized_context:
            extra_tokens = max(
                0,
                len(normalized_context.split()) - len(normalized_title.split()),
            )
            if extra_tokens <= 4:
                return False
        return any(term in normalized_context for term in VISUAL_DESCRIPTION_TERMS)

    def _is_descriptive_support_text(self, value: str | None) -> bool:
        normalized_value = self._normalize_text_for_match(value)
        if not normalized_value:
            return False
        return any(term in normalized_value for term in VISUAL_DESCRIPTION_TERMS)

    def _supports_query_focus(self, normalized_text: str, *, query: str | None) -> bool:
        focus_terms = self._extract_visual_focus_terms(query)
        if not focus_terms:
            return True
        return any(term in normalized_text for term in focus_terms)

    def _extract_visual_focus_terms(self, query: str | None) -> tuple[str, ...]:
        normalized_query = self._normalize_text_for_match(query)
        if not normalized_query:
            return ()
        for terms in VISUAL_FOCUS_GROUPS.values():
            if any(term in normalized_query for term in terms):
                return terms
        return ()

    @staticmethod
    def _normalize_text_for_match(value: str | None) -> str:
        if value is None:
            return ""
        normalized_value = re.sub(r"\s+", " ", value).strip().casefold()
        return normalized_value

    def _research_recursion_limit(self) -> int:
        return (self._configuration.max_react_tool_calls * 2) + 1

    def _ensure_query_or_request_images(
        self,
        *,
        query: str | None,
        request_context: RagRequestContext,
    ) -> None:
        if query is None and not request_context.has_request_images:
            raise ValueError("rag query requires text query or uploaded images")

    @staticmethod
    def _normalize_optional_query(query: str | None) -> str | None:
        if query is None:
            return None
        normalized_query = query.strip()
        return normalized_query or None

    @staticmethod
    def _require_non_empty_query(query: str) -> str:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must be a non-empty string")
        return normalized_query

    def _extract_agent_answer(self, result: Any) -> str:
        messages = self._extract_result_messages(result)
        if not messages:
            raise ValueError("rag answer synthesis returned no messages")
        return self._extract_message_content(messages[-1].content)

    def _extract_result_messages(self, result: Any) -> list[Any]:
        if hasattr(result, "value"):
            result = result.value
        if not isinstance(result, dict):
            raise ValueError("agent result must be a dict-like payload")
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise ValueError("agent result must include messages")
        return messages

    def _extract_stream_delta_text(self, event: Any) -> str:
        if not isinstance(event, dict) or event.get("event") != "on_chat_model_stream":
            return ""
        data = event.get("data")
        if not isinstance(data, dict):
            return ""
        chunk = data.get("chunk")
        if chunk is None:
            return ""
        return self._extract_message_content(getattr(chunk, "content", ""))

    def _merge_rag_packages(self, rag_results: list[QueryResult]) -> list[ArticlePackage]:
        merged_by_article_id: dict[str, ArticlePackage] = {}
        for result in rag_results:
            for package in result.packages:
                existing_package = merged_by_article_id.get(package.article_id)
                if existing_package is None:
                    merged_by_article_id[package.article_id] = package.model_copy(deep=True)
                    continue
                existing_package.text_hits = self._merge_hits(
                    existing_package.text_hits,
                    package.text_hits,
                )
                existing_package.image_hits = self._merge_hits(
                    existing_package.image_hits,
                    package.image_hits,
                )
                existing_package.combined_score = max(
                    existing_package.combined_score,
                    package.combined_score,
                )
                existing_package.title = existing_package.title or package.title
                existing_package.summary = existing_package.summary or package.summary
        return sorted(
            merged_by_article_id.values(),
            key=lambda package: package.combined_score,
            reverse=True,
        )

    def _merge_hits(
        self,
        current_hits: list[RetrievalHit],
        incoming_hits: list[RetrievalHit],
    ) -> list[RetrievalHit]:
        merged_by_unit_id = {
            hit.retrieval_unit_id: hit.model_copy(deep=True)
            for hit in current_hits
        }
        for hit in incoming_hits:
            merged_by_unit_id.setdefault(hit.retrieval_unit_id, hit.model_copy(deep=True))
        return sorted(merged_by_unit_id.values(), key=lambda hit: hit.score, reverse=True)

    def _deduplicate_web_results(self, web_results: list[WebSearchResult]) -> list[WebSearchResult]:
        unique_by_url: dict[str, WebSearchResult] = {}
        for result in web_results:
            unique_by_url.setdefault(result.url, result)
        return list(unique_by_url.values())

    def _build_citations(
        self,
        *,
        packages: list[ArticlePackage],
        web_results: list[WebSearchResult],
        external_visual_results: list[ExternalVisualResult],
    ) -> list[AnswerCitation]:
        citations: list[AnswerCitation] = []
        seen_rag_keys: set[tuple[str, str | None, int | None]] = set()
        rag_index = 1
        for package in packages:
            for hit in [*package.text_hits, *package.image_hits]:
                rag_key = (
                    hit.citation_locator.article_id,
                    hit.citation_locator.article_image_id,
                    hit.citation_locator.chunk_index,
                )
                if rag_key in seen_rag_keys:
                    continue
                seen_rag_keys.add(rag_key)
                citations.append(
                    AnswerCitation(
                        marker=f"C{rag_index}",
                        source_type="rag",
                        title=package.title,
                        source_name=hit.citation_locator.source_name,
                        url=hit.citation_locator.canonical_url,
                        article_id=hit.citation_locator.article_id,
                        article_image_id=hit.citation_locator.article_image_id,
                        chunk_index=hit.citation_locator.chunk_index,
                    )
                )
                rag_index += 1
                for grounding_text in hit.grounding_texts:
                    grounding_key = (
                        grounding_text.citation_locator.article_id,
                        grounding_text.citation_locator.article_image_id,
                        grounding_text.citation_locator.chunk_index,
                    )
                    if grounding_key in seen_rag_keys:
                        continue
                    seen_rag_keys.add(grounding_key)
                    citations.append(
                        AnswerCitation(
                            marker=f"C{rag_index}",
                            source_type="rag",
                            title=package.title,
                            source_name=grounding_text.citation_locator.source_name,
                            url=grounding_text.citation_locator.canonical_url,
                            article_id=grounding_text.citation_locator.article_id,
                            article_image_id=grounding_text.citation_locator.article_image_id,
                            chunk_index=grounding_text.citation_locator.chunk_index,
                        )
                    )
                    rag_index += 1

        for index, web_result in enumerate(web_results, start=1):
            parsed_url = urlparse(web_result.url)
            citations.append(
                AnswerCitation(
                    marker=f"W{index}",
                    source_type="web",
                    title=web_result.title,
                    source_name=parsed_url.netloc or web_result.url,
                    url=web_result.url,
                    snippet=web_result.snippet,
                )
            )

        for index, visual_result in enumerate(external_visual_results, start=1):
            citation_url = visual_result.source_page_url or visual_result.url
            parsed_url = urlparse(citation_url)
            citations.append(
                AnswerCitation(
                    marker=f"V{index}",
                    source_type="web",
                    title=visual_result.title,
                    source_name=visual_result.source_name or parsed_url.netloc or citation_url,
                    url=citation_url,
                    snippet=visual_result.snippet or visual_result.content[:200],
                )
            )
        return citations

    def _normalize_answer_citation_markers(
        self,
        answer: str,
        citations: list[AnswerCitation],
    ) -> str:
        if not answer or not citations:
            return answer

        marker_by_lower = {
            citation.marker.casefold(): citation.marker
            for citation in citations
        }

        normalized_parts: list[str] = []
        last_index = 0
        for match in CITATION_MARKER_PATTERN.finditer(answer):
            normalized_parts.append(answer[last_index:match.start()])
            marker = marker_by_lower.get(match.group(1).casefold())
            if marker is not None:
                normalized_parts.append(f"[{marker}]")
            last_index = match.end()

        normalized_parts.append(answer[last_index:])
        normalized_answer = "".join(normalized_parts)

        for citation in citations:
            marker_token = f"[{citation.marker}]"
            duplicate_pattern = re.compile(
                rf"{re.escape(marker_token)}(?:\s*{re.escape(marker_token)})+"
            )
            normalized_answer = duplicate_pattern.sub(
                marker_token,
                normalized_answer,
            )

        normalized_answer = re.sub(r"[ \t]+\n", "\n", normalized_answer)
        normalized_answer = re.sub(r"\n{3,}", "\n\n", normalized_answer)
        return normalized_answer.strip()

    def _extract_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                item_type = getattr(item, "type", None)
                if item_type == "text":
                    text = getattr(item, "text", "")
                    if text:
                        text_parts.append(str(text))
                    continue
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        text_parts.append(text)
            return "".join(text_parts)
        return ""


__all__ = ["RagAnswerService"]
