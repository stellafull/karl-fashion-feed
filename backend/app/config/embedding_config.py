"""Embedding model configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


@dataclass(frozen=True)
class EmbeddingModelConfig:
    model_name: str
    vector_dimension: int | None = None


DENSE_EMBEDDING_CONFIG = EmbeddingModelConfig(
    model_name=os.getenv("DENSE_EMBEDDING_MODEL", "qwen3-vl-embedding"),
    vector_dimension=int(os.getenv("DENSE_EMBEDDING_DIMENSION", 2560)),
)

SPARSE_EMBEDDING_CONFIG = EmbeddingModelConfig(
    model_name=os.getenv("SPARSE_EMBEDDING_MODEL", "text-embedding-v4"),
    vector_dimension=None,
)
