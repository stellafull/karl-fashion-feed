"""Central LLM and VLM model configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    temperature: float



ARTICLE_PARSE_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("ARTICLE_PARSE_MODEL", "qwen-plus"),
    temperature=0.0,
)

IMAGE_ANALYSIS_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("IMAGE_ANALYSIS_MODEL", "qwen-vl-max"),
    temperature=0.1,
)

STORY_SUMMARIZATION_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("STORY_SUMMARIZATION_MODEL", "qwen-plus"),
    temperature=0.3,
)

RAG_CHAT_MODEL_CONFIG = ModelConfig(
    model_name=os.getenv("RAG_CHAT_MODEL", "qwen-plus"),
    temperature=0.2,
)
