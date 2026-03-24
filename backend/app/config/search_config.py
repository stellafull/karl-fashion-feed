"""External search provider configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


@dataclass(frozen=True)
class BraveSearchConfig:
    """Configuration for the Brave Search API."""

    base_url: str
    country: str
    search_lang: str
    timeout_seconds: int
    api_key_env: str = "BRAVE_API_KEY"

    @property
    def api_key(self) -> str | None:
        value = os.getenv(self.api_key_env, "").strip()
        return value or None


BRAVE_SEARCH_CONFIG = BraveSearchConfig(
    base_url=os.getenv("BRAVE_SEARCH_BASE_URL", "https://api.search.brave.com/res/v1/web/search"),
    country=os.getenv("BRAVE_SEARCH_COUNTRY", "US"),
    search_lang=os.getenv("BRAVE_SEARCH_LANGUAGE", "en"),
    timeout_seconds=int(os.getenv("BRAVE_SEARCH_TIMEOUT_SECONDS", "30")),
)
