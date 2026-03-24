"""Embedding service backed by DashScope."""

from __future__ import annotations

import base64
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
MAX_MULTIMODAL_BATCH_SIZE = 20
MAX_IMAGE_ITEMS_PER_BATCH = 5
MAX_SUMMARY_EMBEDDING_BATCH_SIZE = 10


def generate_article_summary_embedding(text: str) -> List[float]:
    """Generate summary embedding for story clustering."""
    [embedding] = generate_article_summary_embeddings([text])
    return embedding


def generate_article_summary_embeddings(texts: List[str]) -> List[List[float]]:
    """Generate summary embeddings for story clustering in batches."""
    if not texts:
        return []

    batch_size = min(
        max(DENSE_SUMMARIZATION_EMBEDDING_CONFIG.batch_size, 1),
        MAX_SUMMARY_EMBEDDING_BATCH_SIZE,
    )
    embeddings: list[list[float]] = []
    for index in range(0, len(texts), batch_size):
        batch = texts[index : index + batch_size]
        request_kwargs: dict[str, Any] = {
            "model": DENSE_SUMMARIZATION_EMBEDDING_CONFIG.model_name,
            "input": batch,
            "api_key": DENSE_SUMMARIZATION_EMBEDDING_CONFIG.api_key,
        }
        if DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension is not None:
            request_kwargs["dimension"] = DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension

        try:
            response = _call_with_retry(
                request_callable=TextEmbedding.call,
                request_kwargs=request_kwargs,
                operation_name="summary embedding",
            )
        except Exception as exc:
            exc.add_note(
                f"summary embedding batch_start={index} batch_size={len(batch)}"
            )
            raise
        batch_embeddings = _extract_embeddings(
            response=response,
            operation_name="summary embedding",
        )
        if len(batch_embeddings) != len(batch):
            raise ValueError("summary embedding response size does not match request size")
        embeddings.extend(
            [float(value) for value in item["embedding"]]
            for item in batch_embeddings
        )
    return embeddings


def generate_dense_embedding(
    texts: List[str],
    image_inputs: Sequence[str | None] | None = None,
) -> List[List[float]]:
    """Generate one dense vector per retrieval unit."""
    if not texts:
        return []

    normalized_image_inputs = [None] * len(texts) if image_inputs is None else list(image_inputs)
    if len(normalized_image_inputs) != len(texts):
        raise ValueError("dense embedding texts and image_inputs must have the same length")

    embeddings: list[list[float]] = []
    batches = _build_dense_embedding_batches(texts, normalized_image_inputs)
    for batch_start, batch_texts, batch_image_inputs in batches:
        batch_inputs: list[dict[str, str]] = []
        for text, image_input in zip(batch_texts, batch_image_inputs, strict=True):
            input_item: dict[str, str] = {"text": text}
            if image_input is not None and image_input.strip():
                input_item["image"] = image_input.strip()
            batch_inputs.append(input_item)

        request_kwargs: dict[str, Any] = {
            "model": DENSE_EMBEDDING_CONFIG.model_name,
            "input": batch_inputs,
            "api_key": DENSE_EMBEDDING_CONFIG.api_key,
        }
        if DENSE_EMBEDDING_CONFIG.vector_dimension is not None:
            request_kwargs["parameters"] = {"dimension": DENSE_EMBEDDING_CONFIG.vector_dimension}
        try:
            response = _call_with_retry(
                request_callable=MultiModalEmbedding.call,
                request_kwargs=request_kwargs,
                operation_name="dense embedding",
            )
        except Exception as exc:
            exc.add_note(
                f"dense embedding batch_start={batch_start} batch_size={len(batch_inputs)}"
            )
            raise
        batch_embeddings = _extract_embeddings(response=response, operation_name="dense embedding")
        if len(batch_embeddings) != len(batch_inputs):
            raise ValueError("dense embedding response size does not match request size")
        for item in batch_embeddings:
            embeddings.append([float(value) for value in item["embedding"]])

    return embeddings


def _build_dense_embedding_batches(
    texts: List[str],
    image_inputs: Sequence[str | None],
) -> list[tuple[int, list[str], list[str | None]]]:
    configured_batch_size = max(DENSE_EMBEDDING_CONFIG.batch_size, 1)
    total_limit = min(configured_batch_size, MAX_MULTIMODAL_BATCH_SIZE)

    batches: list[tuple[int, list[str], list[str | None]]] = []
    batch_start = 0
    current_texts: list[str] = []
    current_image_inputs: list[str | None] = []
    current_image_count = 0

    for index, (text, image_input) in enumerate(zip(texts, image_inputs, strict=True)):
        has_image = bool(image_input is not None and image_input.strip())
        next_total = len(current_texts) + 1
        next_image_count = current_image_count + (1 if has_image else 0)
        exceeds_total_limit = next_total > total_limit
        exceeds_image_limit = next_image_count > MAX_IMAGE_ITEMS_PER_BATCH

        if current_texts and (exceeds_total_limit or exceeds_image_limit):
            batches.append((batch_start, current_texts, current_image_inputs))
            batch_start = index
            current_texts = []
            current_image_inputs = []
            current_image_count = 0

        current_texts.append(text)
        current_image_inputs.append(image_input)
        if has_image:
            current_image_count += 1

    if current_texts:
        batches.append((batch_start, current_texts, current_image_inputs))

    return batches


def build_data_url(*, mime_type: str, base64_data: str) -> str:
    """Build a data URL for multimodal chat or embedding input."""
    normalized_mime_type = mime_type.strip()
    normalized_base64_data = base64_data.strip()
    if not normalized_mime_type:
        raise ValueError("data URL mime_type must not be empty")
    if not normalized_base64_data:
        raise ValueError("data URL base64_data must not be empty")
    return f"data:{normalized_mime_type};base64,{normalized_base64_data}"


def encode_bytes_as_base64(content: bytes) -> str:
    """Encode uploaded bytes into an ASCII base64 string."""
    if not content:
        raise ValueError("image content must not be empty")
    return base64.b64encode(content).decode("ascii")


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
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            response = request_callable(**request_kwargs)
            _extract_embeddings(response=response, operation_name=operation_name)
            return response
        except Exception as exc:
            if attempt == EMBEDDING_MAX_RETRIES:
                exc.add_note(
                    f"{operation_name} failed after {EMBEDDING_MAX_RETRIES} attempts"
                )
                raise
            time.sleep(EMBEDDING_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"{operation_name} retry loop exited unexpectedly")


def _extract_embeddings(*, response: Any, operation_name: str) -> list[dict[str, Any]]:
    output = getattr(response, "output", None)
    if not isinstance(output, dict):
        error = ValueError(f"{operation_name} response missing output")
        error.add_note(_describe_response(response))
        raise error
    embeddings = output.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        error = ValueError(f"{operation_name} response missing embeddings")
        error.add_note(_describe_response(response))
        raise error
    if not all(isinstance(item, dict) for item in embeddings):
        error = ValueError(f"{operation_name} response embeddings must be dict items")
        error.add_note(_describe_response(response))
        raise error
    return embeddings


def _describe_response(response: Any) -> str:
    details = {
        "response_type": type(response).__name__,
        "status_code": getattr(response, "status_code", None),
        "code": getattr(response, "code", None),
        "message": getattr(response, "message", None),
        "request_id": getattr(response, "request_id", None),
        "output": getattr(response, "output", None),
        "usage": getattr(response, "usage", None),
    }
    return f"response_debug={details!r}"
