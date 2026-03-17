"""Semantic clustering for publishable articles."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.prompts.story_cluster_review_prompt import STORY_CLUSTER_REVIEW_PROMPT
from backend.app.schemas.llm.story_cluster_review import StoryClusterReviewSchema
from backend.app.service.llm_client_service import (
    BatchChatRequest,
    OpenAICompatibleClient,
)
from backend.app.service.story_pipeline_contracts import EmbeddedArticle


@dataclass(frozen=True)
class ClusterReviewArticleInput:
    article_id: str
    title_zh: str
    summary_zh: str
    tags: tuple[str, ...]
    brands: tuple[str, ...]
    category_candidates: tuple[str, ...]
    source_name: str


class ArticleClusterService:
    def __init__(
        self,
        *,
        llm_client: OpenAICompatibleClient | Any | None = None,
        distance_threshold: float = 0.18,
    ) -> None:
        self._llm_client = llm_client or OpenAICompatibleClient()
        self._distance_threshold = distance_threshold

    def cluster_articles(self, articles: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
        semantic_clusters = self.build_semantic_clusters(articles)
        return self.review_clusters(semantic_clusters)

    def build_semantic_clusters(self, articles: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
        if not articles:
            return []
        if len(articles) == 1:
            return [[articles[0]]]

        model = AgglomerativeClustering(
            metric="cosine",
            linkage="average",
            distance_threshold=self._distance_threshold,
            n_clusters=None,
        )
        labels = model.fit_predict(_normalize_embeddings(articles))

        grouped: dict[int, list[EmbeddedArticle]] = defaultdict(list)
        for label, article in zip(labels, articles, strict=True):
            grouped[int(label)].append(article)

        semantic_clusters = [_sort_articles(cluster) for cluster in grouped.values()]
        semantic_clusters.sort(key=_cluster_sort_key, reverse=True)
        return semantic_clusters

    def review_clusters(
        self,
        clusters: list[list[EmbeddedArticle]],
    ) -> list[list[EmbeddedArticle]]:
        if not clusters:
            return []

        if not hasattr(self._llm_client, "complete_json_batch"):
            return self._review_clusters_sequentially(clusters)

        requests: list[BatchChatRequest] = []
        cluster_by_custom_id: dict[str, list[EmbeddedArticle]] = {}
        reviewed_clusters: list[list[EmbeddedArticle]] = []

        for index, cluster in enumerate(clusters):
            if len(cluster) <= 1:
                reviewed_clusters.append(cluster)
                continue
            custom_id = f"cluster:{index}"
            requests.append(
                BatchChatRequest(
                    custom_id=custom_id,
                    messages=self.build_review_messages(cluster),
                )
            )
            cluster_by_custom_id[custom_id] = cluster

        if not requests:
            return reviewed_clusters

        try:
            batch_results = self._review_clusters_with_batch(requests)
        except Exception:
            return self._review_clusters_sequentially(clusters)

        for index, cluster in enumerate(clusters):
            if len(cluster) <= 1:
                continue
            custom_id = f"cluster:{index}"
            outcome = batch_results.get(custom_id)
            if outcome is None or outcome.error or not isinstance(outcome.value, StoryClusterReviewSchema):
                reviewed_clusters.append(cluster)
                continue
            reviewed_clusters.extend(self.apply_review_result(cluster, outcome.value))

        reviewed_clusters.sort(key=_cluster_sort_key, reverse=True)
        return reviewed_clusters

    def build_review_messages(self, cluster: list[EmbeddedArticle]) -> list[dict[str, str]]:
        payload = [
            ClusterReviewArticleInput(
                article_id=item.article.article_id,
                title_zh=item.article.title_zh,
                summary_zh=item.article.summary_zh,
                tags=item.article.tags,
                brands=item.article.brands,
                category_candidates=item.article.category_candidates,
                source_name=item.article.source_name,
            )
            for item in cluster
        ]
        return [
            {"role": "system", "content": STORY_CLUSTER_REVIEW_PROMPT},
            {"role": "user", "content": _render_json_payload([asdict(item) for item in payload])},
        ]

    def apply_review_result(
        self,
        cluster: list[EmbeddedArticle],
        result: StoryClusterReviewSchema,
    ) -> list[list[EmbeddedArticle]]:
        cluster_by_id = {item.article.article_id: item for item in cluster}
        proposed_ids = [article_id for group in result.groups for article_id in group.article_ids]
        expected_ids = [item.article.article_id for item in cluster]
        if sorted(proposed_ids) != sorted(expected_ids):
            return [cluster]

        if len(set(proposed_ids)) != len(proposed_ids):
            return [cluster]

        reviewed: list[list[EmbeddedArticle]] = []
        for group in result.groups:
            reviewed.append(_sort_articles([cluster_by_id[article_id] for article_id in group.article_ids]))

        reviewed.sort(key=_cluster_sort_key, reverse=True)
        return reviewed

    def _review_cluster(self, cluster: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
        if len(cluster) <= 1:
            return [cluster]

        try:
            result = self._llm_client.complete_json(
                model_config=STORY_SUMMARIZATION_MODEL_CONFIG,
                messages=self.build_review_messages(cluster),
                schema=StoryClusterReviewSchema,
            )
        except Exception:
            return [cluster]
        return self.apply_review_result(cluster, result)

    def _review_clusters_with_batch(
        self,
        requests: list[BatchChatRequest],
    ) -> dict[str, Any]:
        return self._llm_client.complete_json_batch(
            model_config=STORY_SUMMARIZATION_MODEL_CONFIG,
            requests=requests,
            schema=StoryClusterReviewSchema,
            metadata={"stage": "cluster_review"},
        )

    def _review_clusters_sequentially(
        self,
        clusters: list[list[EmbeddedArticle]],
    ) -> list[list[EmbeddedArticle]]:
        reviewed_clusters: list[list[EmbeddedArticle]] = []
        for cluster in clusters:
            reviewed_clusters.extend(self._review_cluster(cluster))
        reviewed_clusters.sort(key=_cluster_sort_key, reverse=True)
        return reviewed_clusters


def _sort_articles(cluster: list[EmbeddedArticle]) -> list[EmbeddedArticle]:
    ordered = sorted(cluster, key=lambda item: item.article.article_id)
    ordered = sorted(ordered, key=lambda item: item.article.ingested_at, reverse=True)
    ordered = sorted(
        ordered,
        key=lambda item: item.article.published_at or datetime.min,
        reverse=True,
    )
    return ordered


def _cluster_sort_key(cluster: list[EmbeddedArticle]) -> tuple[datetime, datetime]:
    first = cluster[0].article
    return first.published_at or datetime.min, first.ingested_at


def _normalize_embeddings(articles: list[EmbeddedArticle]) -> np.ndarray:
    matrix = np.array([article.embedding for article in articles], dtype=float)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def _render_json_payload(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
