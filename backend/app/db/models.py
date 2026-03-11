"""SQLAlchemy models for the current persistence scope."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JSON_PAYLOAD_TYPE = JSON().with_variant(JSONB(), "postgresql")


class Document(Base):
    __tablename__ = "document"

    article_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255))
    canonical_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    domain: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_md_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    summary_zh: Mapped[str | None] = mapped_column(Text)
    category_hint: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(64))
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    relevance_reason: Mapped[str | None] = mapped_column(Text)
    is_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="parsed")
    source_payload: Mapped[dict] = mapped_column(JSON_PAYLOAD_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
