"""Rebuild the shared Qdrant retrieval collection from article truth sources."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.database import SessionLocal, engine
from backend.app.models import Article, ensure_article_storage_schema
from backend.app.service.RAG.article_rag_service import ArticleRagService, RAG_COLLECTION_NAME
from backend.app.service.RAG.qdrant_service import QdrantService


@dataclass(frozen=True)
class RebuildSummary:
    article_count: int
    publishable_articles: int
    text_units: int
    image_units: int
    upserted_units: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild the shared Qdrant retrieval collection from publishable articles.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="How many publishable articles to upsert per batch.",
    )
    parser.add_argument(
        "--collection-name",
        default=RAG_COLLECTION_NAME,
        help="Qdrant collection name to rebuild.",
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="Resume rebuilding from this ordered article offset.",
    )
    parser.add_argument(
        "--skip-delete",
        action="store_true",
        help="Do not delete the existing collection before rebuilding.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="How many times to retry one failed batch before aborting.",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=int,
        default=2,
        help="Delay between failed batch retries.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    if args.start_offset < 0:
        raise ValueError("start_offset must be greater than or equal to 0")
    if args.max_retries <= 0:
        raise ValueError("max_retries must be greater than 0")
    if args.retry_delay_seconds <= 0:
        raise ValueError("retry_delay_seconds must be greater than 0")

    ensure_article_storage_schema(engine)
    summary = rebuild_collection(
        batch_size=args.batch_size,
        collection_name=args.collection_name,
        start_offset=args.start_offset,
        skip_delete=args.skip_delete,
        max_retries=args.max_retries,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    print(
        "rag rebuild completed: "
        f"articles={summary.article_count} "
        f"publishable_articles={summary.publishable_articles} "
        f"text_units={summary.text_units} "
        f"image_units={summary.image_units} "
        f"upserted_units={summary.upserted_units}"
    )
    return 0


def rebuild_collection(
    *,
    batch_size: int,
    collection_name: str,
    start_offset: int,
    skip_delete: bool,
    max_retries: int,
    retry_delay_seconds: int,
) -> RebuildSummary:
    qdrant_service = QdrantService()
    client = qdrant_service._client
    if not skip_delete and client.collection_exists(collection_name):
        print(f"deleting existing qdrant collection: {collection_name}")
        client.delete_collection(collection_name)

    with SessionLocal() as session:
        articles = session.scalars(
            select(Article)
            .where(
                Article.should_publish.is_(True),
                Article.enrichment_status == "done",
            )
            .order_by(Article.ingested_at.asc(), Article.article_id.asc())
        ).all()

    if not articles:
        return RebuildSummary(
            article_count=0,
            publishable_articles=0,
            text_units=0,
            image_units=0,
            upserted_units=0,
        )

    rag_service = ArticleRagService(collection_name=collection_name)
    publishable_articles = 0
    text_units = 0
    image_units = 0
    upserted_units = 0

    remaining_articles = articles[start_offset:]
    for relative_start in range(0, len(remaining_articles), batch_size):
        start = start_offset + relative_start
        batch = remaining_articles[relative_start : relative_start + batch_size]
        result = _upsert_batch_with_retry(
            rag_service=rag_service,
            batch=batch,
            start=start,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
        )
        publishable_articles += result.publishable_articles
        text_units += result.text_units
        image_units += result.image_units
        upserted_units += result.upserted_units
        print(
            "rag rebuild batch done: "
            f"start={start} "
            f"size={len(batch)} "
            f"text_units={result.text_units} "
            f"image_units={result.image_units} "
            f"upserted_units={result.upserted_units}"
        )

    return RebuildSummary(
        article_count=len(articles),
        publishable_articles=publishable_articles,
        text_units=text_units,
        image_units=image_units,
        upserted_units=upserted_units,
    )


def _upsert_batch_with_retry(
    *,
    rag_service: ArticleRagService,
    batch: list[Article],
    start: int,
    max_retries: int,
    retry_delay_seconds: int,
):
    for attempt in range(1, max_retries + 1):
        try:
            return rag_service.upsert_articles(batch)
        except Exception as exc:
            if attempt == max_retries:
                raise
            print(
                "rag rebuild batch failed, retrying: "
                f"start={start} "
                f"size={len(batch)} "
                f"article_ids={[article.article_id for article in batch]} "
                f"attempt={attempt}/{max_retries} "
                f"error={exc}"
            )
            time.sleep(retry_delay_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
