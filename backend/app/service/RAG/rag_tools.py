"""LLM-facing retrieval tools for the RAG answer API."""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from backend.app.schemas.rag_api import RagRequestContext, WebSearchResult
from backend.app.schemas.rag_query import QueryPlan, QueryResult, REQUEST_IMAGE_REF
from backend.app.service.RAG.query_service import QueryService
from backend.app.service.RAG.web_search_service import WebSearchService

ToolExecutionResult = QueryResult | list[WebSearchResult]
REQUEST_IMAGE_TOOL_REF = "request_image"


class _SearchFashionArticlesArgs(BaseModel):
    query: str = Field(description="The text query used for article retrieval.")


class _SearchFashionImagesArgs(BaseModel):
    text_query: str | None = Field(
        default=None,
        description="Optional text query for text-to-image retrieval.",
    )
    image_ref: Literal["request_image"] | None = Field(
        default=None,
        description="Use request_image to search with the uploaded request images.",
    )


class _SearchFashionFusionArgs(BaseModel):
    query: str = Field(description="The text query used for fusion retrieval.")
    image_ref: Literal["request_image"] | None = Field(
        default=None,
        description="Use request_image to include the uploaded request images in fusion retrieval.",
    )


class _SearchWebArgs(BaseModel):
    query: str = Field(description="The external web search query.")


class RagTools:
    """Expose deterministic retrieval tools to the answer-layer LLM."""

    def __init__(
        self,
        *,
        request_context: RagRequestContext,
        query_service: QueryService | None = None,
        web_search_service: WebSearchService | None = None,
    ) -> None:
        self._request_context = request_context
        self._query_service = QueryService() if query_service is None else query_service
        self._web_search_service = (
            WebSearchService() if web_search_service is None else web_search_service
        )
        self._rag_results: list[QueryResult] = []
        self._web_results: list[WebSearchResult] = []

    def build_langchain_tools(self) -> list[StructuredTool]:
        """Return LangChain tool adapters with in-memory result collection."""
        return [
            StructuredTool.from_function(
                func=self._search_fashion_articles_tool,
                name="search_fashion_articles",
                description="Search Chinese-grounded fashion articles and return text evidence.",
                args_schema=_SearchFashionArticlesArgs,
            ),
            StructuredTool.from_function(
                func=self._search_fashion_images_tool,
                name="search_fashion_images",
                description="Search fashion images either by text or by the uploaded request image.",
                args_schema=_SearchFashionImagesArgs,
            ),
            StructuredTool.from_function(
                func=self._search_fashion_fusion_tool,
                name="search_fashion_fusion",
                description="Run text+image fusion retrieval over fashion evidence packages.",
                args_schema=_SearchFashionFusionArgs,
            ),
            StructuredTool.from_function(
                coroutine=self._search_web_tool,
                name="search_web",
                description="Search the external web for latest information when internal RAG is insufficient.",
                args_schema=_SearchWebArgs,
            ),
        ]

    def get_collected_results(self) -> tuple[list[QueryResult], list[WebSearchResult]]:
        """Return the query and web results gathered through tool execution."""
        return list(self._rag_results), list(self._web_results)

    def search_fashion_articles(self, *, query: str) -> QueryResult:
        """Run text-only article retrieval."""
        query_plan = QueryPlan(
            plan_type="text_only",
            text_query=query,
            filters=self._request_context.filters,
            output_goal="reference_lookup",
            limit=self._request_context.limit,
        )
        return self._query_service.execute(
            query_plan,
            request_images=self._request_context.request_images,
        )

    def search_fashion_images(
        self,
        *,
        text_query: str | None = None,
        image_ref: Literal["request_image"] | None = None,
    ) -> QueryResult:
        """Run text-to-image or image-to-image retrieval."""
        if (text_query is None) == (image_ref is None):
            raise ValueError("search_fashion_images requires exactly one of text_query or image_ref")

        image_query = self._resolve_image_ref(image_ref)
        query_plan = QueryPlan(
            plan_type="image_only",
            text_query=text_query,
            image_query=image_query,
            filters=self._request_context.filters,
            output_goal="similarity_search" if image_query is not None else "inspiration",
            limit=self._request_context.limit,
        )
        return self._query_service.execute(
            query_plan,
            request_images=self._request_context.request_images,
        )

    def search_fashion_fusion(
        self,
        *,
        query: str,
        image_ref: Literal["request_image"] | None = None,
    ) -> QueryResult:
        """Run fusion retrieval with text and an optional request image."""
        query_plan = QueryPlan(
            plan_type="fusion",
            text_query=query,
            image_query=self._resolve_image_ref(image_ref),
            filters=self._request_context.filters,
            output_goal="reference_lookup",
            limit=self._request_context.limit,
        )
        return self._query_service.execute(
            query_plan,
            request_images=self._request_context.request_images,
        )

    async def search_web(self, *, query: str) -> list[WebSearchResult]:
        """Search Brave for external evidence."""
        return await self._web_search_service.search(query=query, limit=self._request_context.limit)

    def _search_fashion_articles_tool(self, query: str) -> str:
        result = self.search_fashion_articles(query=self._require_query(query, field_name="query"))
        return self._record_tool_result(result)

    def _search_fashion_images_tool(
        self,
        text_query: str | None = None,
        image_ref: Literal["request_image"] | None = None,
    ) -> str:
        result = self.search_fashion_images(
            text_query=self._normalize_optional_text(text_query),
            image_ref=self._normalize_optional_image_ref(image_ref),
        )
        return self._record_tool_result(result)

    def _search_fashion_fusion_tool(
        self,
        query: str,
        image_ref: Literal["request_image"] | None = None,
    ) -> str:
        result = self.search_fashion_fusion(
            query=self._require_query(query, field_name="query"),
            image_ref=self._normalize_optional_image_ref(image_ref),
        )
        return self._record_tool_result(result)

    async def _search_web_tool(self, query: str) -> str:
        result = await self.search_web(query=self._require_query(query, field_name="query"))
        return self._record_tool_result(result)

    @staticmethod
    def serialize_tool_result(result: ToolExecutionResult) -> str:
        """Serialize one tool result for tool-call message playback."""
        if isinstance(result, QueryResult):
            return result.model_dump_json(indent=2)
        return json.dumps(
            [item.model_dump() for item in result],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    def _record_tool_result(self, result: ToolExecutionResult) -> str:
        if isinstance(result, QueryResult):
            self._rag_results.append(result)
        else:
            self._web_results.extend(result)
        return self.serialize_tool_result(result)

    def _resolve_image_ref(self, image_ref: Literal["request_image"] | None) -> str | None:
        if image_ref is None:
            return None
        if image_ref != REQUEST_IMAGE_TOOL_REF:
            raise ValueError(f"unsupported image_ref: {image_ref}")
        if not self._request_context.has_request_images:
            raise ValueError("image_ref=request_image requires uploaded request images")
        return REQUEST_IMAGE_REF

    @staticmethod
    def _require_query(value: str, *, field_name: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{field_name} must be a non-empty string")
        return normalized_value

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        return normalized_value or None

    @staticmethod
    def _normalize_optional_image_ref(value: str | None) -> Literal["request_image"] | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            return None
        if normalized_value != REQUEST_IMAGE_TOOL_REF:
            raise ValueError(f"unsupported image_ref: {normalized_value}")
        return REQUEST_IMAGE_TOOL_REF
