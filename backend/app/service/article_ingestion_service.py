"""Persist collected article metadata, markdown files, and image assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import Article, ArticleImage, ensure_article_storage_schema
from backend.app.service.article_contracts import CollectedArticle
from backend.app.service.article_markdown_service import ArticleMarkdownService
from backend.app.service.news_collection_service import NewsCollectionService


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
        markdown_service: ArticleMarkdownService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._collector = collector or NewsCollectionService()
        self._markdown_service = markdown_service or ArticleMarkdownService()

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
        written_paths: list[Path] = []
        try:
            bind = session.get_bind()
            ensure_article_storage_schema(bind)

            canonical_urls = [item.canonical_url for item in deduped]
            existing_urls = set(
                session.scalars(
                    select(Article.canonical_url).where(Article.canonical_url.in_(canonical_urls))
                )
            )

            inserted = 0
            skipped_existing = 0
            for article in deduped:
                if article.canonical_url in existing_urls:
                    skipped_existing += 1
                    continue

                article_id = str(uuid4())
                image_ids_by_index = {
                    index: str(uuid4()) for index, _ in enumerate(article.images)
                }
                relative_path = self._markdown_service.build_relative_path(
                    article_id=article_id,
                    reference_time=article.published_at,
                )
                markdown = self._markdown_service.render_canonical_markdown(
                    title=article.title,
                    summary=article.summary,
                    blocks=article.markdown_blocks,
                    image_ids_by_index=image_ids_by_index,
                )
                written_paths.append(
                    self._markdown_service.write_markdown(
                        relative_path=relative_path,
                        content=markdown,
                    )
                )

                hero_image_id = _select_hero_image_id(article, image_ids_by_index)
                hero_image_url = _select_hero_image_url(article)

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
                        markdown_rel_path=relative_path,
                        hero_image_id=hero_image_id,
                        published_at=article.published_at,
                        metadata_json={
                            **article.metadata,
                            "image_count": len(article.images),
                            "block_count": len(article.markdown_blocks),
                        },
                        content_raw="",
                        image_url=hero_image_url,
                    )
                )
                for index, image in enumerate(article.images):
                    session.add(
                        ArticleImage(
                            image_id=image_ids_by_index[index],
                            article_id=article_id,
                            source_url=image.source_url,
                            normalized_url=image.normalized_url,
                            role=image.role,
                            position=index,
                            alt_text=image.alt_text,
                            caption_raw=image.caption_raw,
                            credit_raw=image.credit_raw,
                            source_kind=image.source_kind,
                            source_selector=image.source_selector,
                            context_snippet=image.context_snippet,
                            analysis_metadata_json=image.metadata,
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
            for path in written_paths:
                if path.exists():
                    path.unlink()
            raise
        finally:
            session.close()


def _select_hero_image_id(article: CollectedArticle, image_ids_by_index: dict[int, str]) -> str | None:
    for index, image in enumerate(article.images):
        if image.role == "hero":
            return image_ids_by_index[index]
    if article.images:
        return image_ids_by_index[0]
    return None


def _select_hero_image_url(article: CollectedArticle) -> str | None:
    for image in article.images:
        if image.role == "hero":
            return image.source_url
    if article.images:
        return article.images[0].source_url
    return None
