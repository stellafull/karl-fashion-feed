"""Embedding service backed by DashScope."""

from __future__ import annotations

from typing import Any, List, Sequence

from dashscope import MultiModalEmbedding, TextEmbedding

from backend.app.config.embedding_config import (
    DENSE_EMBEDDING_CONFIG,
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
    SPARSE_EMBEDDING_CONFIG,
)

SparseEmbedding = dict[int, float]


def generate_article_summary_embedding(text: str) -> List[float]:
    """Generate summary embedding for story clustering."""
    request_kwargs: dict[str, Any] = {
        "model": DENSE_SUMMARIZATION_EMBEDDING_CONFIG.model_name,
        "input": text,
        "api_key": DENSE_SUMMARIZATION_EMBEDDING_CONFIG.api_key,
    }
    if DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension is not None:
        request_kwargs["dimension"] = DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension

    response = TextEmbedding.call(**request_kwargs)
    [item] = response.output["embeddings"]
    return [float(value) for value in item["embedding"]]


def generate_dense_embedding(
    texts: List[str],
    image_urls: Sequence[str | None] | None = None,
) -> List[List[float]]:
    """Generate one dense vector per retrieval unit."""
    if not texts:
        return []

    normalized_image_urls = [None] * len(texts) if image_urls is None else list(image_urls)

    embeddings: list[list[float]] = []
    for text, image_url in zip(texts, normalized_image_urls, strict=True):
        input_item: dict[str, str] = {"text": text}
        if image_url is not None and image_url.strip():
            input_item["image"] = image_url.strip()

        request_kwargs: dict[str, Any] = {
            "model": DENSE_EMBEDDING_CONFIG.model_name,
            "input": [input_item],
            "api_key": DENSE_EMBEDDING_CONFIG.api_key,
        }
        if DENSE_EMBEDDING_CONFIG.vector_dimension is not None:
            request_kwargs["parameters"] = {"dimension": DENSE_EMBEDDING_CONFIG.vector_dimension}

        response = MultiModalEmbedding.call(**request_kwargs)
        [item] = response.output["embeddings"]
        embeddings.append([float(value) for value in item["embedding"]])

    return embeddings


def generate_sparse_embedding(texts: List[str]) -> List[SparseEmbedding]:
    """Generate sparse text embeddings."""
    embeddings: list[SparseEmbedding] = []
    batch_size = SPARSE_EMBEDDING_CONFIG.batch_size
    for index in range(0, len(texts), batch_size):
        batch = texts[index : index + batch_size]
        response = TextEmbedding.call(
            model=SPARSE_EMBEDDING_CONFIG.model_name,
            input=batch,
            api_key=SPARSE_EMBEDDING_CONFIG.api_key,
            output_type="sparse",
        )
        for item in response.output["embeddings"]:
            raw_vector = item["sparse_embedding"] if "sparse_embedding" in item else item["embedding"]
            if isinstance(raw_vector, list) and raw_vector and isinstance(raw_vector[0], dict):
                embeddings.append(
                    {
                        int(vector_item["index"]): float(vector_item["value"])
                        for vector_item in raw_vector
                    }
                )
                continue
            if isinstance(raw_vector, dict):
                embeddings.append(
                    {int(vector_index): float(vector_value) for vector_index, vector_value in raw_vector.items()}
                )
                continue
            embeddings.append(
                {
                    vector_index: float(vector_value)
                    for vector_index, vector_value in enumerate(raw_vector)
                }
            )
    return embeddings
