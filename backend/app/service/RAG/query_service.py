"""Retrieval-core query execution across text, image, and fusion lanes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor

from qdrant_client.http import models
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Article, ArticleImage
from backend.app.schemas.rag_api import RequestImageInput
from backend.app.schemas.rag_query import (
    ArticlePackage,
    CitationLocator,
    GroundingText,
    QueryPlan,
    QueryResult,
    REQUEST_IMAGE_REF,
    RetrievalHit,
)
from backend.app.service.RAG.article_rag_service import (
    RAG_COLLECTION_NAME,
    build_image_retrieval_content,
)
from backend.app.service.RAG.embedding_service import (
    build_data_url,
    generate_dense_embedding,
    generate_sparse_embedding,
)
from backend.app.service.RAG.qdrant_service import QdrantService
from backend.app.service.RAG.reranker_service import RerankerService
from backend.app.service.article_chunk_service import split_markdown_into_text_chunks
from backend.app.service.article_parse_service import ArticleMarkdownService


TEXT_RECALL_LIMIT = 40
IMAGE_RECALL_LIMIT = 30
TEXT_RERANK_TOP_N = 10
IMAGE_RERANK_TOP_N = 10
IMAGE_GROUNDING_LIMIT = 2
IMAGE_QUERY_EMBEDDING_TEXT = "image query"


class QueryService:
    """Execute one deterministic retrieval plan over the shared collection."""

    def __init__(self) -> None:
        self._markdown_service = ArticleMarkdownService()
        self._qdrant_service = QdrantService()
        self._reranker_service = RerankerService()
        self._collection_name = RAG_COLLECTION_NAME

    def execute(
        self,
        query_plan: QueryPlan,
        *,
        request_images: Sequence[RequestImageInput] | None = None,
    ) -> QueryResult:
        """Execute one retrieval plan and return structured evidence."""
        if query_plan.plan_type == "text_only":
            text_results = self._execute_text_lane(query_plan)
            packages = self._build_packages(text_results=text_results, image_results=[])
            return QueryResult(
                query_plan=query_plan,
                text_results=text_results,
                image_results=[],
                packages=packages,
                citation_locators=self._collect_citation_locators(text_results, []),
            )

        if query_plan.plan_type == "image_only":
            image_results = self._execute_image_lane(
                query_plan,
                request_images=request_images,
            )
            packages = self._build_packages(text_results=[], image_results=image_results)
            return QueryResult(
                query_plan=query_plan,
                text_results=[],
                image_results=image_results,
                packages=packages,
                citation_locators=self._collect_citation_locators([], image_results),
            )

        if query_plan.plan_type == "fusion":
            with ThreadPoolExecutor(max_workers=2) as executor:
                text_future = executor.submit(self._execute_text_lane, query_plan)
                image_future = executor.submit(
                    self._execute_image_lane,
                    query_plan,
                    request_images=request_images,
                )
                text_results = text_future.result()
                image_results = image_future.result()
            packages = self._build_packages(text_results=text_results, image_results=image_results)
            return QueryResult(
                query_plan=query_plan,
                text_results=text_results,
                image_results=image_results,
                packages=packages,
                citation_locators=self._collect_citation_locators(text_results, image_results),
            )

        raise ValueError(f"unsupported query plan type: {query_plan.plan_type}")

    def _execute_text_lane(self, query_plan: QueryPlan) -> list[RetrievalHit]:
        text_query = query_plan.text_query
        if text_query is None:
            raise ValueError("text lane requires text_query")

        dense_vector = generate_dense_embedding([text_query])[0]
        sparse_vector = generate_sparse_embedding([text_query])[0]
        metadata_filter = self._build_lane_filter(query_plan=query_plan, modality="text")
        scored_points = self._qdrant_service.search_hybrid(
            self._collection_name,
            dense_vector,
            sparse_vector,
            limit=TEXT_RECALL_LIMIT,
            filters=metadata_filter,
        )
        documents = self._build_text_candidate_documents(scored_points)
        reranked_candidates = self._rerank_candidates(
            scored_points,
            query=text_query,
            top_n=min(TEXT_RERANK_TOP_N, query_plan.limit),
            documents=documents,
        )
        return self._build_text_hits(reranked_candidates[: query_plan.limit])

    def _execute_image_lane(
        self,
        query_plan: QueryPlan,
        *,
        request_images: Sequence[RequestImageInput] | None = None,
    ) -> list[RetrievalHit]:
        image_query = query_plan.image_query
        text_query = query_plan.text_query
        metadata_filter = self._build_lane_filter(query_plan=query_plan, modality="image")

        if image_query is not None:
            image_inputs = self._resolve_image_query_inputs(
                image_query=image_query,
                request_images=request_images,
            )
            dense_vectors = generate_dense_embedding(
                [IMAGE_QUERY_EMBEDDING_TEXT] * len(image_inputs),
                image_inputs=image_inputs,
            )
            candidates = self._search_request_image_candidates(
                dense_vectors=dense_vectors,
                metadata_filter=metadata_filter,
                limit=query_plan.limit,
            )
            return self._build_image_hits(candidates)

        if text_query is None:
            raise ValueError("image lane requires text_query or image_query")

        dense_vector = generate_dense_embedding([text_query])[0]
        sparse_vector = generate_sparse_embedding([text_query])[0]
        scored_points = self._qdrant_service.search_hybrid(
            self._collection_name,
            dense_vector,
            sparse_vector,
            limit=IMAGE_RECALL_LIMIT,
            filters=metadata_filter,
        )
        documents = self._build_image_candidate_documents(scored_points)
        reranked_candidates = self._rerank_candidates(
            scored_points,
            query=text_query,
            top_n=min(IMAGE_RERANK_TOP_N, query_plan.limit),
            documents=documents,
        )
        return self._build_image_hits(reranked_candidates[: query_plan.limit])

    def _resolve_image_query_inputs(
        self,
        *,
        image_query: str,
        request_images: Sequence[RequestImageInput] | None,
    ) -> list[str]:
        normalized_image_query = image_query.strip()
        if normalized_image_query == REQUEST_IMAGE_REF:
            if not request_images:
                raise ValueError("request_image query requires uploaded request image context")
            return [
                build_data_url(
                    mime_type=request_image.mime_type,
                    base64_data=request_image.base64_data,
                )
                for request_image in request_images
            ]
        return [normalized_image_query]

    def _search_request_image_candidates(
        self,
        *,
        dense_vectors: list[list[float]],
        metadata_filter: models.Filter,
        limit: int,
    ) -> list[tuple[models.ScoredPoint, float]]:
        merged_candidates: dict[str, tuple[models.ScoredPoint, float]] = {}

        for dense_vector in dense_vectors:
            scored_points = self._qdrant_service.search_dense(
                self._collection_name,
                dense_vector,
                limit=IMAGE_RECALL_LIMIT,
                filters=metadata_filter,
            )
            for point in scored_points:
                payload = self._require_payload(point)
                retrieval_unit_id = self._require_str(payload, "retrieval_unit_id")
                score = float(point.score)
                existing_candidate = merged_candidates.get(retrieval_unit_id)
                if existing_candidate is None or score > existing_candidate[1]:
                    merged_candidates[retrieval_unit_id] = (point, score)

        return sorted(
            merged_candidates.values(),
            key=lambda candidate: candidate[1],
            reverse=True,
        )[:limit]

    def _build_lane_filter(self, *, query_plan: QueryPlan, modality: str) -> models.Filter:
        time_range = query_plan.filters.time_range
        return self._qdrant_service.build_metadata_filter(
            modality=modality,
            source_names=query_plan.filters.source_names,
            categories=query_plan.filters.categories,
            tags=query_plan.filters.tags,
            brands=query_plan.filters.brands,
            start_at=time_range.start_at if time_range is not None else None,
            end_at=time_range.end_at if time_range is not None else None,
        )

    def _rerank_candidates(
        self,
        scored_points: list[models.ScoredPoint],
        *,
        query: str,
        top_n: int,
        documents: list[str],
    ) -> list[tuple[models.ScoredPoint, float]]:
        if not scored_points:
            return []
        rerank_results = self._reranker_service.rerank(query, documents, top_n)
        return [
            (scored_points[index], score)
            for index, score in rerank_results
        ]

    def _build_text_hits(self, candidates: list[tuple[models.ScoredPoint, float]]) -> list[RetrievalHit]:
        article_map = self._load_articles(
            self._require_str(self._require_payload(point), "article_id")
            for point, _score in candidates
        )
        chunk_content_map_by_article_id: dict[str, dict[int, str]] = {}
        hits: list[RetrievalHit] = []
        for point, score in candidates:
            payload = self._require_payload(point)
            article_id = self._require_str(payload, "article_id")
            chunk_index = self._require_int(payload, "chunk_index")
            article = article_map[article_id]
            content = self._load_text_hit_content(
                article,
                chunk_index=chunk_index,
                cache_by_article_id=chunk_content_map_by_article_id,
            )
            locator = CitationLocator(
                article_id=article.article_id,
                chunk_index=chunk_index,
                source_name=article.source_name,
                canonical_url=article.canonical_url,
            )
            hits.append(
                RetrievalHit(
                    retrieval_unit_id=self._require_str(payload, "retrieval_unit_id"),
                    modality="text",
                    article_id=article.article_id,
                    article_image_id=None,
                    content=content,
                    score=score,
                    citation_locator=locator,
                    title_zh=None,
                    summary_zh=None,
                )
            )
        return hits

    def _build_image_hits(self, candidates: list[tuple[models.ScoredPoint, float]]) -> list[RetrievalHit]:
        article_map = self._load_articles(
            self._require_str(self._require_payload(point), "article_id")
            for point, _score in candidates
        )
        image_map = self._load_images(
            self._require_image_id(self._require_payload(point)) for point, _score in candidates
        )
        hits: list[RetrievalHit] = []
        for point, score in candidates:
            payload = self._require_payload(point)
            article_id = self._require_str(payload, "article_id")
            image_id = self._require_image_id(payload)
            article = article_map[article_id]
            image = image_map[image_id]
            content = build_image_retrieval_content(article, image)
            if not content:
                raise ValueError(f"image grounding content must not be empty: {image.image_id}")
            locator = CitationLocator(
                article_id=article.article_id,
                article_image_id=image.image_id,
                source_name=article.source_name,
                canonical_url=article.canonical_url,
            )
            hits.append(
                RetrievalHit(
                    retrieval_unit_id=self._require_str(payload, "retrieval_unit_id"),
                    modality="image",
                    article_id=article.article_id,
                    article_image_id=image.image_id,
                    content=content,
                    score=score,
                    citation_locator=locator,
                    source_url=image.source_url,
                    caption_raw=image.caption_raw or None,
                    alt_text=image.alt_text or None,
                    credit_raw=image.credit_raw or None,
                    context_snippet=image.context_snippet or None,
                    ocr_text=None,
                    observed_description=None,
                    contextual_interpretation=None,
                    title_zh=None,
                    summary_zh=None,
                    grounding_texts=self._load_grounding_texts(article),
                )
            )
        return hits

    def _load_articles(self, article_ids: Iterable[str]) -> dict[str, Article]:
        normalized_article_ids = sorted(set(article_ids))
        if not normalized_article_ids:
            return {}
        with SessionLocal() as session:
            articles = session.scalars(
                select(Article).where(Article.article_id.in_(normalized_article_ids))
            ).all()
        article_map = {article.article_id: article for article in articles}
        missing_ids = sorted(set(normalized_article_ids) - set(article_map))
        if missing_ids:
            raise ValueError(f"missing articles for grounding: {missing_ids}")
        return article_map

    def _load_images(self, image_ids: Iterable[str | None]) -> dict[str, ArticleImage]:
        normalized_image_ids = sorted({image_id for image_id in image_ids if image_id})
        if not normalized_image_ids:
            return {}
        with SessionLocal() as session:
            images = session.scalars(
                select(ArticleImage).where(ArticleImage.image_id.in_(normalized_image_ids))
            ).all()
        image_map = {image.image_id: image for image in images}
        missing_ids = sorted(set(normalized_image_ids) - set(image_map))
        if missing_ids:
            raise ValueError(f"missing article images for grounding: {missing_ids}")
        return image_map

    def _load_grounding_texts(self, article: Article) -> list[GroundingText]:
        if not article.markdown_rel_path:
            raise ValueError(f"markdown_rel_path is required for image grounding: {article.article_id}")

        markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
        chunks = split_markdown_into_text_chunks(markdown, source_id=article.article_id)
        if not chunks:
            raise ValueError(f"no grounding text chunks found for article: {article.article_id}")
        grounding_texts: list[GroundingText] = []
        for chunk in chunks[:IMAGE_GROUNDING_LIMIT]:
            chunk_index = int(chunk["metadata"]["chunk_index"])
            grounding_texts.append(
                GroundingText(
                    chunk_index=chunk_index,
                    content=str(chunk["page_content"]),
                    citation_locator=CitationLocator(
                        article_id=article.article_id,
                        chunk_index=chunk_index,
                        source_name=article.source_name,
                        canonical_url=article.canonical_url,
                    ),
                )
            )
        if not grounding_texts:
            raise ValueError(f"grounding text generation failed for article: {article.article_id}")
        return grounding_texts

    def _build_text_candidate_documents(self, scored_points: list[models.ScoredPoint]) -> list[str]:
        article_ids = [
            self._require_str(self._require_payload(point), "article_id") for point in scored_points
        ]
        article_map = self._load_articles(article_ids)
        cache_by_article_id: dict[str, dict[int, str]] = {}
        documents: list[str] = []
        for point in scored_points:
            payload = self._require_payload(point)
            article_id = self._require_str(payload, "article_id")
            chunk_index = self._require_int(payload, "chunk_index")
            documents.append(
                self._load_text_hit_content(
                    article_map[article_id],
                    chunk_index=chunk_index,
                    cache_by_article_id=cache_by_article_id,
                )
            )
        return documents

    def _build_image_candidate_documents(self, scored_points: list[models.ScoredPoint]) -> list[str]:
        article_ids = [
            self._require_str(self._require_payload(point), "article_id") for point in scored_points
        ]
        image_ids = [self._require_image_id(self._require_payload(point)) for point in scored_points]
        article_map = self._load_articles(article_ids)
        image_map = self._load_images(image_ids)
        documents: list[str] = []
        for point in scored_points:
            payload = self._require_payload(point)
            article_id = self._require_str(payload, "article_id")
            image_id = self._require_image_id(payload)
            content = build_image_retrieval_content(article_map[article_id], image_map[image_id])
            if not content:
                raise ValueError(f"image grounding content must not be empty: {image_id}")
            documents.append(content)
        return documents

    def _build_packages(
        self,
        *,
        text_results: list[RetrievalHit],
        image_results: list[RetrievalHit],
    ) -> list[ArticlePackage]:
        grouped_text_hits: dict[str, list[RetrievalHit]] = defaultdict(list)
        grouped_image_hits: dict[str, list[RetrievalHit]] = defaultdict(list)
        for hit in text_results:
            grouped_text_hits[hit.article_id].append(hit)
        for hit in image_results:
            grouped_image_hits[hit.article_id].append(hit)

        article_ids = sorted(set(grouped_text_hits) | set(grouped_image_hits))
        packages: list[ArticlePackage] = []
        for article_id in article_ids:
            article_hits = grouped_text_hits.get(article_id, [])
            image_hits_for_article = grouped_image_hits.get(article_id, [])
            title_zh = None
            summary_zh = None
            combined_score = 0.0
            if article_hits:
                title_zh = article_hits[0].title_zh
                summary_zh = article_hits[0].summary_zh
                combined_score = max(combined_score, max(hit.score for hit in article_hits))
            if image_hits_for_article:
                title_zh = title_zh or image_hits_for_article[0].title_zh
                summary_zh = summary_zh or image_hits_for_article[0].summary_zh
                combined_score = max(combined_score, max(hit.score for hit in image_hits_for_article))
            packages.append(
                ArticlePackage(
                    article_id=article_id,
                    title_zh=title_zh,
                    summary_zh=summary_zh,
                    text_hits=article_hits,
                    image_hits=image_hits_for_article,
                    combined_score=combined_score,
                )
            )
        return sorted(packages, key=lambda package: package.combined_score, reverse=True)

    def _collect_citation_locators(
        self,
        text_results: list[RetrievalHit],
        image_results: list[RetrievalHit],
    ) -> list[CitationLocator]:
        seen: set[tuple[str, str | None, int | None]] = set()
        locators: list[CitationLocator] = []
        for hit in [*text_results, *image_results]:
            self._append_citation_locator(locators, seen, hit.citation_locator)
            for grounding_text in hit.grounding_texts:
                self._append_citation_locator(locators, seen, grounding_text.citation_locator)
        return locators

    def _load_text_hit_content(
        self,
        article: Article,
        *,
        chunk_index: int,
        cache_by_article_id: dict[str, dict[int, str]],
    ) -> str:
        chunk_content_map = cache_by_article_id.get(article.article_id)
        if chunk_content_map is None:
            chunk_content_map = self._build_chunk_content_map(article)
            cache_by_article_id[article.article_id] = chunk_content_map
        content = chunk_content_map.get(chunk_index)
        if content is None:
            raise ValueError(
                "missing markdown chunk for text grounding: "
                f"article_id={article.article_id} chunk_index={chunk_index}"
            )
        return content

    def _build_chunk_content_map(self, article: Article) -> dict[int, str]:
        if not article.markdown_rel_path:
            raise ValueError(f"markdown_rel_path is required for text grounding: {article.article_id}")
        markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
        chunks = split_markdown_into_text_chunks(markdown, source_id=article.article_id)
        if not chunks:
            raise ValueError(f"no markdown chunks found for text grounding: {article.article_id}")
        chunk_content_map: dict[int, str] = {}
        for chunk in chunks:
            chunk_index = int(chunk["metadata"]["chunk_index"])
            if chunk_index in chunk_content_map:
                raise ValueError(
                    "duplicate markdown chunk index for text grounding: "
                    f"article_id={article.article_id} chunk_index={chunk_index}"
                )
            chunk_content_map[chunk_index] = str(chunk["page_content"])
        return chunk_content_map

    def _append_citation_locator(
        self,
        locators: list[CitationLocator],
        seen: set[tuple[str, str | None, int | None]],
        locator: CitationLocator,
    ) -> None:
        key = (locator.article_id, locator.article_image_id, locator.chunk_index)
        if key in seen:
            return
        seen.add(key)
        locators.append(locator)

    def _require_payload(self, point: models.ScoredPoint) -> dict[str, object]:
        if not isinstance(point.payload, dict):
            raise ValueError("qdrant scored point missing payload")
        return point.payload

    def _require_str(self, payload: dict[str, object], field_name: str) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"qdrant payload missing required non-empty string field: {field_name}")
        return value

    def _require_int(self, payload: dict[str, object], field_name: str) -> int:
        value = payload.get(field_name)
        if not isinstance(value, int):
            raise ValueError(f"qdrant payload missing required integer field: {field_name}")
        return value

    def _require_image_id(self, payload: dict[str, object]) -> str:
        value = payload.get("article_image_id")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("qdrant image payload missing required article_image_id")
        return value
