"""Insert publishable article text and image units into Qdrant."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Article, ArticleImage
from backend.app.service.RAG.embedding_service import (
    generate_dense_embedding,
    generate_sparse_embedding,
)
from backend.app.service.RAG.qdrant_service import QdrantService
from backend.app.service.article_chunk_service import split_markdown_into_text_chunks
from backend.app.service.article_parse_service import ArticleMarkdownService

RAG_COLLECTION_NAME = "kff_retrieval"


@dataclass(frozen=True)
class RagInsertResult:
    publishable_articles: int
    text_units: int
    image_units: int
    upserted_units: int


def build_image_retrieval_content(article: Article, image: ArticleImage) -> str:
    """Build the canonical image-lane retrieval content from truth sources."""
    parts = [
        image.caption_raw,
        image.alt_text,
        image.credit_raw,
        image.context_snippet,
        image.ocr_text,
        image.observed_description,
        image.contextual_interpretation,
        article.title_zh,
        article.summary_zh,
        _join_terms(article.tags_json),
        _join_terms(article.brands_json),
    ]
    normalized_parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n".join(normalized_parts)


def has_image_text_projection(image: ArticleImage) -> bool:
    """Return whether an image has at least one retrieval text projection signal."""
    projection_fields = (
        image.alt_text,
        image.caption_raw,
        image.credit_raw,
        image.context_snippet,
        image.ocr_text,
        image.observed_description,
        image.contextual_interpretation,
    )
    return any(isinstance(field, str) and field.strip() for field in projection_fields)


def _join_terms(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return " ".join(str(item).strip() for item in value if str(item).strip())


class ArticleRagService:
    """Build retrieval units from articles and insert them into Qdrant."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        markdown_service: ArticleMarkdownService | None = None,
        qdrant_service: QdrantService | None = None,
        collection_name: str = RAG_COLLECTION_NAME,
    ) -> None:
        self._session_factory = session_factory
        self._markdown_service = markdown_service or ArticleMarkdownService()
        self._qdrant_service = qdrant_service or QdrantService()
        self._collection_name = collection_name

    def upsert_articles(self, articles: list[Article]) -> RagInsertResult:
        """Upsert retrieval units for publishable articles into Qdrant."""
        publishable_articles = sorted(
            [
                article
                for article in articles
                if article.should_publish is True and article.enrichment_status == "done"
            ],
            key=lambda article: (article.ingested_at, article.article_id),
        )
        if not publishable_articles:
            return RagInsertResult(
                publishable_articles=0,
                text_units=0,
                image_units=0,
                upserted_units=0,
            )

        text_records = self._build_text_records(publishable_articles)
        image_records = self._build_image_records(publishable_articles)
        records = text_records + image_records
        if not records:
            return RagInsertResult(
                publishable_articles=len(publishable_articles),
                text_units=0,
                image_units=0,
                upserted_units=0,
            )

        texts = [str(record["content"]) for record in records]
        image_urls = [record.get("image_url") for record in records]
        dense_vectors = generate_dense_embedding(texts, image_urls)
        sparse_vectors = generate_sparse_embedding(texts)

        for record, dense_vector, sparse_vector in zip(
            records,
            dense_vectors,
            sparse_vectors,
            strict=True,
        ):
            record["dense_vector"] = dense_vector
            record["sparse_vector"] = sparse_vector

        upserted_units = self._qdrant_service.upsert_data(self._collection_name, records)
        return RagInsertResult(
            publishable_articles=len(publishable_articles),
            text_units=len(text_records),
            image_units=len(image_records),
            upserted_units=upserted_units,
        )

    def _build_text_records(self, articles: list[Article]) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for article in articles:
            if not article.markdown_rel_path:
                raise ValueError(f"markdown_rel_path is required for RAG text lane: {article.article_id}")

            markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
            chunks = split_markdown_into_text_chunks(markdown, source_id=article.article_id)
            if not chunks:
                raise ValueError(f"no text chunks generated for article: {article.article_id}")

            for chunk in chunks:
                chunk_index = int(chunk["metadata"]["chunk_index"])
                records.append(
                    {
                        "retrieval_unit_id": f"text:{article.article_id}:{chunk_index}",
                        "article_id": article.article_id,
                        "article_image_id": None,
                        "content": str(chunk["page_content"]),
                        "chunk_index": chunk_index,
                        "modality": "text",
                        "source_name": article.source_name,
                        "category": article.category,
                        "tags_json": list(article.tags_json or []),
                        "brands_json": list(article.brands_json or []),
                        "ingested_at": article.ingested_at,
                        "dense_vector": [],
                        "sparse_vector": {},
                        "image_url": None,
                    }
                )
        return records

    def _build_image_records(self, articles: list[Article]) -> list[dict[str, object]]:
        article_by_id = {article.article_id: article for article in articles}
        article_ids = list(article_by_id)
        with self._session_factory() as session:
            images = session.scalars(
                select(ArticleImage)
                .where(ArticleImage.article_id.in_(article_ids))
                .order_by(
                    ArticleImage.article_id.asc(),
                    ArticleImage.position.asc(),
                    ArticleImage.image_id.asc(),
                )
            ).all()

        records: list[dict[str, object]] = []
        for image in images:
            article = article_by_id.get(image.article_id)
            if article is None or image.visual_status != "done":
                continue
            if not has_image_text_projection(image):
                continue

            content = build_image_retrieval_content(article, image)
            if not content:
                continue

            records.append(
                {
                    "retrieval_unit_id": f"image:{image.image_id}",
                    "article_id": image.article_id,
                    "article_image_id": image.image_id,
                    "content": content,
                    "chunk_index": None,
                    "modality": "image",
                    "source_name": article.source_name,
                    "category": article.category,
                    "tags_json": list(article.tags_json or []),
                    "brands_json": list(article.brands_json or []),
                    "ingested_at": article.ingested_at,
                    "dense_vector": [],
                    "sparse_vector": {},
                    "image_url": image.source_url,
                }
            )
        return records
