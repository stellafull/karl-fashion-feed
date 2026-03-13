"""Raw document layer models."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.models.common import JSON_PAYLOAD_TYPE, utcnow


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
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="parsed", index=True)
    source_payload: Mapped[dict] = mapped_column(JSON_PAYLOAD_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class DocumentAsset(Base):
    __tablename__ = "document_asset"
    __table_args__ = (
        UniqueConstraint("article_id", "asset_url", name="uq_document_asset_article_url"),
    )

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    article_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document.article_id"),
        nullable=False,
        index=True,
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_url: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    asset_text: Mapped[str | None] = mapped_column(Text)
    visual_description: Mapped[str | None] = mapped_column(Text)
    asset_role: Mapped[str | None] = mapped_column(String(32))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON_PAYLOAD_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class RetrievalUnitRef(Base):
    __tablename__ = "retrieval_unit_ref"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "unit_type",
            "chunk_index",
            name="uq_retrieval_unit_ref_article_unit_chunk",
        ),
    )

    unit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    article_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document.article_id"),
        nullable=False,
        index=True,
    )
    unit_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    asset_url: Mapped[str | None] = mapped_column(Text)
    dense_embedding_provider: Mapped[str | None] = mapped_column(String(64))
    dense_embedding_model: Mapped[str | None] = mapped_column(String(128), index=True)
    dense_embedding_version: Mapped[str | None] = mapped_column(String(64))
    sparse_embedding_provider: Mapped[str | None] = mapped_column(String(64))
    sparse_embedding_model: Mapped[str | None] = mapped_column(String(128), index=True)
    sparse_embedding_version: Mapped[str | None] = mapped_column(String(64))
    content_version_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
