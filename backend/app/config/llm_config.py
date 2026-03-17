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
    api_key_env: str
    base_url_env: str
    timeout_seconds: int = 120
    fallback_api_key_envs: tuple[str, ...] = ()
    fallback_base_url_envs: tuple[str, ...] = ()
    default_base_url: str | None = None

    @property
    def api_key(self) -> str | None:
        candidates = [self.api_key_env, *self.fallback_api_key_envs, "OPENAI_API_KEY"]
        for env_name in candidates:
            value = os.getenv(env_name, "").strip()
            if value:
                return value
        return None

    @property
    def base_url(self) -> str | None:
        candidates = [self.base_url_env, *self.fallback_base_url_envs, "OPENAI_BASE_URL"]
        for env_name in candidates:
            value = os.getenv(env_name, "").strip()
            if value:
                return value
        return self.default_base_url or DEFAULT_OPENAI_BASE_URL

# minimax系列模型在文本理解和生成方面表现优异，适合文章解析和故事总结等任务
# kimi系列模型在多模态理解方面表现更好，适合图像分析和RAG以图搜图等任务


IMAGE_ANALYSIS_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("IMAGE_ANALYSIS_MODEL", "kimi/kimi-k2.5"),
    temperature=0.1,
    api_key_env="IMAGE_ANALYSIS_API_KEY",
    base_url_env="IMAGE_ANALYSIS_BASE_URL",
)

STORY_SUMMARIZATION_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("STORY_SUMMARIZATION_MODEL", "google/gemini-2.5-flash"),
    temperature=0.3,
    api_key_env="STORY_SUMMARIZATION_API_KEY",
    base_url_env="STORY_SUMMARIZATION_BASE_URL",
    fallback_api_key_envs=("OPENROUTER_API_KEY",),
    fallback_base_url_envs=("OPENROUTER_BASE_URL",),
    default_base_url="https://openrouter.ai/api/v1",
)

RAG_CHAT_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("RAG_CHAT_MODEL", "kimi/kimi-k2.5"),
    temperature=0.2,
    api_key_env="RAG_CHAT_API_KEY",
    base_url_env="RAG_CHAT_BASE_URL",
)
