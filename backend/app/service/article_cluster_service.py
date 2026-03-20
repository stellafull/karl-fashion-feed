"""Semantic clustering for publishable articles."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json

import numpy as np
from openai import AsyncOpenAI
from sklearn.cluster import AgglomerativeClustering

from backend.app.config.llm_config import STORY_SUMMARIZATION_MODEL_CONFIG
from backend.app.prompts.story_cluster_review_prompt import STORY_CLUSTER_REVIEW_PROMPT
from backend.app.schemas.llm.story_cluster_review import StoryClusterReviewSchema
from backend.app.service.article_enrichment_service import EnrichedArticle


@dataclass(frozen=True)
class EmbeddedArticle:
    article: EnrichedArticle
    embedding: tuple[float, ...]


class ArticleClusterService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=STORY_SUMMARIZATION_MODEL_CONFIG.api_key,
            base_url=STORY_SUMMARIZATION_MODEL_CONFIG.base_url,
            timeout=STORY_SUMMARIZATION_MODEL_CONFIG.timeout_seconds,
        )
        self._distance_threshold = 0.18

    async def cluster_articles(self, articles: list[EmbeddedArticle]) -> list[list[EmbeddedArticle]]:
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
        reviewed_clusters: list[list[EmbeddedArticle]] = []
        review_tasks = []
        review_targets: list[list[EmbeddedArticle]] = []
        for cluster in semantic_clusters:
            if len(cluster) <= 1:
                reviewed_clusters.append(cluster)
                continue
            review_targets.append(cluster)
            review_tasks.append(
                self._client.beta.chat.completions.parse(
                    model=STORY_SUMMARIZATION_MODEL_CONFIG.model_name,
                    temperature=STORY_SUMMARIZATION_MODEL_CONFIG.temperature,
                    messages=[
                        {"role": "system", "content": STORY_CLUSTER_REVIEW_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                [
                                    {
                                        "article_id": item.article.article_id,
                                        "title_zh": item.article.title_zh,
                                        "summary_zh": item.article.summary_zh,
                                        "tags": item.article.tags,
                                        "brands": item.article.brands,
                                        "category_candidates": item.article.category_candidates,
                                        "source_name": item.article.source_name,
                                    }
                                    for item in cluster
                                ],
                                ensure_ascii=False,
                                indent=2,
                                sort_keys=True,
                            ),
                        },
                    ],
                    response_format=StoryClusterReviewSchema,
                )
            )

        responses = await asyncio.gather(*review_tasks)
        for cluster, response in zip(review_targets, responses, strict=True):
            result = response.choices[0].message.parsed
            if result is None:
                raise ValueError("story cluster review response missing parsed payload")

            cluster_by_id = {item.article.article_id: item for item in cluster}
            reviewed_article_ids = [article_id for group in result.groups for article_id in group.article_ids]
            expected_article_ids = [item.article.article_id for item in cluster]
            if sorted(reviewed_article_ids) != sorted(expected_article_ids):
                raise ValueError("story cluster review returned mismatched article ids")
            if len(set(reviewed_article_ids)) != len(reviewed_article_ids):
                raise ValueError("story cluster review returned duplicate article ids")

            for group in result.groups:
                reviewed_clusters.append(_sort_articles([cluster_by_id[article_id] for article_id in group.article_ids]))

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
