"""Persist collected article seeds into the article table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import Article, ensure_article_storage_schema
from backend.app.models.runtime import SOURCE_RUN_MAX_ATTEMPTS, SourceRunState, _utcnow_naive
from backend.app.service.article_contracts import CollectedArticle
from backend.app.service.news_collection_service import NewsCollectionService

INSERT_BATCH_SIZE = 100


@dataclass(frozen=True)
class CollectionResult:
    total_collected: int
    unique_candidates: int
    inserted: int
    skipped_existing: int
    skipped_in_batch: int
    inserted_article_ids: tuple[str, ...]


class ArticleCollectionService:
    def __init__(self) -> None:
        self._collector = NewsCollectionService()

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

    async def collect_source(
        self,
        session: Session,
        *,
        run_id: str,
        source_name: str,
    ) -> CollectionResult:
        ensure_article_storage_schema(session.get_bind())
        state = self._get_or_create_source_state(
            session,
            run_id=run_id,
            source_name=source_name,
        )
        if state.attempts >= SOURCE_RUN_MAX_ATTEMPTS:
            state.status = "abandoned"
            state.updated_at = _utcnow_naive()
            session.commit()
            raise RuntimeError(f"source already exhausted retries: {source_name}")

        try:
            collected = await self._collector.collect_articles(
                source_names=[source_name],
                limit_sources=1,
            )
            result = self.store_articles(collected, session=session)
        except Exception as exc:
            if session.in_transaction():
                session.rollback()
            state = self._get_or_create_source_state(
                session,
                run_id=run_id,
                source_name=source_name,
            )
            state.attempts += 1
            state.status = "abandoned" if state.attempts >= SOURCE_RUN_MAX_ATTEMPTS else "failed"
            state.error = f"{exc.__class__.__name__}: {exc}"
            state.updated_at = _utcnow_naive()
            session.commit()
            raise

        state.status = "done"
        state.error = None
        state.discovered_count = result.total_collected
        state.inserted_count = result.inserted
        state.updated_at = _utcnow_naive()
        session.commit()
        return result

    def store_articles(
        self,
        articles: Iterable[CollectedArticle],
        *,
        session: Session | None = None,
    ) -> CollectionResult:
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

        owns_session = session is None
        if session is None:
            session = SessionLocal()
        inserted = 0
        skipped_existing = 0
        inserted_article_ids: list[str] = []
        try:
            bind = session.get_bind()
            if owns_session:
                ensure_article_storage_schema(bind)

            for batch_start in range(0, len(deduped), INSERT_BATCH_SIZE):
                batch = deduped[batch_start : batch_start + INSERT_BATCH_SIZE]
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
                            parse_status="pending",
                        )
                    )
                    inserted += 1
                    inserted_article_ids.append(article_id)
                if owns_session:
                    session.commit()
                else:
                    session.flush()

            return CollectionResult(
                total_collected=len(materialized),
                unique_candidates=len(deduped),
                inserted=inserted,
                skipped_existing=skipped_existing,
                skipped_in_batch=skipped_in_batch,
                inserted_article_ids=tuple(inserted_article_ids),
            )
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            if owns_session:
                session.close()

    @staticmethod
    def _get_or_create_source_state(
        session: Session,
        *,
        run_id: str,
        source_name: str,
    ) -> SourceRunState:
        state = session.get(
            SourceRunState,
            {"run_id": run_id, "source_name": source_name},
        )
        if state is not None:
            return state

        state = SourceRunState(
            run_id=run_id,
            source_name=source_name,
        )
        session.add(state)
        session.flush()
        return state
