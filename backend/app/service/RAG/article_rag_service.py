"""Insert normalized article text and source-text image units into Qdrant."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

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
    eligible_articles: int
    text_units: int
    image_units: int
    upserted_units: int


def build_image_retrieval_content(article: Article, image: ArticleImage) -> str:
    """Build image-lane retrieval content from source-provided text signals."""
    parts = [
        image.caption_raw,
        image.alt_text,
        image.credit_raw,
        image.context_snippet,
        article.title_zh,
        article.summary_zh,
    ]
    normalized_parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n".join(normalized_parts)


def has_image_text_projection(image: ArticleImage) -> bool:
    """Return whether an image has source-provided retrieval text."""
    projection_fields = (
        image.alt_text,
        image.caption_raw,
        image.credit_raw,
        image.context_snippet,
    )
    return any(isinstance(field, str) and field.strip() for field in projection_fields)


class ArticleRagService:
    """Build retrieval units from articles and insert them into Qdrant."""

    def __init__(self) -> None:
        self._markdown_service = ArticleMarkdownService()
        self._qdrant_service = QdrantService()
        self._collection_name = RAG_COLLECTION_NAME

    def upsert_articles(self, article_ids: list[str]) -> RagInsertResult:
        """Upsert retrieval units for parse+normalization-complete articles into Qdrant."""
        if not article_ids:
            return RagInsertResult(
                eligible_articles=0,
                text_units=0,
                image_units=0,
                upserted_units=0,
            )

        with SessionLocal() as session:
            articles = session.scalars(
                select(Article)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()

        eligible_articles = [
            article
            for article in articles
            if article.parse_status == "done" and article.normalization_status == "done"
        ]
        if not eligible_articles:
            return RagInsertResult(
                eligible_articles=0,
                text_units=0,
                image_units=0,
                upserted_units=0,
            )

        text_records = self._build_text_records(eligible_articles)
        image_records = self._build_image_records(eligible_articles)
        records = text_records + image_records
        if not records:
            return RagInsertResult(
                eligible_articles=len(eligible_articles),
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
            eligible_articles=len(eligible_articles),
            text_units=len(text_records),
            image_units=len(image_records),
            upserted_units=upserted_units,
        )

    def _build_text_records(self, articles: list[Article]) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for article in articles:
            if not article.body_zh_rel_path:
                raise ValueError(f"body_zh_rel_path is required for RAG text lane: {article.article_id}")

            markdown = self._markdown_service.read_markdown(relative_path=article.body_zh_rel_path)
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
                        "tags_json": [],
                        "brands_json": [],
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
        with SessionLocal() as session:
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
            if article is None:
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
                    "tags_json": [],
                    "brands_json": [],
                    "ingested_at": article.ingested_at,
                    "dense_vector": [],
                    "sparse_vector": {},
                    "image_url": image.source_url,
                }
            )
        return records
