"""Story aggregation embedding service backed by DashScope."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from backend.app.config.embedding_config import (
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
    EmbeddingModelConfig,
)
from backend.app.service.story_pipeline_contracts import EmbeddedArticle, EnrichedArticleRecord

EmbedTexts = Callable[[list[str]], list[list[float]]]


class StoryEmbeddingService:
    def __init__(
        self,
        *,
        model_config: EmbeddingModelConfig = DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
        embed_texts: EmbedTexts | None = None,
    ) -> None:
        self._model_config = model_config
        self._embed_texts = embed_texts or self._embed_with_dashscope

    def embed_articles(
        self,
        articles: Iterable[EnrichedArticleRecord],
    ) -> list[EmbeddedArticle]:
        materialized = list(articles)
        if not materialized:
            return []

        batches = _chunked(
            [article.cluster_text for article in materialized],
            self._model_config.batch_size,
        )
        batch_vectors = [self._embed_texts(batch) for batch in batches]
        vectors = [vector for batch in batch_vectors for vector in batch]
        if len(vectors) != len(materialized):
            raise ValueError("embedding result size does not match article count")

        embedded: list[EmbeddedArticle] = []
        for article, vector in zip(materialized, vectors, strict=True):
            embedded.append(
                EmbeddedArticle(
                    article=article,
                    embedding=tuple(float(value) for value in vector),
                )
            )
        return embedded

    def _embed_with_dashscope(self, texts: list[str]) -> list[list[float]]:
        if not self._model_config.api_key:
            raise ValueError(
                f"missing embedding API key for {self._model_config.model_name} "
                f"(expected {self._model_config.api_key_env})"
            )

        from dashscope import TextEmbedding

        response = TextEmbedding.call(
            api_key=self._model_config.api_key,
            model=self._model_config.model_name,
            input=texts,
        )
        payload = _to_mapping(response)
        status_code = payload.get("status_code") or getattr(response, "status_code", 200)
        if isinstance(status_code, int) and status_code >= 400:
            message = payload.get("message") or getattr(response, "message", "embedding request failed")
            raise RuntimeError(str(message))

        output = payload.get("output")
        if not isinstance(output, dict):
            output = getattr(response, "output", None)
        if not isinstance(output, dict):
            raise ValueError("unexpected embedding response payload")

        embeddings = output.get("embeddings")
        if not isinstance(embeddings, list):
            raise ValueError("embedding response missing embeddings")

        vectors: list[list[float]] = []
        for item in embeddings:
            if not isinstance(item, dict):
                raise ValueError("invalid embedding entry")
            vector = item.get("embedding") or item.get("vector")
            if not isinstance(vector, list):
                raise ValueError("invalid embedding vector")
            vectors.append([float(value) for value in vector])
        return vectors


def _to_mapping(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "to_dict"):
        result = response.to_dict()
        if isinstance(result, dict):
            return result
    return {}


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


class EmbeddingService(StoryEmbeddingService):
    """Backward-compatible alias for story aggregation embedding."""
