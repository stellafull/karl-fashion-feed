"""Embedding service backed by DashScope."""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence

from dashscope import MultiModalEmbedding, TextEmbedding
from dashscope.embeddings.multimodal_embedding import (
    MultiModalEmbeddingItemImage,
    MultiModalEmbeddingItemText,
)

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
    embeddings = _extract_embeddings(response)
    if len(embeddings) != 1:
        raise ValueError("summary embedding result size does not match request size")
    return embeddings[0]


def generate_dense_embedding(
    texts: List[str],
    image_urls: Sequence[str | None] | None = None,
) -> List[List[float]]:
    """Generate dense embeddings with a single multimodal path."""
    if not texts:
        return []

    if image_urls is None:
        normalized_image_urls = [None] * len(texts)
    else:
        normalized_image_urls = list(image_urls)
        if len(normalized_image_urls) != len(texts):
            raise ValueError("texts and image_urls must have the same length")

    embeddings: list[list[float]] = []
    for text, image_url in zip(texts, normalized_image_urls, strict=True):
        items = [MultiModalEmbeddingItemText(text=text, factor=1.0)]
        if image_url is not None and image_url.strip():
            items.append(MultiModalEmbeddingItemImage(image=image_url.strip(), factor=1.0))
        request_kwargs: dict[str, Any] = {
            "model": DENSE_EMBEDDING_CONFIG.model_name,
            "input": items,
            "api_key": DENSE_EMBEDDING_CONFIG.api_key,
        }
        if DENSE_EMBEDDING_CONFIG.vector_dimension is not None:
            request_kwargs["dimension"] = DENSE_EMBEDDING_CONFIG.vector_dimension

        response = MultiModalEmbedding.call(**request_kwargs)
        embeddings.extend(_extract_embeddings(response))

    if len(embeddings) != len(texts):
        raise ValueError("dense embedding result size does not match request size")
    return embeddings


def generate_sparse_embedding(texts: List[str]) -> List[SparseEmbedding]:
    """Generate sparse text embeddings."""
    embeddings: list[SparseEmbedding] = []
    for batch in _chunked(texts, SPARSE_EMBEDDING_CONFIG.batch_size):
        response = TextEmbedding.call(
            model=SPARSE_EMBEDDING_CONFIG.model_name,
            input=list(batch),
            api_key=SPARSE_EMBEDDING_CONFIG.api_key,
            output_type="sparse",
        )
        embeddings.extend(_extract_sparse_embeddings(response))
    return embeddings


def _extract_embeddings(response: Any) -> List[List[float]]:
    output = getattr(response, "output", None)
    if not isinstance(output, dict):
        raise ValueError(
            "embedding request failed: "
            f"status_code={getattr(response, 'status_code', None)} "
            f"code={getattr(response, 'code', None)} "
            f"message={getattr(response, 'message', None)}"
        )

    items = output.get("embeddings")
    if not isinstance(items, list):
        raise ValueError("embedding response missing embeddings")

    vectors: list[list[float]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid embedding item")
        vector = item.get("embedding")
        if not isinstance(vector, list):
            raise ValueError("embedding item missing vector")
        vectors.append([float(value) for value in vector])
    return vectors


def _extract_sparse_embeddings(response: Any) -> List[SparseEmbedding]:
    output = getattr(response, "output", None)
    if not isinstance(output, dict):
        raise ValueError(
            "sparse embedding request failed: "
            f"status_code={getattr(response, 'status_code', None)} "
            f"code={getattr(response, 'code', None)} "
            f"message={getattr(response, 'message', None)}"
        )

    items = output.get("embeddings")
    if not isinstance(items, list):
        raise ValueError("embedding response missing embeddings")

    vectors: list[SparseEmbedding] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid embedding item")

        raw_vector = item.get("sparse_embedding")
        if raw_vector is None:
            raw_vector = item.get("embedding")

        vectors.append(_normalize_sparse_embedding(raw_vector))
    return vectors


def _chunked(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    if size <= 0:
        raise ValueError("batch size must be positive")
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _normalize_sparse_embedding(raw_vector: Any) -> SparseEmbedding:
    if isinstance(raw_vector, dict):
        if "indices" in raw_vector and "values" in raw_vector:
            indices = raw_vector["indices"]
            values = raw_vector["values"]
            if not isinstance(indices, list) or not isinstance(values, list):
                raise ValueError("sparse embedding indices/values must be lists")
            if len(indices) != len(values):
                raise ValueError("sparse embedding indices/values length mismatch")
            return {
                int(index): float(value)
                for index, value in zip(indices, values, strict=True)
            }

        return {int(index): float(value) for index, value in raw_vector.items()}

    if isinstance(raw_vector, list):
        return {index: float(value) for index, value in enumerate(raw_vector)}

    raise ValueError("embedding item missing sparse vector")
