"""Brave Search integration for external evidence retrieval."""

from __future__ import annotations

from typing import Any

import aiohttp

from backend.app.config.search_config import BRAVE_SEARCH_CONFIG
from backend.app.schemas.rag_api import WebSearchResult


class WebSearchService:
    """Call Brave Search and normalize results for the answer layer."""

    def __init__(self) -> None:
        api_key = BRAVE_SEARCH_CONFIG.api_key
        if not api_key:
            raise ValueError("missing BRAVE_API_KEY for Brave Search")
        self._api_key = api_key
        self._base_url = BRAVE_SEARCH_CONFIG.base_url
        self._country = BRAVE_SEARCH_CONFIG.country
        self._search_lang = BRAVE_SEARCH_CONFIG.search_lang
        self._timeout_seconds = BRAVE_SEARCH_CONFIG.timeout_seconds

    async def search(self, *, query: str, limit: int) -> list[WebSearchResult]:
        """Run one Brave web search request."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("web search query must not be empty")
        if limit <= 0:
            raise ValueError("web search limit must be greater than 0")

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._api_key,
        }
        params = {
            "q": normalized_query,
            "count": str(limit),
            "country": self._country,
            "search_lang": self._search_lang,
        }

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(self._base_url, params=params) as response:
                if response.status != 200:
                    body = await response.text()
                    raise ValueError(
                        "brave search failed: "
                        f"status={response.status} body={body[:500]}"
                    )
                payload = await response.json()

        return self._parse_results(payload=payload, limit=limit)

    def _parse_results(self, *, payload: dict[str, Any], limit: int) -> list[WebSearchResult]:
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
                )
            )
            if len(results) >= limit:
                break
        return results

    def _extract_snippet(self, item: dict[str, Any]) -> str:
        description = str(item.get("description") or "").strip()
        if description:
            return description

        extra_snippets = item.get("extra_snippets")
        if isinstance(extra_snippets, list):
            normalized = [str(snippet).strip() for snippet in extra_snippets if str(snippet).strip()]
            if normalized:
                return " ".join(normalized)
        return ""
