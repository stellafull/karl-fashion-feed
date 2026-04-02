"""Central LLM and VLM model configuration."""

from __future__ import annotations

import os
from typing import Any

from dotenv import find_dotenv, load_dotenv
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict

_ = load_dotenv(find_dotenv())

DEFAULT_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class Configuration(BaseModel):
    """Runtime configuration for OpenAI-compatible story and RAG models."""

    model_config = ConfigDict(frozen=True)

    base_url: str = DEFAULT_OPENAI_BASE_URL
    api_key: str | None = None
    max_structured_output_retries: int = 3
    story_summarization_model: str = "kimi-k2.5"
    story_summarization_model_max_tokens: int = 4000
    story_summarization_temperature: float = 0.0
    story_summarization_timeout_seconds: int = 600
    rag_model: str = "kimi-k2.5"
    rag_model_max_tokens: int = 2000
    rag_temperature: float = 0.2
    rag_timeout_seconds: int = 120
    max_react_tool_calls: int = 8

    @classmethod
    def from_runnable_config(cls, runnable_config: RunnableConfig | None = None) -> Configuration:
        configurable = _extract_configurable(runnable_config)
        data = {
            "base_url": _resolve_string(
                env_keys=("OPENAI_BASE_URL",),
                configurable=configurable,
                configurable_keys=("base_url",),
                default=DEFAULT_OPENAI_BASE_URL,
            ),
            "api_key": _resolve_string(
                env_keys=("OPENAI_API_KEY",),
                configurable=configurable,
                configurable_keys=("api_key",),
                default=None,
            ),
            "max_structured_output_retries": _resolve_numeric(
                env_keys=("MAX_STRUCTURED_OUTPUT_RETRIES", "STORY_SUMMARIZATION_MAX_STRUCTURED_OUTPUT_RETRIES"),
                configurable=configurable,
                configurable_keys=("max_structured_output_retries",),
                default=3,
            ),
            "story_summarization_model": _resolve_string(
                env_keys=("STORY_SUMMARIZATION_MODEL",),
                configurable=configurable,
                configurable_keys=("story_summarization_model",),
                default="kimi-k2.5",
            ),
            "story_summarization_model_max_tokens": _resolve_numeric(
                env_keys=("STORY_SUMMARIZATION_MODEL_MAX_TOKENS", "STORY_SUMMARIZATION_MAX_COMPLETION_TOKENS"),
                configurable=configurable,
                configurable_keys=("story_summarization_model_max_tokens",),
                default=4000,
            ),
            "story_summarization_temperature": _resolve_numeric(
                env_keys=("STORY_SUMMARIZATION_TEMPERATURE",),
                configurable=configurable,
                configurable_keys=("story_summarization_temperature",),
                default=0.0,
            ),
            "story_summarization_timeout_seconds": _resolve_numeric(
                env_keys=("STORY_SUMMARIZATION_TIMEOUT_SECONDS",),
                configurable=configurable,
                configurable_keys=("story_summarization_timeout_seconds",),
                default=600,
            ),
            "rag_model": _resolve_string(
                env_keys=("RAG_CHAT_MODEL", "RAG_MODEL"),
                configurable=configurable,
                configurable_keys=("rag_model",),
                default="kimi-k2.5",
            ),
            "rag_model_max_tokens": _resolve_numeric(
                env_keys=("RAG_MODEL_MAX_TOKENS", "RAG_MAX_COMPLETION_TOKENS"),
                configurable=configurable,
                configurable_keys=("rag_model_max_tokens",),
                default=2000,
            ),
            "rag_temperature": _resolve_numeric(
                env_keys=("RAG_TEMPERATURE",),
                configurable=configurable,
                configurable_keys=("rag_temperature",),
                default=0.2,
            ),
            "rag_timeout_seconds": _resolve_numeric(
                env_keys=("RAG_TIMEOUT_SECONDS",),
                configurable=configurable,
                configurable_keys=("rag_timeout_seconds",),
                default=120,
            ),
            "max_react_tool_calls": _resolve_numeric(
                env_keys=("MAX_REACT_TOOL_CALLS",),
                configurable=configurable,
                configurable_keys=("max_react_tool_calls",),
                default=8,
            ),
        }
        return cls.model_validate(data)


class ModelConfig(BaseModel):
    """Legacy profile model contract kept for existing services."""

    model_config = ConfigDict(frozen=True)

    model_name: str
    temperature: float
    timeout_seconds: int
    base_url: str = DEFAULT_OPENAI_BASE_URL
    api_key: str | None = None


def _extract_configurable(runnable_config: RunnableConfig | None) -> dict[str, Any]:
    if not isinstance(runnable_config, dict):
        return {}
    configurable = runnable_config.get("configurable")
    if not isinstance(configurable, dict):
        return {}
    return configurable


def _resolve_string(
    *,
    env_keys: tuple[str, ...],
    configurable: dict[str, Any],
    configurable_keys: tuple[str, ...],
    default: str | None,
) -> str | None:
    for env_key in env_keys:
        value = os.getenv(env_key)
        if value is not None and value.strip():
            return value.strip()
    for key in configurable_keys:
        value = configurable.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _resolve_numeric(
    *,
    env_keys: tuple[str, ...],
    configurable: dict[str, Any],
    configurable_keys: tuple[str, ...],
    default: int | float,
) -> Any:
    for env_key in env_keys:
        value = os.getenv(env_key)
        if value is not None and value.strip():
            return value.strip()
    for key in configurable_keys:
        value = configurable.get(key)
        if value is not None:
            return value
    return default


_GLOBAL_CONFIGURATION = Configuration.from_runnable_config()

IMAGE_ANALYSIS_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("IMAGE_ANALYSIS_MODEL", "qwen3.5-plus").strip() or "qwen3.5-plus",
    temperature=float(os.getenv("IMAGE_ANALYSIS_TEMPERATURE", "0.6")),
    timeout_seconds=int(os.getenv("IMAGE_ANALYSIS_TIMEOUT_SECONDS", "120")),
    base_url=_GLOBAL_CONFIGURATION.base_url,
    api_key=_GLOBAL_CONFIGURATION.api_key,
)

STORY_SUMMARIZATION_MODEL_CONFIG = ModelConfig(
    model_name=_GLOBAL_CONFIGURATION.story_summarization_model,
    temperature=_GLOBAL_CONFIGURATION.story_summarization_temperature,
    timeout_seconds=_GLOBAL_CONFIGURATION.story_summarization_timeout_seconds,
    base_url=_GLOBAL_CONFIGURATION.base_url,
    api_key=_GLOBAL_CONFIGURATION.api_key,
)

RAG_CHAT_MODEL_CONFIG = ModelConfig(
    model_name=_GLOBAL_CONFIGURATION.rag_model,
    temperature=_GLOBAL_CONFIGURATION.rag_temperature,
    timeout_seconds=_GLOBAL_CONFIGURATION.rag_timeout_seconds,
    base_url=_GLOBAL_CONFIGURATION.base_url,
    api_key=_GLOBAL_CONFIGURATION.api_key,
)
