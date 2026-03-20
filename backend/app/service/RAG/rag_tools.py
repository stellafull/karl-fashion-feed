"""Deterministic retrieval tools for the quick-query retrieval core."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.schemas.rag_query import QueryFilters, QueryPlan, QueryResult, TimeRange
from backend.app.service.RAG.query_service import QueryService


class RagTools:
    """Build retrieval plans from tool arguments and execute them."""

    def __init__(self, *, query_service: QueryService | None = None) -> None:
        self._query_service = query_service or QueryService()

    def search_fashion_articles(
        self,
        *,
        query: str,
        brands: list[str] | None = None,
        categories: list[str] | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        include_images: bool = False,
        limit: int = 10,
    ) -> QueryResult:
        """Search fashion articles with an optional fused image lane."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search_fashion_articles requires a non-empty query")

        query_plan = QueryPlan(
            plan_type="fusion" if include_images else "text_only",
            text_query=normalized_query,
            filters=self._build_filters(
                categories=categories,
                brands=brands,
                start_at=start_at,
                end_at=end_at,
            ),
            output_goal="reference_lookup",
            limit=limit,
        )
        return self._query_service.execute(query_plan)

    def search_fashion_images(
        self,
        *,
        text_query: str | None = None,
        image_url: str | None = None,
        brands: list[str] | None = None,
        categories: list[str] | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = 10,
    ) -> QueryResult:
        """Search fashion images by text or by example image."""
        normalized_text_query = (text_query or "").strip() or None
        normalized_image_url = (image_url or "").strip() or None
        if normalized_text_query is not None and normalized_image_url is not None:
            raise ValueError("search_fashion_images accepts exactly one of text_query or image_url")
        if normalized_text_query is None and normalized_image_url is None:
            raise ValueError("search_fashion_images requires text_query or image_url")

        query_plan = QueryPlan(
            plan_type="image_only",
            text_query=normalized_text_query,
            image_query=normalized_image_url,
            filters=self._build_filters(
                categories=categories,
                brands=brands,
                start_at=start_at,
                end_at=end_at,
            ),
            output_goal="similarity_search" if normalized_image_url else "inspiration",
            limit=limit,
        )
        return self._query_service.execute(query_plan)

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> QueryResult:
        """Dispatch one named tool with validated arguments."""
        if tool_name == "search_fashion_articles":
            return self.search_fashion_articles(**arguments)
        if tool_name == "search_fashion_images":
            return self.search_fashion_images(**arguments)
        raise ValueError(f"unsupported tool: {tool_name}")

    def _build_filters(
        self,
        *,
        categories: list[str] | None,
        brands: list[str] | None,
        start_at: str | None,
        end_at: str | None,
    ) -> QueryFilters:
        time_range = None
        parsed_start_at = self._parse_optional_datetime(start_at)
        parsed_end_at = self._parse_optional_datetime(end_at)
        if parsed_start_at is not None or parsed_end_at is not None:
            time_range = TimeRange(start_at=parsed_start_at, end_at=parsed_end_at)
        return QueryFilters(
            categories=self._normalize_terms(categories),
            brands=self._normalize_terms(brands),
            time_range=time_range,
        )

    def _normalize_terms(self, value: list[str] | None) -> list[str]:
        return [term.strip() for term in value or [] if term and term.strip()]

    def _parse_optional_datetime(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            return None
        if normalized_value.endswith("Z"):
            normalized_value = normalized_value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized_value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
