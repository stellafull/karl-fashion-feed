"""Answer-layer orchestration over internal RAG tools and Brave search."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
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
    ExternalVisualResult,
    RagAnswerResponse,
    RagQueryRequest,
    RagRequestContext,
    WebSearchResult,
)
from backend.app.schemas.rag_query import ArticlePackage, QueryResult, RetrievalHit
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
VISUAL_FOCUS_GROUPS = {
    "eyewear": ("眼镜", "墨镜", "太阳镜", "glasses", "sunglasses", "eyewear", "frame"),
    "bag": ("包", "手袋", "tote", "bag", "handbag", "purse", "clutch"),
    "shoe": ("鞋", "靴", "凉鞋", "高跟鞋", "shoe", "boot", "loafer", "heel", "sandal"),
    "dress": ("连衣裙", "裙", "dress", "gown", "skirt"),
    "outerwear": ("夹克", "大衣", "外套", "jacket", "coat", "blazer"),
    "hat": ("帽", "hat", "cap", "beanie"),
}


class _RagAnswerToolArgs(BaseModel):
    query: str | None = Field(
        default=None,
        description="The user question to answer with grounded fashion evidence.",
    )


class RagAnswerService:
    """Run retrieval agents and synthesize one final grounded answer."""

    def __init__(
        self,
        *,
        configuration: Configuration | None = None,
        tools_factory: Callable[[RagRequestContext], RagTools] | None = None,
        research_agent_factory: Callable[[RagTools], Any] | None = None,
        synthesis_agent: Any | None = None,
    ) -> None:
        self._configuration = configuration or Configuration.from_runnable_config()
        self._tools_factory = (
            (lambda request_context: RagTools(request_context=request_context))
            if tools_factory is None
            else tools_factory
        )
        self._research_agent_factory = research_agent_factory
        self._synthesis_agent = synthesis_agent

    async def answer(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None = None,
        recent_messages: list[dict] | None = None,
        user_memories: list[dict] | None = None,
    ) -> RagAnswerResponse:
        """Execute retrieval tools and synthesize one final Chinese answer."""
        (
            packages,
            query_plans,
            unique_web_results,
            external_visual_results,
            citations,
        ) = await self._collect_answer_materials(
            request=request,
            request_context=request_context,
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        answer_visible_evidence = self._build_answer_visible_evidence(
            packages=packages,
            query=request.query,
            external_visual_results=external_visual_results,
        )
        answer = await self._synthesize_answer(
            request=request,
            request_context=request_context,
            packages=packages,
            answer_visible_evidence=answer_visible_evidence,
            web_results=unique_web_results,
            external_visual_results=external_visual_results,
            citations=citations,
        )
        answer = self._normalize_answer_citation_markers(answer, citations)
        return RagAnswerResponse(
            answer=answer,
            citations=citations,
            packages=packages,
            query_plans=query_plans,
            web_results=unique_web_results,
            external_visual_results=external_visual_results,
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
        """Execute retrieval first, then stream answer synthesis deltas."""
        (
            packages,
            query_plans,
            unique_web_results,
            external_visual_results,
            citations,
        ) = await self._collect_answer_materials(
            request=request,
            request_context=request_context,
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        answer_visible_evidence = self._build_answer_visible_evidence(
            packages=packages,
            query=request.query,
            external_visual_results=external_visual_results,
        )
        answer = await self._synthesize_answer_stream(
            request=request,
            request_context=request_context,
            packages=packages,
            answer_visible_evidence=answer_visible_evidence,
            web_results=unique_web_results,
            external_visual_results=external_visual_results,
            citations=citations,
            on_delta=on_delta,
        )
        answer = self._normalize_answer_citation_markers(answer, citations)
        return RagAnswerResponse(
            answer=answer,
            citations=citations,
            packages=packages,
            query_plans=query_plans,
            web_results=unique_web_results,
            external_visual_results=external_visual_results,
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

    async def _collect_answer_materials(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None,
        recent_messages: list[dict] | None,
        user_memories: list[dict] | None,
    ) -> tuple[
        list[ArticlePackage],
        list[Any],
        list[WebSearchResult],
        list[ExternalVisualResult],
        list[AnswerCitation],
    ]:
        forced_results = self._collect_forced_rag_results(
            request=request,
            request_context=request_context,
        )
        if forced_results is not None:
            packages = self._merge_rag_packages(forced_results)
            query_plans = [result.query_plan for result in forced_results]
            external_visual_results = await self._collect_external_visual_fallback(
                request=request,
                request_context=request_context,
                packages=packages,
            )
            citations = self._build_citations(
                packages=packages,
                web_results=[],
                external_visual_results=external_visual_results,
            )
            return packages, query_plans, [], external_visual_results, citations

        rag_tools = self._tools_factory(request_context)
        research_agent = self._build_research_agent(rag_tools)
        await research_agent.ainvoke(
            {
                "messages": self._build_research_messages(
                    request=request,
                    request_context=request_context,
                    conversation_compact=conversation_compact,
                    recent_messages=recent_messages,
                    user_memories=user_memories,
                ),
            },
            config={"recursion_limit": self._research_recursion_limit()},
        )

        rag_results, web_results = rag_tools.get_collected_results()
        packages = self._merge_rag_packages(rag_results)
        query_plans = [result.query_plan for result in rag_results]
        unique_web_results = self._deduplicate_web_results(web_results)
        external_visual_results = await self._collect_external_visual_fallback(
            request=request,
            request_context=request_context,
            packages=packages,
        )
        citations = self._build_citations(
            packages=packages,
            web_results=unique_web_results,
            external_visual_results=external_visual_results,
        )
        return packages, query_plans, unique_web_results, external_visual_results, citations

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

    async def _collect_external_visual_fallback(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
    ) -> list[ExternalVisualResult]:
        fallback_query = self._build_external_fallback_query(
            request=request,
            request_context=request_context,
            packages=packages,
        )
        if fallback_query is None:
            return []

        rag_tools = self._tools_factory(request_context)
        return await rag_tools.search_external_visuals(query=fallback_query)

    def _build_external_fallback_query(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
    ) -> str | None:
        normalized_query = self._normalize_optional_query(request.query)
        is_visual_request = request_context.has_request_images or (
            normalized_query is not None and self._is_visual_query(normalized_query)
        )
        if not is_visual_request or normalized_query is None:
            return None

        if not self._needs_external_visual_fallback(packages, query=normalized_query):
            return None

        simplified_query = (
            normalized_query.replace("根据这张图", "")
            .replace("这张图", "")
            .replace("图里", "")
            .replace("图片里", "")
            .replace("请根据", "")
            .strip("，。 ")
        )
        if simplified_query.startswith("请"):
            simplified_query = simplified_query.removeprefix("请").strip("，。 ")
        return simplified_query or normalized_query

    def _needs_external_visual_fallback(
        self,
        packages: list[ArticlePackage],
        *,
        query: str | None,
    ) -> bool:
        return not self._has_strong_image_evidence(packages, query=query)

    @staticmethod
    def _is_visual_query(query: str) -> bool:
        normalized_query = query.strip()
        lower_query = normalized_query.lower()
        return any(
            term in normalized_query or term in lower_query
            for term in VISUAL_QUERY_TERMS
        )

    def _build_research_messages(
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
            "role": "user",
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

    def _build_synthesis_user_content(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
        answer_visible_evidence: AnswerVisibleEvidence,
        web_results: list[WebSearchResult],
        external_visual_results: list[ExternalVisualResult],
        citations: list[AnswerCitation],
    ) -> list[dict[str, object]]:
        strong_image_hit_count, weak_image_hit_count = self._summarize_image_hit_strength(
            packages,
            query=request.query,
        )
        synthesis_payload = {
            "user_query": request.query or "",
            "has_request_images": request_context.has_request_images,
            "request_image_count": len(request_context.request_images),
            "strong_image_hit_count": strong_image_hit_count,
            "weak_image_hit_count": weak_image_hit_count,
            "visual_external_fallback_triggered": bool(external_visual_results),
            "raw_rag_packages": [package.model_dump() for package in packages],
            "answer_visible_evidence": answer_visible_evidence.model_dump(),
            "external_visual_results": [
                result.model_dump() for result in external_visual_results
            ],
            "web_results": [result.model_dump() for result in web_results],
            "citations": [
                {
                    "marker": citation.marker,
                    "title": citation.title,
                    "source_name": citation.source_name,
                    "url": citation.url,
                    "snippet": citation.snippet,
                }
                for citation in citations
            ],
        }
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": json.dumps(
                    synthesis_payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
            }
        ]
        return content

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

    def _has_strong_image_evidence(
        self,
        packages: list[ArticlePackage],
        *,
        query: str | None,
    ) -> bool:
        return any(
            self._is_strong_image_hit(hit, query=query)
            for package in packages
            for hit in package.image_hits
        )

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

    async def _synthesize_answer(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
        answer_visible_evidence: AnswerVisibleEvidence,
        web_results: list[WebSearchResult],
        external_visual_results: list[ExternalVisualResult],
        citations: list[AnswerCitation],
    ) -> str:
        synthesis_agent = self._get_synthesis_agent()
        result = await synthesis_agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": self._build_synthesis_user_content(
                            request=request,
                            request_context=request_context,
                            packages=packages,
                            answer_visible_evidence=answer_visible_evidence,
                            web_results=web_results,
                            external_visual_results=external_visual_results,
                            citations=citations,
                        ),
                    }
                ]
            }
        )
        answer = self._extract_agent_answer(result).strip()
        if not answer:
            raise ValueError("rag answer synthesis returned empty content")
        return answer

    async def _synthesize_answer_stream(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
        answer_visible_evidence: AnswerVisibleEvidence,
        web_results: list[WebSearchResult],
        external_visual_results: list[ExternalVisualResult],
        citations: list[AnswerCitation],
        on_delta: AsyncDeltaHandler,
    ) -> str:
        synthesis_agent = self._get_synthesis_agent()
        answer_parts: list[str] = []
        async for event in synthesis_agent.astream_events(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": self._build_synthesis_user_content(
                            request=request,
                            request_context=request_context,
                            packages=packages,
                            answer_visible_evidence=answer_visible_evidence,
                            web_results=web_results,
                            external_visual_results=external_visual_results,
                            citations=citations,
                        ),
                    }
                ]
            },
            version="v2",
        ):
            delta_text = self._extract_stream_delta_text(event)
            if not delta_text:
                continue
            answer_parts.append(delta_text)
            await on_delta(delta_text)

        answer = "".join(answer_parts).strip()
        if not answer:
            raise ValueError("rag answer synthesis returned empty content")
        return answer

    def _build_research_agent(self, rag_tools: RagTools):
        if self._research_agent_factory is not None:
            return self._research_agent_factory(rag_tools)
        return create_agent(
            model=build_rag_model(self._configuration),
            tools=rag_tools.build_langchain_tools(),
            system_prompt=RAG_TOOL_LOOP_PROMPT,
        )

    def _get_synthesis_agent(self):
        if self._synthesis_agent is None:
            self._synthesis_agent = create_agent(
                model=build_rag_model(self._configuration),
                tools=[],
                system_prompt=RAG_ANSWER_SYNTHESIS_PROMPT,
            )
        return self._synthesis_agent

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
