"""Central LLM and VLM model configuration."""

from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import find_dotenv, load_dotenv
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict

_ = load_dotenv(find_dotenv())

DEFAULT_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "story_summarization": {
        "model": "kimi-k2.5",
        "temperature": 0.3,
        "timeout_seconds": 600,
        "max_completion_tokens": 4000,
        "max_structured_output_retries": 2,
    },
    "rag": {
        "model": "kimi-k2.5",
        "temperature": 0.2,
        "timeout_seconds": 120,
        "max_completion_tokens": 2000,
        "max_structured_output_retries": 2,
    },
    "image_analysis": {
        "model": "qwen3.5-plus",
        "temperature": 0.6,
        "timeout_seconds": 120,
        "max_completion_tokens": 2000,
        "max_structured_output_retries": 2,
    },
}


class Configuration(BaseModel):
    """Runtime configuration for OpenAI-compatible models."""

    model_config = ConfigDict(frozen=True)

    api_key: str | None = None
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str
    temperature: float
    timeout_seconds: int
    max_completion_tokens: int
    max_structured_output_retries: int

    @property
    def model_name(self) -> str:
        return self.model

    @classmethod
    def from_runnable_config(
        cls,
        profile: Literal["story_summarization", "rag", "image_analysis"],
        runnable_config: RunnableConfig | None = None,
    ) -> Configuration:
        defaults = _PROFILE_DEFAULTS[profile]
        configurable = {}
        if isinstance(runnable_config, dict):
            raw_configurable = runnable_config.get("configurable", {})
            if isinstance(raw_configurable, dict):
                configurable = raw_configurable
        prefix = profile.upper()
        data = {
            "api_key": _resolve_string(
                env_keys=("OPENAI_API_KEY",),
                configurable=configurable,
                configurable_keys=("api_key", f"{profile}_api_key"),
                default=None,
            ),
            "base_url": _resolve_string(
                env_keys=("OPENAI_BASE_URL",),
                configurable=configurable,
                configurable_keys=("base_url", f"{profile}_base_url"),
                default=DEFAULT_OPENAI_BASE_URL,
            ),
            "model": _resolve_string(
                env_keys=(f"{prefix}_MODEL",),
                configurable=configurable,
                configurable_keys=("model", f"{profile}_model"),
                default=defaults["model"],
            ),
            "temperature": _resolve_numeric(
                env_keys=(f"{prefix}_TEMPERATURE",),
                configurable=configurable,
                configurable_keys=("temperature", f"{profile}_temperature"),
                default=defaults["temperature"],
            ),
            "timeout_seconds": _resolve_numeric(
                env_keys=(f"{prefix}_TIMEOUT_SECONDS",),
                configurable=configurable,
                configurable_keys=("timeout_seconds", f"{profile}_timeout_seconds"),
                default=defaults["timeout_seconds"],
            ),
            "max_completion_tokens": _resolve_numeric(
                env_keys=(f"{prefix}_MAX_COMPLETION_TOKENS",),
                configurable=configurable,
                configurable_keys=("max_completion_tokens", f"{profile}_max_completion_tokens"),
                default=defaults["max_completion_tokens"],
            ),
            "max_structured_output_retries": _resolve_numeric(
                env_keys=(f"{prefix}_MAX_STRUCTURED_OUTPUT_RETRIES",),
                configurable=configurable,
                configurable_keys=(
                    "max_structured_output_retries",
                    f"{profile}_max_structured_output_retries",
                ),
                default=defaults["max_structured_output_retries"],
            ),
        }
        return cls.model_validate(data)


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


ModelConfig = Configuration

IMAGE_ANALYSIS_MODEL_CONFIG = Configuration.from_runnable_config(profile="image_analysis")
STORY_SUMMARIZATION_MODEL_CONFIG = Configuration.from_runnable_config(profile="story_summarization")
RAG_CHAT_MODEL_CONFIG = Configuration.from_runnable_config(profile="rag")
