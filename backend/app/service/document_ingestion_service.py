"""Persist collected articles into PostgreSQL documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.app.config.storage import get_document_markdown_root
from backend.app.db.models import Document
from backend.app.db.session import get_session_factory
from backend.app.repository.document_repository import DocumentRepository
from backend.app.service.news_collection_service import collect_articles


SessionFactory = Callable[[], Session]

MAPPED_ARTICLE_KEYS = {
    "article_summary",
    "category_hint",
    "canonical_url",
    "content_hash",
    "content_md_path",
    "content_text",
    "content_type",
    "external_id",
    "id",
    "is_relevant",
    "is_sensitive",
    "published",
    "relevance_reason",
    "relevance_score",
    "source_host",
    "source_id",
    "source_lang",
    "title",
}


def _parse_published_at(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_source_payload(article: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "link": article.get("link"),
        "source": article.get("source"),
        "source_type": article.get("source_type"),
        "image": article.get("image"),
        "content_snippet": article.get("content_snippet"),
        "article_tags": article.get("article_tags") or [],
        "category_id": article.get("category_id"),
    }
    for key, value in article.items():
        if key in MAPPED_ARTICLE_KEYS or value in (None, "", []):
            continue
        payload.setdefault(key, value)
    return payload


def render_article_markdown(article: dict[str, Any]) -> str | None:
    body = (article.get("content_text") or article.get("content_snippet") or "").strip()
    if not body:
        return None

    title = str(article.get("title") or "").strip()
    source = str(article.get("source") or "").strip()
    published = str(article.get("published") or "").strip()

    lines: list[str] = []
    if title:
        lines.extend([f"# {title}", ""])
    if source:
        lines.append(f"- Source: {source}")
    if published:
        lines.append(f"- Published: {published}")
    if source or published:
        lines.append("")
    lines.append(body)
    return "\n".join(lines).strip() + "\n"


def persist_article_markdown(article: dict[str, Any], *, storage_root: Path) -> str | None:
    markdown = render_article_markdown(article)
    if markdown is None:
        return None

    article_id = str(article.get("id") or "").strip()
    if not article_id:
        raise ValueError("Article id is required before persisting cleaned markdown.")

    storage_root.mkdir(parents=True, exist_ok=True)
    markdown_path = storage_root / f"{article_id}.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    return str(markdown_path)


def map_article_to_document(article: dict[str, Any], *, content_md_path: str | None = None) -> Document:
    canonical_url = article.get("canonical_url") or article.get("link") or ""
    return Document(
        article_id=str(article.get("id") or "").strip(),
        source_id=str(article.get("source_id") or "").strip(),
        external_id=(article.get("external_id") or "").strip() or None,
        canonical_url=canonical_url.strip(),
        title=str(article.get("title") or "").strip(),
        domain=(article.get("source_host") or "").strip() or None,
        language=(article.get("source_lang") or "").strip() or None,
        published_at=_parse_published_at(article.get("published")),
        content_md_path=content_md_path,
        content_hash=(article.get("content_hash") or "").strip() or None,
        summary_zh=(article.get("article_summary") or "").strip() or None,
        category_hint=(article.get("category_hint") or "").strip() or None,
        content_type=(article.get("content_type") or "").strip() or None,
        relevance_score=article.get("relevance_score"),
        relevance_reason=(article.get("relevance_reason") or "").strip() or None,
        is_relevant=bool(article.get("is_relevant", True)),
        is_sensitive=bool(article.get("is_sensitive", False)),
        parse_status="parsed",
        source_payload=_build_source_payload(article),
    )


@dataclass(frozen=True)
class DocumentIngestionStats:
    collected_count: int
    existing_count: int
    inserted_count: int


class DocumentIngestionService:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        markdown_storage_root: str | Path | None = None,
    ):
        self._session_factory = session_factory or get_session_factory()
        self._markdown_storage_root = Path(markdown_storage_root) if markdown_storage_root else get_document_markdown_root()

    def collect_and_ingest(self, *, sources_file: str | Path | None = None) -> DocumentIngestionStats:
        articles = collect_articles(sources_file=sources_file)
        return self.ingest_articles(articles)

    def ingest_articles(self, articles: list[dict[str, Any]]) -> DocumentIngestionStats:
        collected_count = len(articles)
        if not articles:
            return DocumentIngestionStats(collected_count=0, existing_count=0, inserted_count=0)

        canonical_urls = [self._canonical_url(article) for article in articles]
        with self._session_factory() as session:
            repository = DocumentRepository(session)
            existing_urls = repository.fetch_existing_canonical_urls(canonical_urls)
            new_articles = [article for article in articles if self._canonical_url(article) not in existing_urls]
            written_markdown_paths: list[Path] = []
            try:
                new_documents = []
                for article in new_articles:
                    content_md_path = persist_article_markdown(
                        article,
                        storage_root=self._markdown_storage_root,
                    )
                    if content_md_path:
                        written_markdown_paths.append(Path(content_md_path))
                    new_documents.append(
                        map_article_to_document(article, content_md_path=content_md_path)
                    )
                repository.add_documents(new_documents)
                session.commit()
            except Exception:
                session.rollback()
                for path in written_markdown_paths:
                    path.unlink(missing_ok=True)
                raise

        return DocumentIngestionStats(
            collected_count=collected_count,
            existing_count=len(existing_urls),
            inserted_count=len(new_articles),
        )

    @staticmethod
    def _canonical_url(article: dict[str, Any]) -> str:
        return str(article.get("canonical_url") or article.get("link") or "").strip()
