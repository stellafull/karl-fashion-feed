"""Embedding service backed by DashScope."""

from __future__ import annotations

import time
from typing import Any, List, Sequence

from dashscope import MultiModalEmbedding, TextEmbedding

from backend.app.config.embedding_config import (
    DENSE_EMBEDDING_CONFIG,
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
    SPARSE_EMBEDDING_CONFIG,
)

SparseEmbedding = dict[int, float]
EMBEDDING_MAX_RETRIES = 3
EMBEDDING_RETRY_DELAY_SECONDS = 1


def generate_article_summary_embedding(text: str) -> List[float]:
    """Generate summary embedding for story clustering."""
    request_kwargs: dict[str, Any] = {
        "model": DENSE_SUMMARIZATION_EMBEDDING_CONFIG.model_name,
        "input": text,
        "api_key": DENSE_SUMMARIZATION_EMBEDDING_CONFIG.api_key,
    }
    if DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension is not None:
        request_kwargs["dimension"] = DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension

    response = _call_with_retry(
        request_callable=TextEmbedding.call,
        request_kwargs=request_kwargs,
        operation_name="summary embedding",
    )
    [item] = _extract_embeddings(response=response, operation_name="summary embedding")
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

        response = _call_with_retry(
            request_callable=MultiModalEmbedding.call,
            request_kwargs=request_kwargs,
            operation_name="dense embedding",
        )
        [item] = _extract_embeddings(response=response, operation_name="dense embedding")
        embeddings.append([float(value) for value in item["embedding"]])

    return embeddings


def generate_sparse_embedding(texts: List[str]) -> List[SparseEmbedding]:
    """Generate sparse text embeddings."""
    embeddings: list[SparseEmbedding] = []
    batch_size = SPARSE_EMBEDDING_CONFIG.batch_size
    for index in range(0, len(texts), batch_size):
        batch = texts[index : index + batch_size]
        response = _call_with_retry(
            request_callable=TextEmbedding.call,
            request_kwargs={
                "model": SPARSE_EMBEDDING_CONFIG.model_name,
                "input": batch,
                "api_key": SPARSE_EMBEDDING_CONFIG.api_key,
                "output_type": "sparse",
            },
            operation_name="sparse embedding",
        )
        for item in _extract_embeddings(response=response, operation_name="sparse embedding"):
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


def _call_with_retry(
    *,
    request_callable,
    request_kwargs: dict[str, Any],
    operation_name: str,
):
    last_error: Exception | None = None
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            response = request_callable(**request_kwargs)
            _extract_embeddings(response=response, operation_name=operation_name)
            return response
        except Exception as exc:
            last_error = exc
            if attempt == EMBEDDING_MAX_RETRIES:
                break
            time.sleep(EMBEDDING_RETRY_DELAY_SECONDS)
    raise ValueError(f"{operation_name} failed after {EMBEDDING_MAX_RETRIES} attempts") from last_error


def _extract_embeddings(*, response: Any, operation_name: str) -> list[dict[str, Any]]:
    output = getattr(response, "output", None)
    if not isinstance(output, dict):
        raise ValueError(f"{operation_name} response missing output")
    embeddings = output.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        raise ValueError(f"{operation_name} response missing embeddings")
    if not all(isinstance(item, dict) for item in embeddings):
        raise ValueError(f"{operation_name} response embeddings must be dict items")
    return embeddings
