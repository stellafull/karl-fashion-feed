"""Retrieval bridge models linking source-of-truth records to index replicas."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class RetrievalUnitRef(Base):
    __tablename__ = "retrieval_unit_ref"

    retrieval_unit_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    modality: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        comment="检索模态：text / image",
    )
    unit_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        comment="检索单元类型：text_chunk / text_group / image_asset",
    )
    article_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("article.article_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    article_image_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("article_image.image_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_unit_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("retrieval_unit_ref.retrieval_unit_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    chunk_index: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )
    heading_path_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    content_locator_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    canonical_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    dense_embedding_ref: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    sparse_embedding_ref: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    milvus_collection: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    milvus_primary_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    index_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        default="v1",
    )
    created_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("pipeline_run.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_searchable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
