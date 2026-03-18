"""Insert publishable article text and image units into Milvus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Article, ArticleImage
from backend.app.service.RAG.embedding_service import (
    generate_dense_embedding,
    generate_sparse_embedding,
)
from backend.app.service.RAG.milvus_service import MilvusService
from backend.app.service.article_chunk_service import split_markdown_into_text_chunks
from backend.app.service.article_markdown_service import ArticleMarkdownService

RAG_COLLECTION_NAME = "kff_retrieval"


@dataclass(frozen=True)
class RagInsertResult:
    publishable_articles: int
    text_units: int
    image_units: int
    inserted_units: int


class ArticleRagService:
    """Build retrieval units from articles and insert them into Milvus."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        markdown_service: ArticleMarkdownService | None = None,
        milvus_service: MilvusService | None = None,
        collection_name: str = RAG_COLLECTION_NAME,
    ) -> None:
        self._session_factory = session_factory
        self._markdown_service = markdown_service or ArticleMarkdownService()
        self._milvus_service = milvus_service or MilvusService()
        self._collection_name = collection_name

    def insert_articles(self, articles: list[Article]) -> RagInsertResult:
        """Insert retrieval units for publishable articles into Milvus."""
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
                inserted_units=0,
            )

        text_records = self._build_text_records(publishable_articles)
        image_records = self._build_image_records(publishable_articles)
        records = text_records + image_records
        if not records:
            return RagInsertResult(
                publishable_articles=len(publishable_articles),
                text_units=0,
                image_units=0,
                inserted_units=0,
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

        inserted_units = self._milvus_service.insert_data(self._collection_name, records)
        return RagInsertResult(
            publishable_articles=len(publishable_articles),
            text_units=len(text_records),
            image_units=len(image_records),
            inserted_units=inserted_units,
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
                        "ingested_at": int(article.ingested_at.timestamp() * 1000),
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

            content = self._build_image_content(article, image)
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
                    "ingested_at": int(article.ingested_at.timestamp() * 1000),
                    "dense_vector": [],
                    "sparse_vector": {},
                    "image_url": image.source_url,
                }
            )
        return records

    def _build_image_content(self, article: Article, image: ArticleImage) -> str:
        parts = [
            (image.caption_raw or "").strip(),
            (image.alt_text or "").strip(),
            (image.ocr_text or "").strip(),
            (image.observed_description or "").strip(),
            (image.contextual_interpretation or "").strip(),
            (image.context_snippet or "").strip(),
            (article.title_zh or "").strip(),
            (article.summary_zh or "").strip(),
            " ".join(str(tag).strip() for tag in (article.tags_json or []) if str(tag).strip()),
            " ".join(str(brand).strip() for brand in (article.brands_json or []) if str(brand).strip()),
        ]
        return "\n".join(part for part in parts if part)
