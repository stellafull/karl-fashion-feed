"""Persist collected articles into the database."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import Article
from backend.app.service.news_collection_service import CollectedArticle, NewsCollectionService


@dataclass(frozen=True)
class IngestionResult:
    total_collected: int
    unique_candidates: int
    inserted: int
    skipped_existing: int
    skipped_in_batch: int


class ArticleIngestionService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        collector: NewsCollectionService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._collector = collector or NewsCollectionService()

    async def collect_and_ingest(
        self,
        *,
        source_names: list[str] | None = None,
        limit_sources: int | None = None,
        published_after: datetime | None = None,
        max_articles_per_source: int | None = None,
        max_pages_per_source: int | None = None,
        include_undated: bool = False,
    ) -> IngestionResult:
        articles = await self._collector.collect_articles(
            source_names=source_names,
            limit_sources=limit_sources,
            published_after=published_after,
            max_articles_per_source=max_articles_per_source,
            max_pages_per_source=max_pages_per_source,
            include_undated=include_undated,
        )
        return self.ingest_articles(articles)

    def ingest_articles(self, articles: Iterable[CollectedArticle]) -> IngestionResult:
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
        try:
            existing_urls = set(
                session.scalars(
                    select(Article.canonical_url).where(
                        Article.canonical_url.in_([item.canonical_url for item in deduped])
                    )
                )
            )

            inserted = 0
            skipped_existing = 0
            for article in deduped:
                if article.canonical_url in existing_urls:
                    skipped_existing += 1
                    continue

                session.add(
                    Article(
                        source_name=article.source_name,
                        source_type=article.source_type,
                        source_lang=article.lang,
                        category=article.category,
                        canonical_url=article.canonical_url,
                        original_url=article.url,
                        title_raw=article.title,
                        summary_raw=article.summary,
                        content_raw=article.content,
                        image_url=article.image_url,
                        published_at=article.published_at,
                        metadata_json=article.metadata,
                    )
                )
                inserted += 1

            session.commit()
            return IngestionResult(
                total_collected=len(materialized),
                unique_candidates=len(deduped),
                inserted=inserted,
                skipped_existing=skipped_existing,
                skipped_in_batch=skipped_in_batch,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
