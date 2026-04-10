"""External search provider configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


@dataclass(frozen=True)
class BraveSearchConfig:
    """Configuration for the Brave Search API."""

    web_base_url: str
    image_base_url: str
    llm_context_base_url: str
    country: str
    search_lang: str
    timeout_seconds: int
    llm_context_count: int
    llm_context_max_urls: int
    llm_context_max_snippets: int
    llm_context_max_tokens: int
    api_key_env: str = "BRAVE_API_KEY"

    @property
    def api_key(self) -> str | None:
        value = os.getenv(self.api_key_env, "").strip()
        return value or None


BRAVE_SEARCH_CONFIG = BraveSearchConfig(
    web_base_url=os.getenv("BRAVE_WEB_SEARCH_BASE_URL", "https://api.search.brave.com/res/v1/web/search"),
    image_base_url=os.getenv(
        "BRAVE_IMAGE_SEARCH_BASE_URL",
        "https://api.search.brave.com/res/v1/images/search",
    ),
    llm_context_base_url=os.getenv(
        "BRAVE_LLM_CONTEXT_BASE_URL",
        "https://api.search.brave.com/res/v1/llm/context",
    ),
    country=os.getenv("BRAVE_SEARCH_COUNTRY", "US"),
    search_lang=os.getenv("BRAVE_SEARCH_LANGUAGE", "en"),
    timeout_seconds=int(os.getenv("BRAVE_SEARCH_TIMEOUT_SECONDS", "30")),
    llm_context_count=int(os.getenv("BRAVE_LLM_CONTEXT_COUNT", "5")),
    llm_context_max_urls=int(os.getenv("BRAVE_LLM_CONTEXT_MAX_URLS", "5")),
    llm_context_max_snippets=int(os.getenv("BRAVE_LLM_CONTEXT_MAX_SNIPPETS", "20")),
    llm_context_max_tokens=int(os.getenv("BRAVE_LLM_CONTEXT_MAX_TOKENS", "4096")),
)


@dataclass(frozen=True)
class TavilySearchConfig:
    """Configuration for the Tavily Search API."""

    base_url: str
    topic: str
    search_depth: str
    timeout_seconds: int
    include_raw_content: bool
    api_key_env: str = "TAVILY_API_KEY"

    @property
    def api_key(self) -> str | None:
        value = os.getenv(self.api_key_env, "").strip()
        return value or None


TAVILY_SEARCH_CONFIG = TavilySearchConfig(
    base_url=os.getenv("TAVILY_SEARCH_BASE_URL", "https://api.tavily.com/search"),
    topic=os.getenv("TAVILY_SEARCH_TOPIC", "general"),
    search_depth=os.getenv("TAVILY_SEARCH_DEPTH", "advanced"),
    timeout_seconds=int(os.getenv("TAVILY_SEARCH_TIMEOUT_SECONDS", "30")),
    include_raw_content=os.getenv("TAVILY_INCLUDE_RAW_CONTENT", "true").strip().lower() in {"1", "true", "yes", "on"},
)


DEFAULT_WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
