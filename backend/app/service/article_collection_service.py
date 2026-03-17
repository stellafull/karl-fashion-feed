"""Persist collected article seeds into the article table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import Article, ensure_article_storage_schema
from backend.app.service.article_contracts import CollectedArticle
from backend.app.service.news_collection_service import NewsCollectionService


@dataclass(frozen=True)
class CollectionResult:
    total_collected: int
    unique_candidates: int
    inserted: int
    skipped_existing: int
    skipped_in_batch: int
    inserted_article_ids: tuple[str, ...]


class ArticleCollectionService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        collector: NewsCollectionService | None = None,
        insert_batch_size: int = 100,
    ) -> None:
        self._session_factory = session_factory
        self._collector = collector or NewsCollectionService()
        self._insert_batch_size = max(insert_batch_size, 1)

    async def collect_articles(
        self,
        *,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
        published_after: datetime | None = None,
        max_articles_per_source: int | None = None,
        max_pages_per_source: int | None = None,
        include_undated: bool = False,
    ) -> CollectionResult:
        articles = await self._collector.collect_articles(
            source_names=source_names,
            limit_sources=limit_sources,
            published_after=published_after,
            max_articles_per_source=max_articles_per_source,
            max_pages_per_source=max_pages_per_source,
            include_undated=include_undated,
        )
        return self.store_articles(articles)

    def store_articles(self, articles: Iterable[CollectedArticle]) -> CollectionResult:
        materialized = list(articles)
        deduped: list[CollectedArticle] = []
        seen_urls: set[str] = set()
        skipped_in_batch = 0

        for article in materialized:
            if article.canonical_url in seen_urls:
                skipped_in_batch += 1
                continue
            seen_urls.add(article.canonical_url)
            deduped.append(article)

        session = self._session_factory()
        inserted = 0
        skipped_existing = 0
        inserted_article_ids: list[str] = []
        try:
            bind = session.get_bind()
            ensure_article_storage_schema(bind)

            for batch in _chunked(deduped, self._insert_batch_size):
                canonical_urls = [item.canonical_url for item in batch]
                existing_urls = set(
                    session.scalars(
                        select(Article.canonical_url).where(Article.canonical_url.in_(canonical_urls))
                    )
                )
                for article in batch:
                    if article.canonical_url in existing_urls:
                        skipped_existing += 1
                        continue

                    article_id = str(uuid4())
                    session.add(
                        Article(
                            article_id=article_id,
                            source_name=article.source_name,
                            source_type=article.source_type,
                            source_lang=article.lang,
                            category=article.category,
                            canonical_url=article.canonical_url,
                            original_url=article.url,
                            title_raw=article.title,
                            summary_raw=article.summary,
                            published_at=article.published_at,
                            metadata_json=dict(article.metadata),
                            markdown_rel_path=None,
                            hero_image_id=None,
                            content_raw="",
                            image_url=None,
                            parse_status="pending",
                        )
                    )
                    inserted += 1
                    inserted_article_ids.append(article_id)
                session.commit()

            return CollectionResult(
                total_collected=len(materialized),
                unique_candidates=len(deduped),
                inserted=inserted,
                skipped_existing=skipped_existing,
                skipped_in_batch=skipped_in_batch,
                inserted_article_ids=tuple(inserted_article_ids),
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _chunked(values: list[CollectedArticle], size: int) -> list[list[CollectedArticle]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
