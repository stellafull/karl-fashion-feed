"""External search integration for text and visual evidence retrieval."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import aiohttp

from backend.app.config.search_config import (
    BRAVE_SEARCH_CONFIG,
    DEFAULT_WEB_SEARCH_PROVIDER,
    TAVILY_SEARCH_CONFIG,
)
from backend.app.schemas.rag_api import ExternalVisualResult, WebSearchResult

MAX_WEB_RESULT_CONTENT_CHARS = 6000


class WebSearchService:
    """Call external search providers and normalize their results."""

    def __init__(self) -> None:
        self._tavily_api_key = TAVILY_SEARCH_CONFIG.api_key
        self._brave_api_key = BRAVE_SEARCH_CONFIG.api_key
        self._provider = DEFAULT_WEB_SEARCH_PROVIDER

        if not self._tavily_api_key and not self._brave_api_key:
            raise ValueError("missing TAVILY_API_KEY and BRAVE_API_KEY for web search")

    async def search(self, *, query: str, limit: int) -> list[WebSearchResult]:
        """Run one external text/web search request."""
        normalized_query = self._normalize_query(query)
        normalized_limit = self._normalize_limit(limit)

        if self._provider == "tavily" and self._tavily_api_key:
            return await self._search_tavily(query=normalized_query, limit=normalized_limit)

        if self._brave_api_key:
            return await self._search_brave_web(query=normalized_query, limit=normalized_limit)

        return await self._search_tavily(query=normalized_query, limit=normalized_limit)

    async def search_visual(self, *, query: str, limit: int) -> list[ExternalVisualResult]:
        """Run Brave image and LLM-context fallback for visual evidence."""
        normalized_query = self._normalize_query(query)
        normalized_limit = self._normalize_limit(limit)
        if not self._brave_api_key:
            raise ValueError("BRAVE_API_KEY is required for external visual search")

        image_results = await self._search_brave_images(query=normalized_query, limit=normalized_limit)
        try:
            llm_context_results = await self._search_brave_llm_context(
                query=normalized_query,
                limit=min(normalized_limit, BRAVE_SEARCH_CONFIG.llm_context_count),
            )
        except ValueError as error:
            error_text = str(error)
            if "status=429" in error_text or "OPTION_NOT_IN_PLAN" in error_text:
                llm_context_results = []
            else:
                raise
        return [*image_results, *llm_context_results]

    async def _search_tavily(self, *, query: str, limit: int) -> list[WebSearchResult]:
        timeout = aiohttp.ClientTimeout(total=TAVILY_SEARCH_CONFIG.timeout_seconds)
        request_payload = {
            "api_key": self._tavily_api_key,
            "query": query,
            "topic": TAVILY_SEARCH_CONFIG.topic,
            "search_depth": TAVILY_SEARCH_CONFIG.search_depth,
            "include_raw_content": TAVILY_SEARCH_CONFIG.include_raw_content,
            "include_images": False,
            "max_results": limit,
        }

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(TAVILY_SEARCH_CONFIG.base_url, json=request_payload) as response:
                if response.status != 200:
                    body = await response.text()
                    raise ValueError(
                        "tavily search failed: "
                        f"status={response.status} body={body[:500]}"
                    )
                payload = await response.json()

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("tavily search response missing results list")

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            raw_content = str(item.get("raw_content") or "").strip()
            if not title or not url:
                continue
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet[:500],
                    content=(raw_content or snippet)[:MAX_WEB_RESULT_CONTENT_CHARS],
                )
            )
            if len(results) >= limit:
                break
        return results

    async def _search_brave_web(self, *, query: str, limit: int) -> list[WebSearchResult]:
        payload = await self._get_json(
            BRAVE_SEARCH_CONFIG.web_base_url,
            {
                "q": query,
                "count": str(limit),
                "country": BRAVE_SEARCH_CONFIG.country,
                "search_lang": BRAVE_SEARCH_CONFIG.search_lang,
            },
            error_label="brave web search",
        )
        return self._parse_brave_web_results(payload=payload, limit=limit)

    async def _search_brave_images(self, *, query: str, limit: int) -> list[ExternalVisualResult]:
        payload = await self._get_json(
            BRAVE_SEARCH_CONFIG.image_base_url,
            {
                "q": query,
                "count": str(limit),
                "country": BRAVE_SEARCH_CONFIG.country,
                "search_lang": BRAVE_SEARCH_CONFIG.search_lang,
            },
            error_label="brave image search",
        )
        return self._parse_brave_image_results(payload=payload, query=query, limit=limit)

    async def _search_brave_llm_context(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[ExternalVisualResult]:
        payload = await self._get_json(
            BRAVE_SEARCH_CONFIG.llm_context_base_url,
            {
                "q": query,
                "count": str(limit),
                "country": BRAVE_SEARCH_CONFIG.country,
                "search_lang": BRAVE_SEARCH_CONFIG.search_lang,
                "max_snippets": str(BRAVE_SEARCH_CONFIG.llm_context_max_snippets),
                "max_tokens": str(BRAVE_SEARCH_CONFIG.llm_context_max_tokens),
            },
            error_label="brave llm context",
        )
        return self._parse_brave_llm_context_results(payload=payload, query=query, limit=limit)

    async def _get_json(
        self,
        url: str,
        params: dict[str, str],
        *,
        error_label: str,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=BRAVE_SEARCH_CONFIG.timeout_seconds)
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._brave_api_key or "",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    body = await response.text()
                    raise ValueError(
                        f"{error_label} failed: status={response.status} body={body[:500]}"
                    )
                payload = await response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"{error_label} response must be a JSON object")
        return payload

    def _parse_brave_web_results(self, *, payload: dict[str, Any], limit: int) -> list[WebSearchResult]:
        raw_results = payload.get("web", {}).get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("brave search response missing web.results list")

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = self._extract_snippet(item)
            if not title or not url:
                continue
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    content=snippet[:MAX_WEB_RESULT_CONTENT_CHARS],
                )
            )
            if len(results) >= limit:
                break
        return results

    def _parse_brave_image_results(
        self,
        *,
        payload: dict[str, Any],
        query: str,
        limit: int,
    ) -> list[ExternalVisualResult]:
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("brave image search response missing results list")

        results: list[ExternalVisualResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            source_page_url = str(item.get("url") or "").strip()
            if not title or not source_page_url:
                continue

            thumbnail = item.get("thumbnail")
            thumbnail_url = (
                str(thumbnail.get("src") or "").strip()
                if isinstance(thumbnail, dict)
                else ""
            )
            properties = item.get("properties")
            image_url = (
                str(properties.get("url") or "").strip()
                if isinstance(properties, dict)
                else ""
            )
            source_name = str(item.get("source") or "").strip() or self._extract_source_name(
                source_page_url
            )
            results.append(
                ExternalVisualResult(
                    provider="brave_image",
                    query=query,
                    title=title,
                    url=image_url or source_page_url,
                    source_name=source_name or None,
                    source_page_url=source_page_url,
                    image_url=image_url or None,
                    thumbnail_url=thumbnail_url or None,
                    snippet=title,
                    content=title[:MAX_WEB_RESULT_CONTENT_CHARS],
                )
            )
            if len(results) >= limit:
                break
        return results

    def _parse_brave_llm_context_results(
        self,
        *,
        payload: dict[str, Any],
        query: str,
        limit: int,
    ) -> list[ExternalVisualResult]:
        raw_results = self._extract_llm_context_items(payload)

        results: list[ExternalVisualResult] = []
        for item in raw_results:
            title = str(item.get("title") or item.get("name") or "").strip()
            url = str(item.get("url") or item.get("source_page_url") or "").strip()
            content = self._extract_content(item)
            snippet = self._extract_snippet(item)
            if not url:
                continue
            results.append(
                ExternalVisualResult(
                    provider="brave_llm_context",
                    query=query,
                    title=title or self._extract_source_name(url) or url,
                    url=url,
                    source_name=self._extract_source_name(url) or None,
                    source_page_url=url,
                    snippet=snippet,
                    content=content[:MAX_WEB_RESULT_CONTENT_CHARS],
                )
            )
            if len(results) >= limit:
                break
        return results

    def _extract_llm_context_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = [
            payload.get("results"),
            payload.get("sources"),
            payload.get("grounding", {}).get("sources")
            if isinstance(payload.get("grounding"), dict)
            else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        raise ValueError("brave llm context response missing results/sources list")

    def _extract_snippet(self, item: dict[str, Any]) -> str:
        for key in ("description", "snippet", "summary"):
            value = str(item.get(key) or "").strip()
            if value:
                return value

        for key in ("extra_snippets", "snippets"):
            snippets = item.get(key)
            if isinstance(snippets, list):
                normalized = [str(snippet).strip() for snippet in snippets if str(snippet).strip()]
                if normalized:
                    return " ".join(normalized)
        return ""

    def _extract_content(self, item: dict[str, Any]) -> str:
        for key in ("raw_content", "content", "text", "context", "description", "summary"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        nested_content = item.get("source")
        if isinstance(nested_content, dict):
            for key in ("content", "text", "description"):
                value = nested_content.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return self._extract_snippet(item)

    @staticmethod
    def _extract_source_name(url: str) -> str:
        parsed_url = urlparse(url)
        return parsed_url.netloc.strip()

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("web search query must not be empty")
        return normalized_query

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        if limit <= 0:
            raise ValueError("web search limit must be greater than 0")
        return limit
