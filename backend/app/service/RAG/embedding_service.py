"""Embedding service backed by DashScope.

- generate_article_summary_embedding: 生成文章摘要的向量表示
- generate_dense_embedding: 生成多模态向量
- generate_sparse_embedding: 生成文本稀疏向量
"""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence

SparseEmbedding = dict[int, float]
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



def generate_article_summary_embedding(text: str) -> List[float]:
    """生成文章摘要的向量表示."""
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
    """生成多模态向量.

    百炼多模态仅支持公网图片 URL。
    对纯文本条目仅发送 `texts`，对图文条目发送 `texts + image_url`，
    避免 image_url 为空时整批请求无法返回 embedding。
    """
    if not texts:
        return []

    if image_urls is None:
        normalized_image_urls: list[str | None] = [None] * len(texts)
    else:
        normalized_image_urls = list(image_urls)
        if normalized_image_urls and len(normalized_image_urls) != len(texts):
            raise ValueError("texts and image_urls must have the same length")
        if not normalized_image_urls:
            normalized_image_urls = [None] * len(texts)

    text_only_indexes = [
        index
        for index, image_url in enumerate(normalized_image_urls)
        if not _has_image_url(image_url)
    ]
    multimodal_indexes = [
        index
        for index, image_url in enumerate(normalized_image_urls)
        if _has_image_url(image_url)
    ]
    embeddings: list[list[float] | None] = [None] * len(texts)

    if text_only_indexes:
        text_only_texts = [texts[index] for index in text_only_indexes]
        text_only_embeddings = _embed_text_batches(
            texts=text_only_texts,
            model_name=DENSE_EMBEDDING_CONFIG.model_name,
            api_key=DENSE_EMBEDDING_CONFIG.api_key,
            batch_size=DENSE_EMBEDDING_CONFIG.batch_size,
            vector_dimension=DENSE_EMBEDDING_CONFIG.vector_dimension,
        )
        for index, embedding in zip(text_only_indexes, text_only_embeddings, strict=True):
            embeddings[index] = embedding

    if multimodal_indexes:
        multimodal_texts = [texts[index] for index in multimodal_indexes]
        multimodal_image_urls = [normalized_image_urls[index] for index in multimodal_indexes]
        multimodal_embeddings = _embed_multimodal_batches(
            texts=multimodal_texts,
            image_urls=[image_url for image_url in multimodal_image_urls if image_url is not None],
            model_name=DENSE_EMBEDDING_CONFIG.model_name,
            api_key=DENSE_EMBEDDING_CONFIG.api_key,
            batch_size=DENSE_EMBEDDING_CONFIG.batch_size,
            vector_dimension=DENSE_EMBEDDING_CONFIG.vector_dimension,
        )
        for index, embedding in zip(multimodal_indexes, multimodal_embeddings, strict=True):
            embeddings[index] = embedding

    if any(embedding is None for embedding in embeddings):
        raise ValueError("dense embedding result size does not match request size")

    return [embedding for embedding in embeddings if embedding is not None]

def generate_sparse_embedding(texts: List[str]) -> List[SparseEmbedding]:
    """生成文本稀疏向量."""
    return _embed_sparse_text_batches(
        texts=texts,
        model_name=SPARSE_EMBEDDING_CONFIG.model_name,
        api_key=SPARSE_EMBEDDING_CONFIG.api_key,
        batch_size=SPARSE_EMBEDDING_CONFIG.batch_size,
    )

def _has_image_url(image_url: str | None) -> bool:
    return image_url is not None and bool(image_url.strip())


def _extract_embeddings(response: Any) -> List[List[float]]:
    output = getattr(response, "output", None)
    if not isinstance(output, dict):
        raise ValueError("embedding response missing output")

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
        raise ValueError("embedding response missing output")

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


def _embed_text_batches(
    *,
    texts: Sequence[str],
    model_name: str,
    api_key: str | None,
    batch_size: int,
    vector_dimension: int | None = None,
) -> List[List[float]]:
    embeddings: list[list[float]] = []
    for batch in _chunked(texts, batch_size):
        request_kwargs: dict[str, Any] = {
            "model": model_name,
            "input": list(batch),
            "api_key": api_key,
        }
        if vector_dimension is not None:
            request_kwargs["dimension"] = vector_dimension

        response = TextEmbedding.call(**request_kwargs)
        embeddings.extend(_extract_embeddings(response))
    return embeddings


def _embed_sparse_text_batches(
    *,
    texts: Sequence[str],
    model_name: str,
    api_key: str | None,
    batch_size: int,
) -> List[SparseEmbedding]:
    embeddings: list[SparseEmbedding] = []
    for batch in _chunked(texts, batch_size):
        response = TextEmbedding.call(
            model=model_name,
            input=list(batch),
            api_key=api_key,
            output_type="sparse",
        )
        embeddings.extend(_extract_sparse_embeddings(response))
    return embeddings


def _embed_multimodal_batches(
    *,
    texts: Sequence[str],
    image_urls: Sequence[str],
    model_name: str,
    api_key: str | None,
    batch_size: int,
    vector_dimension: int | None = None,
) -> List[List[float]]:
    if len(texts) != len(image_urls):
        raise ValueError("multimodal texts and image_urls must have the same length")

    embeddings: list[list[float]] = []
    for text_batch, image_url_batch in zip(
        _chunked(texts, batch_size),
        _chunked(image_urls, batch_size),
        strict=True,
    ):
        response = MultiModalEmbedding.call(
            **{
                "model": model_name,
                "input": [
                    [
                        MultiModalEmbeddingItemText(text=text, factor=1.0),
                        MultiModalEmbeddingItemImage(image=image_url, factor=1.0),
                    ]
                    for text, image_url in zip(text_batch, image_url_batch, strict=True)
                ],
                "api_key": api_key,
                **(
                    {"dimension": vector_dimension}
                    if vector_dimension is not None
                    else {}
                ),
            }
        )
        embeddings.extend(_extract_embeddings(response))
    return embeddings


def _chunked(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
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
