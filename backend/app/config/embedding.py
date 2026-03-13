"""Embedding provider and model configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


@dataclass(frozen=True)
class EmbeddingConfig:
    embedding_model: str
    embedding_dimension: int


@dataclass(frozen=True)
class EmbeddingModelsConfig:
    sparse_embedding: EmbeddingConfig
    dense_embedding: EmbeddingConfig


def get_sparse_embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
    )


def get_dense_embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        embedding_model=os.getenv("MODALITY_EMBEDDING_MODEL", "qwen3-vl-embedding")
        or "qwen3-vl-embedding",
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "2560") or "2560"),
    )


def get_embedding_models_config() -> EmbeddingModelsConfig:
    return EmbeddingModelsConfig(
        sparse_embedding=get_sparse_embedding_config(),
        dense_embedding=get_dense_embedding_config(),
    )


def get_dashscope_api_key() -> str | None:
    return os.getenv("DASHSCOPE_API_KEY")


def require_dashscope_api_key() -> str:
    api_key = get_dashscope_api_key()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set. Configure the embedding provider before use.")
    return api_key
