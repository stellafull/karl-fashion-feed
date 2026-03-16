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
    api_key_env: str = "DASHSCOPE_API_KEY"
    timeout_seconds: int = 120
    batch_size: int = 10

    @property
    def api_key(self) -> str | None:
        value = os.getenv(self.api_key_env, "").strip()
        return value or None



# modality embedding model setup
# dense embedding model for text and image, sparse embedding model for text only
DENSE_EMBEDDING_CONFIG = EmbeddingModelConfig(
    model_name=os.getenv("DENSE_EMBEDDING_MODEL", "qwen3-vl-embedding"),
    vector_dimension=int(os.getenv("DENSE_EMBEDDING_DIMENSION", 2560)),
    batch_size=min(max(int(os.getenv("DENSE_EMBEDDING_BATCH_SIZE", "10")), 1), 10),
)

SPARSE_EMBEDDING_CONFIG = EmbeddingModelConfig(
    model_name=os.getenv("SPARSE_EMBEDDING_MODEL", "text-embedding-v4"),
    vector_dimension=None,
    batch_size=min(max(int(os.getenv("SPARSE_EMBEDDING_BATCH_SIZE", "10")), 1), 10),
)


### summarization story embedding model
### dense embedding
DENSE_SUMMARIZATION_EMBEDDING_CONFIG = EmbeddingModelConfig(
    model_name=os.getenv("DENSE_SUMMARIZATION_EMBEDDING_MODEL", "text-embedding-v4"),
    vector_dimension=int(os.getenv("DENSE_SUMMARIZATION_EMBEDDING_DIMENSION", 1024)),
    batch_size=min(max(int(os.getenv("DENSE_SUMMARIZATION_EMBEDDING_BATCH_SIZE", "10")), 1), 10),
)

