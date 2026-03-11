"""Persistence helpers for documents."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.db.models import Document


class DocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def fetch_existing_canonical_urls(self, canonical_urls: Iterable[str], *, chunk_size: int = 500) -> set[str]:
        urls = [url for url in canonical_urls if url]
        existing: set[str] = set()
        for start in range(0, len(urls), chunk_size):
            chunk = urls[start:start + chunk_size]
            if not chunk:
                continue
            stmt: Select[tuple[str]] = select(Document.canonical_url).where(Document.canonical_url.in_(chunk))
            existing.update(self.session.scalars(stmt).all())
        return existing

    def add_documents(self, documents: Iterable[Document]) -> None:
        self.session.add_all(list(documents))
