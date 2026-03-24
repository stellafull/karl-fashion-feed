"""Answer-layer orchestration over internal RAG tools and Brave search."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from backend.app.config.llm_config import RAG_CHAT_MODEL_CONFIG
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
from backend.app.service.RAG.rag_tools import RagTools, ToolExecutionResult

MAX_TOOL_CALLS = 3
CITATION_MARKER_PATTERN = re.compile(r"\[([A-Za-z]\d+)\]")
AsyncDeltaHandler = Callable[[str], Awaitable[None]]


class RagAnswerService:
    """Run the multimodal tool loop and synthesize the final grounded answer."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        tools_factory: Callable[[RagRequestContext], RagTools] | None = None,
    ) -> None:
        if client is None:
            api_key = RAG_CHAT_MODEL_CONFIG.api_key
            if not api_key:
                raise ValueError(f"missing API key for {RAG_CHAT_MODEL_CONFIG.model_name}")
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=RAG_CHAT_MODEL_CONFIG.base_url,
                timeout=RAG_CHAT_MODEL_CONFIG.timeout_seconds,
            )
        self._client = client
        self._tools_factory = (
            (lambda request_context: RagTools(request_context=request_context))
            if tools_factory is None
            else tools_factory
        )

    async def answer(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        conversation_compact: str | None = None,
        recent_messages: list[dict] | None = None,
        user_memories: list[dict] | None = None,
    ) -> RagAnswerResponse:
        """Execute retrieval tools and synthesize one final Chinese answer.

        Args:
            request: RAG query request
            request_context: Request context with filters and uploaded images
            conversation_compact: Compressed conversation history (for chat worker)
            recent_messages: Recent 5 messages (for chat worker)
            user_memories: User's long-term memories (for chat worker)
        """
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
        messages = [
            {"role": "system", "content": RAG_TOOL_LOOP_PROMPT},
        ]

        if conversation_compact or recent_messages or user_memories:
            context_parts = []
            if conversation_compact:
                context_parts.append(f"历史对话摘要：{conversation_compact}")
            if user_memories:
                memories_text = "\n".join(
                    [
                        f"- {mem['type']}/{mem['key']}: {mem['value']}"
                        for mem in user_memories
                    ]
                )
                context_parts.append(f"用户记忆：\n{memories_text}")
            if recent_messages:
                messages_text = "\n".join(
                    [f"{msg['role']}: {msg['content']}" for msg in recent_messages]
                )
                context_parts.append(f"最近对话：\n{messages_text}")

            if context_parts:
                messages.append(
                    {
                        "role": "user",
                        "content": "\n\n".join(context_parts),
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": self._build_tool_loop_user_content(
                    request=request,
                    request_context=request_context,
                ),
            }
        )
        rag_results: list[QueryResult] = []
        web_results: list[WebSearchResult] = []
        tool_call_count = 0

        while tool_call_count < MAX_TOOL_CALLS:
            response = await self._client.chat.completions.create(
                model=RAG_CHAT_MODEL_CONFIG.model_name,
                temperature=RAG_CHAT_MODEL_CONFIG.temperature,
                messages=messages,
                tools=rag_tools.build_tool_definitions(),
                tool_choice="auto",
            )
            message = response.choices[0].message
            messages.append(self._serialize_assistant_message(message))
            tool_calls = list(message.tool_calls or [])
            if not tool_calls:
                break

            remaining_tool_budget = MAX_TOOL_CALLS - tool_call_count
            if remaining_tool_budget <= 0:
                break

            for tool_call in tool_calls[:remaining_tool_budget]:
                result = await rag_tools.execute_tool(
                    tool_call.function.name,
                    self._parse_tool_arguments(tool_call.function.arguments),
                )
                self._accumulate_tool_result(
                    result=result,
                    rag_results=rag_results,
                    web_results=web_results,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": rag_tools.serialize_tool_result(result),
                    }
                )
                tool_call_count += 1

        packages = self._merge_rag_packages(rag_results)
        query_plans = [result.query_plan for result in rag_results]
        unique_web_results = self._deduplicate_web_results(web_results)
        citations = self._build_citations(
            packages=packages,
            web_results=unique_web_results,
        )
        return packages, query_plans, unique_web_results, citations

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

    def _serialize_assistant_message(self, message: Any) -> dict[str, object]:
        serialized_message: dict[str, object] = {"role": "assistant"}
        content = self._extract_message_content(message.content)
        serialized_message["content"] = content
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            serialized_message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in tool_calls
            ]
        return serialized_message

    def _parse_tool_arguments(self, raw_arguments: str) -> dict[str, Any]:
        if not raw_arguments.strip():
            return {}
        parsed = json.loads(raw_arguments)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must decode to a JSON object")
        return parsed

    def _accumulate_tool_result(
        self,
        *,
        result: ToolExecutionResult,
        rag_results: list[QueryResult],
        web_results: list[WebSearchResult],
    ) -> None:
        if isinstance(result, QueryResult):
            rag_results.append(result)
            return
        web_results.extend(result)

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
                existing_package.title_zh = existing_package.title_zh or package.title_zh
                existing_package.summary_zh = existing_package.summary_zh or package.summary_zh
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
                        title=package.title_zh,
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
                            title=package.title_zh,
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

    async def _synthesize_answer(
        self,
        *,
        request: RagQueryRequest,
        request_context: RagRequestContext,
        packages: list[ArticlePackage],
        web_results: list[WebSearchResult],
        citations: list[AnswerCitation],
    ) -> str:
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

        response = await self._client.chat.completions.create(
            model=RAG_CHAT_MODEL_CONFIG.model_name,
            temperature=RAG_CHAT_MODEL_CONFIG.temperature,
            messages=[
                {"role": "system", "content": RAG_ANSWER_SYNTHESIS_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        answer = self._extract_message_content(response.choices[0].message.content).strip()
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

        stream = await self._client.chat.completions.create(
            model=RAG_CHAT_MODEL_CONFIG.model_name,
            temperature=RAG_CHAT_MODEL_CONFIG.temperature,
            messages=[
                {"role": "system", "content": RAG_ANSWER_SYNTHESIS_PROMPT},
                {"role": "user", "content": content},
            ],
            stream=True,
        )
        answer_parts: list[str] = []
        async for chunk in stream:
            for choice in chunk.choices:
                delta_text = choice.delta.content or ""
                if not delta_text:
                    continue
                answer_parts.append(delta_text)
                await on_delta(delta_text)

        answer = "".join(answer_parts).strip()
        if not answer:
            raise ValueError("rag answer synthesis returned empty content")
        return answer

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
