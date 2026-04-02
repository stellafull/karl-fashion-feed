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
    AnswerCitation,
    RagAnswerResponse,
    RagQueryRequest,
    RagRequestContext,
    WebSearchResult,
)
from backend.app.schemas.rag_query import ArticlePackage, QueryResult, RetrievalHit
from backend.app.service.RAG.rag_tools import RagTools
from backend.app.service.langchain_model_factory import build_rag_model

MAX_TOOL_CALLS = 3
CITATION_MARKER_PATTERN = re.compile(r"\[([A-Za-z]\d+)\]")
AsyncDeltaHandler = Callable[[str], Awaitable[None]]


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
        packages, query_plans, unique_web_results, citations = await self._collect_answer_materials(
            request=request,
            request_context=request_context,
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        answer = await self._synthesize_answer(
            request=request,
            request_context=request_context,
            packages=packages,
            web_results=unique_web_results,
            citations=citations,
        )
        answer = self._normalize_answer_citation_markers(answer, citations)
        return RagAnswerResponse(
            answer=answer,
            citations=citations,
            packages=packages,
            query_plans=query_plans,
            web_results=unique_web_results,
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
        packages, query_plans, unique_web_results, citations = await self._collect_answer_materials(
            request=request,
            request_context=request_context,
            conversation_compact=conversation_compact,
            recent_messages=recent_messages,
            user_memories=user_memories,
        )
        answer = await self._synthesize_answer_stream(
            request=request,
            request_context=request_context,
            packages=packages,
            web_results=unique_web_results,
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
            response = await self.answer(
                request=RagQueryRequest(
                    query=query,
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
        list[AnswerCitation],
    ]:
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
        citations = self._build_citations(
            packages=packages,
            web_results=unique_web_results,
        )
        return packages, query_plans, unique_web_results, citations

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
        web_results: list[WebSearchResult],
        citations: list[AnswerCitation],
    ) -> list[dict[str, object]]:
        synthesis_payload = {
            "user_query": request.query or "",
            "has_request_images": request_context.has_request_images,
            "request_image_count": len(request_context.request_images),
            "rag_packages": [package.model_dump() for package in packages],
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
        for request_image in request_context.request_images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": request_image.to_data_url()},
                }
            )
        return content

    async def _synthesize_answer(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
        web_results: list[WebSearchResult],
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
                            web_results=web_results,
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
        web_results: list[WebSearchResult],
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
                            web_results=web_results,
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
        return (MAX_TOOL_CALLS * 2) + 1

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
