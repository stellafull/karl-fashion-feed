"""Parse collected article seeds into pure-text markdown and image rows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import aiohttp
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.article import Article, ArticleImage, ensure_article_storage_schema
from backend.app.service.article_markdown_service import ArticleMarkdownService
from backend.app.service.news_collection_service import NewsCollectionService


FetchText = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class ParseResult:
    candidates: int
    parsed: int
    failed: int
    parsed_article_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ParseOutcome:
    article_id: str
    parsed: object | None = None
    error: Exception | None = None


class ArticleParseService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        collector: NewsCollectionService | None = None,
        markdown_service: ArticleMarkdownService | None = None,
        parse_batch_size: int = 20,
        fetch_text: FetchText | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._collector = collector or NewsCollectionService()
        self._markdown_service = markdown_service or ArticleMarkdownService()
        self._parse_batch_size = max(parse_batch_size, 1)
        self._fetch_text_override = fetch_text

    async def parse_articles(
        self,
        *,
        article_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> ParseResult:
        candidates = self._load_candidates(article_ids=article_ids, limit=limit)
        if not candidates:
            return ParseResult(candidates=0, parsed=0, failed=0, parsed_article_ids=tuple())

        fetch_text = self._fetch_text_override or getattr(self._collector, "_fetch_text_override", None)
        if fetch_text is not None:
            outcomes = await self._parse_batches(candidates=candidates, fetch_text=fetch_text)
        else:
            outcomes = await self._parse_batches_with_http_session(candidates)

        return self._persist_outcomes(outcomes)

    def _load_candidates(
        self,
        *,
        article_ids: list[str] | None,
        limit: int | None,
    ) -> list[Article]:
        with self._session_factory() as session:
            query = (
                select(Article)
                .where(Article.parse_status.in_(("pending", "failed")))
                .order_by(Article.discovered_at.asc(), Article.article_id.asc())
            )
            if article_ids:
                query = query.where(Article.article_id.in_(article_ids))
            if limit is not None:
                query = query.limit(limit)
            return session.scalars(query).all()

    async def _parse_batches_with_http_session(self, candidates: list[Article]) -> list[_ParseOutcome]:
        timeout = aiohttp.ClientTimeout(total=getattr(self._collector, "_request_timeout_seconds", 20))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; KarlFashionFeedBot/0.1; +https://example.com/bot)"
            )
        }
        concurrency = max(getattr(self._collector, "_global_http_concurrency", 16), 1)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:

            async def fetch_text(url: str) -> str:
                async with semaphore:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        return await response.text()

            return await self._parse_batches(candidates=candidates, fetch_text=fetch_text)

    async def _parse_batches(
        self,
        *,
        candidates: list[Article],
        fetch_text: FetchText,
    ) -> list[_ParseOutcome]:
        outcomes: list[_ParseOutcome] = []
        for batch in _chunked(candidates, self._parse_batch_size):
            results = await asyncio.gather(
                *(self._parse_single(article=article, fetch_text=fetch_text) for article in batch)
            )
            outcomes.extend(results)
        return outcomes

    async def _parse_single(
        self,
        *,
        article: Article,
        fetch_text: FetchText,
    ) -> _ParseOutcome:
        try:
            html_text = await fetch_text(article.canonical_url)
            parsed = self._collector.parse_article_html(
                source_name=article.source_name,
                url=article.canonical_url,
                html_text=html_text,
            )
            return _ParseOutcome(article_id=article.article_id, parsed=parsed)
        except Exception as exc:
            return _ParseOutcome(article_id=article.article_id, error=exc)

    def _persist_outcomes(self, outcomes: list[_ParseOutcome]) -> ParseResult:
        if not outcomes:
            return ParseResult(candidates=0, parsed=0, failed=0, parsed_article_ids=tuple())

        session = self._session_factory()
        replaced_paths: list[Path] = []
        parsed_count = 0
        failed_count = 0
        parsed_article_ids: list[str] = []
        try:
            bind = session.get_bind()
            ensure_article_storage_schema(bind)

            for batch in _chunked(outcomes, self._parse_batch_size):
                batch_written_paths: list[Path] = []
                batch_replaced_paths: list[Path] = []
                try:
                    for outcome in batch:
                        stored = session.get(Article, outcome.article_id)
                        if stored is None:
                            continue

                        stored.parse_attempts += 1
                        if outcome.error is not None or outcome.parsed is None:
                            stored.parse_status = "failed"
                            stored.parse_error = _format_error(outcome.error)
                            failed_count += 1
                            continue

                        parsed = outcome.parsed
                        existing_images = session.scalars(
                            select(ArticleImage)
                            .where(ArticleImage.article_id == stored.article_id)
                            .order_by(ArticleImage.position.asc(), ArticleImage.image_id.asc())
                        ).all()
                        existing_by_normalized_url = {
                            image.normalized_url: image for image in existing_images
                        }
                        image_ids_by_index: dict[int, str] = {}
                        seen_normalized_urls: set[str] = set()
                        relative_path = self._markdown_service.build_relative_path(
                            article_id=stored.article_id,
                            reference_time=parsed.published_at or stored.published_at or stored.discovered_at,
                        )
                        markdown = self._markdown_service.render_canonical_markdown(
                            title=parsed.title,
                            summary=parsed.summary,
                            blocks=parsed.markdown_blocks,
                        )
                        batch_written_paths.append(
                            self._markdown_service.write_markdown(
                                relative_path=relative_path,
                                content=markdown,
                            )
                        )

                        if stored.markdown_rel_path and stored.markdown_rel_path != relative_path:
                            batch_replaced_paths.append(
                                self._markdown_service.root_path / stored.markdown_rel_path
                            )

                        for index, image in enumerate(parsed.images):
                            seen_normalized_urls.add(image.normalized_url)
                            existing_image = existing_by_normalized_url.get(image.normalized_url)
                            if existing_image is None:
                                existing_image = ArticleImage(
                                    image_id=str(uuid4()),
                                    article_id=stored.article_id,
                                    source_url=image.source_url,
                                    normalized_url=image.normalized_url,
                                )
                                session.add(existing_image)

                            existing_image.source_url = image.source_url
                            existing_image.normalized_url = image.normalized_url
                            existing_image.role = image.role
                            existing_image.position = index
                            existing_image.alt_text = image.alt_text
                            existing_image.caption_raw = image.caption_raw
                            existing_image.credit_raw = image.credit_raw
                            existing_image.source_kind = image.source_kind
                            existing_image.source_selector = image.source_selector
                            existing_image.context_snippet = image.context_snippet
                            existing_image.analysis_metadata_json = {
                                **dict(existing_image.analysis_metadata_json or {}),
                                **dict(image.metadata),
                            }
                            image_ids_by_index[index] = existing_image.image_id

                        obsolete_image_ids = [
                            image.image_id
                            for image in existing_images
                            if image.normalized_url not in seen_normalized_urls
                        ]
                        if obsolete_image_ids:
                            session.execute(
                                delete(ArticleImage).where(ArticleImage.image_id.in_(obsolete_image_ids))
                            )

                        stored.title_raw = parsed.title
                        stored.summary_raw = parsed.summary
                        stored.published_at = parsed.published_at or stored.published_at
                        stored.markdown_rel_path = relative_path
                        stored.hero_image_id = _select_hero_image_id(parsed.images, image_ids_by_index)
                        stored.image_url = _select_hero_image_url(parsed.images)
                        stored.metadata_json = {
                            **dict(stored.metadata_json or {}),
                            **dict(parsed.metadata),
                        }
                        stored.parse_status = "done"
                        stored.parsed_at = _utcnow_naive()
                        stored.parse_error = None
                        stored.ingested_at = stored.parsed_at
                        parsed_count += 1
                        parsed_article_ids.append(stored.article_id)

                    session.commit()
                    replaced_paths.extend(batch_replaced_paths)
                except Exception:
                    session.rollback()
                    for path in batch_written_paths:
                        if path.exists():
                            path.unlink()
                    raise
        finally:
            session.close()

        for path in replaced_paths:
            if path.exists():
                path.unlink()

        return ParseResult(
            candidates=len(outcomes),
            parsed=parsed_count,
            failed=failed_count,
            parsed_article_ids=tuple(parsed_article_ids),
        )


def _chunked(values: list[Article], size: int) -> list[list[Article]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _select_hero_image_id(images, image_ids_by_index: dict[int, str]) -> str | None:
    for index, image in enumerate(images):
        if image.role == "hero":
            return image_ids_by_index[index]
    if images:
        return image_ids_by_index[0]
    return None


def _select_hero_image_url(images) -> str | None:
    for image in images:
        if image.role == "hero":
            return image.source_url
    if images:
        return images[0].source_url
    return None


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _format_error(error: Exception | None) -> str:
    if error is None:
        return "RuntimeError: missing parse result"
    return f"{error.__class__.__name__}: {error}"
