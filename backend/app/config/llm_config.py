"""Central LLM and VLM model configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())

DEFAULT_OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    temperature: float
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url_env: str = "OPENAI_BASE_URL"
    timeout_seconds: int = 120
    default_base_url: str | None = None

    @property
    def api_key(self) -> str | None:
        value = os.getenv(self.api_key_env, "").strip()
        return value or None

    @property
    def base_url(self) -> str | None:
        value = os.getenv(self.base_url_env, "").strip()
        if value:
            return value
        return self.default_base_url or DEFAULT_OPENAI_BASE_URL


IMAGE_ANALYSIS_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("IMAGE_ANALYSIS_MODEL", "kimi-k2.5"),
    temperature=0.6,
    api_key_env="DASHSCOPE_API_KEY",
    base_url_env="IMAGE_ANALYSIS_BASE_URL",
    default_base_url=DEFAULT_OPENAI_BASE_URL,
)

STORY_SUMMARIZATION_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("STORY_SUMMARIZATION_MODEL", "qwen-plus"),
    temperature=0.3,
    api_key_env="DASHSCOPE_API_KEY",
    base_url_env="STORY_SUMMARIZATION_BASE_URL",
    default_base_url=DEFAULT_OPENAI_BASE_URL,
)

RAG_CHAT_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("RAG_CHAT_MODEL", "kimi-k2.5"),
    temperature=0.2,
    api_key_env="DASHSCOPE_API_KEY",
    base_url_env="RAG_CHAT_BASE_URL",
    default_base_url=DEFAULT_OPENAI_BASE_URL,
)
