"""Parse collected article seeds into pure-text markdown and image rows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable, Iterable
from uuid import uuid4

import aiohttp
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config.storage_config import ARTICLE_MARKDOWN_ROOT
from backend.app.core.database import SessionLocal
from backend.app.models import Article, ArticleImage, ensure_article_storage_schema
from backend.app.service.article_contracts import MarkdownBlock, ParsedArticle
from backend.app.service.news_collection_service import NewsCollectionService

PARSE_BATCH_SIZE = 20
MAX_PARSE_ATTEMPTS = 3


@dataclass(frozen=True)
class ParseResult:
    candidates: int
    parsed: int
    failed: int
    parsed_article_ids: tuple[str, ...]


class ArticleMarkdownService:
    """Canonical markdown storage and materialization helpers."""

    def __init__(self, root_path: Path | None = None) -> None:
        self.root_path = Path(root_path or ARTICLE_MARKDOWN_ROOT)

    def build_relative_path(
        self,
        *,
        article_id: str,
        reference_time: datetime | None,
    ) -> str:
        dt = reference_time or datetime.now(UTC).replace(tzinfo=None)
        return str(Path(dt.date().isoformat()) / f"{article_id}.md")

    def render_canonical_markdown(
        self,
        *,
        title: str,
        summary: str,
        blocks: Iterable[MarkdownBlock],
    ) -> str:
        lines: list[str] = [f"# {title.strip()}"]
        if summary.strip():
            lines.extend(["", summary.strip()])

        for block in blocks:
            lines.append("")
            if block.kind == "heading":
                lines.append(f"## {block.text.strip()}")
            elif block.kind == "paragraph":
                lines.append(block.text.strip())
            elif block.kind == "list_item":
                lines.append(f"- {block.text.strip()}")
            elif block.kind == "blockquote":
                lines.append(f"> {block.text.strip()}")

        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def write_markdown(self, *, relative_path: str, content: str) -> Path:
        absolute_path = self.root_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(content, encoding="utf-8")
        return absolute_path

    def read_markdown(self, *, relative_path: str) -> str:
        return (self.root_path / relative_path).read_text(encoding="utf-8")


class ArticleParseService:
    def __init__(self) -> None:
        self._collector = NewsCollectionService()
        self._markdown_service = ArticleMarkdownService()

    async def parse_articles(
        self,
        *,
        article_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> ParseResult:
        candidates = self._load_candidates(article_ids=article_ids, limit=limit)
        if not candidates:
            return ParseResult(candidates=0, parsed=0, failed=0, parsed_article_ids=tuple())

        outcomes = await self._parse_batches_with_http_session(candidates)
        return self._persist_outcomes(outcomes)

    def _load_candidates(
        self,
        *,
        article_ids: list[str] | None,
        limit: int | None,
    ) -> list[Article]:
        with SessionLocal() as session:
            query = (
                select(Article)
                .where(Article.parse_status.in_(("pending", "failed")))
                .where(Article.parse_attempts < MAX_PARSE_ATTEMPTS)
                .order_by(Article.discovered_at.asc(), Article.article_id.asc())
            )
            if article_ids:
                query = query.where(Article.article_id.in_(article_ids))
            if limit is not None:
                query = query.limit(limit)
            return session.scalars(query).all()

    async def _parse_batches_with_http_session(
        self,
        candidates: list[Article],
    ) -> list[tuple[str, ParsedArticle | None, Exception | None]]:
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

            async def fetch_bytes(url: str) -> bytes:
                async with semaphore:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        return await response.read()

            outcomes: list[tuple[str, ParsedArticle | None, Exception | None]] = []
            for batch_start in range(0, len(candidates), PARSE_BATCH_SIZE):
                batch = candidates[batch_start : batch_start + PARSE_BATCH_SIZE]
                results = await asyncio.gather(
                    *(
                        self._parse_single(
                            article=article,
                            fetch_text=fetch_text,
                            fetch_bytes=fetch_bytes,
                        )
                        for article in batch
                    )
                )
                outcomes.extend(results)
            return outcomes

    async def _parse_single(
        self,
        *,
        article: Article,
        fetch_text: Callable[[str], Awaitable[str]],
        fetch_bytes: Callable[[str], Awaitable[bytes]],
    ) -> tuple[str, ParsedArticle | None, Exception | None]:
        try:
            html_text = await self._collector.fetch_html(
                source_name=article.source_name,
                url=article.canonical_url,
                fetch_text=fetch_text,
            )
            parsed = self._collector.parse_article_html(
                source_name=article.source_name,
                url=article.canonical_url,
                html_text=html_text,
            )
            parsed = ParsedArticle(
                title=parsed.title,
                summary=parsed.summary,
                markdown_blocks=parsed.markdown_blocks,
                images=await self._collector.attach_image_hashes(
                    images=parsed.images,
                    fetch_bytes=fetch_bytes,
                ),
                published_at=parsed.published_at,
                metadata=parsed.metadata,
            )
            return article.article_id, parsed, None
        except Exception as exc:
            return article.article_id, None, exc

    def _persist_outcomes(
        self,
        outcomes: list[tuple[str, ParsedArticle | None, Exception | None]],
    ) -> ParseResult:
        if not outcomes:
            return ParseResult(candidates=0, parsed=0, failed=0, parsed_article_ids=tuple())

        session = SessionLocal()
        replaced_paths: list[Path] = []
        parsed_count = 0
        failed_count = 0
        parsed_article_ids: list[str] = []
        try:
            bind = session.get_bind()
            ensure_article_storage_schema(bind)

            for batch_start in range(0, len(outcomes), PARSE_BATCH_SIZE):
                batch = outcomes[batch_start : batch_start + PARSE_BATCH_SIZE]
                batch_written_paths: list[Path] = []
                batch_replaced_paths: list[Path] = []
                try:
                    for article_id, parsed, error in batch:
                        stored = session.get(Article, article_id)
                        if stored is None:
                            continue

                        if error is not None or parsed is None:
                            stored.parse_attempts += 1
                            if stored.parse_attempts >= MAX_PARSE_ATTEMPTS:
                                stored.parse_status = "abandoned"
                            else:
                                stored.parse_status = "failed"
                            stored.parse_error = _format_error(error)
                            stored.parse_updated_at = datetime.now(UTC).replace(tzinfo=None)
                            failed_count += 1
                            continue

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
                            existing_image.image_hash = _metadata_string(image.metadata, "image_hash")
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
                            _reuse_duplicate_image(existing_image=existing_image, session=session)
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
                        stored.character_count = _compute_character_count(
                            title=parsed.title,
                            blocks=parsed.markdown_blocks,
                        )
                        stored.published_at = parsed.published_at or stored.published_at
                        stored.markdown_rel_path = relative_path
                        stored.hero_image_id = _select_hero_image_id(parsed.images, image_ids_by_index)
                        stored.metadata_json = {
                            **dict(stored.metadata_json or {}),
                            **dict(parsed.metadata),
                        }
                        stored.parse_status = "done"
                        stored.parse_updated_at = datetime.now(UTC).replace(tzinfo=None)
                        stored.parse_error = None
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


def _select_hero_image_id(images, image_ids_by_index: dict[int, str]) -> str | None:
    for index, image in enumerate(images):
        if image.role == "hero":
            return image_ids_by_index[index]
    if images:
        return image_ids_by_index[0]
    return None


def _format_error(error: Exception | None) -> str:
    if error is None:
        return "RuntimeError: missing parse result"
    return f"{error.__class__.__name__}: {error}"


def _compute_character_count(*, title: str, blocks) -> int:
    parts = [title.strip(), *(block.text.strip() for block in blocks if block.text.strip())]
    return len("\n".join(part for part in parts if part))


def _metadata_string(metadata: dict, key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _reuse_duplicate_image(*, existing_image: ArticleImage, session: Session) -> None:
    if not existing_image.image_hash:
        return

    duplicate = session.execute(
        select(ArticleImage)
        .where(ArticleImage.image_hash == existing_image.image_hash)
        .where(ArticleImage.image_id != existing_image.image_id)
        .order_by(ArticleImage.image_id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if duplicate is None:
        return

    if (existing_image.fetch_status or "discovered") == "discovered" and duplicate.fetch_status != "discovered":
        existing_image.fetch_status = duplicate.fetch_status
    if existing_image.last_fetched_at is None and duplicate.last_fetched_at is not None:
        existing_image.last_fetched_at = duplicate.last_fetched_at
    if not existing_image.mime_type and duplicate.mime_type:
        existing_image.mime_type = duplicate.mime_type
    if existing_image.width is None and duplicate.width is not None:
        existing_image.width = duplicate.width
    if existing_image.height is None and duplicate.height is not None:
        existing_image.height = duplicate.height
    if (existing_image.visual_status or "pending") == "pending" and duplicate.visual_status != "pending":
        existing_image.visual_status = duplicate.visual_status
    if not existing_image.observed_description and duplicate.observed_description:
        existing_image.observed_description = duplicate.observed_description
    if not existing_image.ocr_text and duplicate.ocr_text:
        existing_image.ocr_text = duplicate.ocr_text
    if not existing_image.visible_entities_json and duplicate.visible_entities_json:
        existing_image.visible_entities_json = list(duplicate.visible_entities_json)
    if not existing_image.style_signals_json and duplicate.style_signals_json:
        existing_image.style_signals_json = list(duplicate.style_signals_json)
    if not existing_image.contextual_interpretation and duplicate.contextual_interpretation:
        existing_image.contextual_interpretation = duplicate.contextual_interpretation
    existing_image.analysis_metadata_json = {
        **dict(duplicate.analysis_metadata_json or {}),
        **dict(existing_image.analysis_metadata_json or {}),
    }


def run_parse_article(*, article_id: str) -> ParseResult:
    """Run parse for one article and fail fast if it does not finish successfully."""
    result = asyncio.run(ArticleParseService().parse_articles(article_ids=[article_id], limit=1))
    if result.candidates != 1:
        raise RuntimeError(f"parse candidate not found or not eligible: {article_id}")
    if result.failed:
        raise RuntimeError(f"parse failed for article: {article_id}")
    if result.parsed != 1:
        raise RuntimeError(f"parse did not complete for article: {article_id}")
    return result
