"""Article persistence models for metadata, markdown path, and image assets."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Article(Base):
    __tablename__ = "article"

    article_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    source_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        index=True,
        comment="数据来源",
    )
    source_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="数据来源类型，如：rss、web等",
    )
    source_lang: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        comment="数据来源语言",
    )
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="文章分类",
    )
    canonical_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="文章唯一URL",
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始URL")
    title_raw: Mapped[str] = mapped_column(Text, nullable=False, comment="原始标题")
    summary_raw: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="原始摘要/预览",
    )
    markdown_rel_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="按日期分层的Markdown相对路径",
    )
    hero_image_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        comment="主图ID，指向article_image.image_id",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="发布时间",
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Legacy transitional fields retained for backfill compatibility.
    content_raw: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="旧版全文字段，迁移期间保留",
    )
    image_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="旧版单图字段，迁移期间保留",
    )


class ArticleImage(Base):
    __tablename__ = "article_image"

    image_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    article_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("article.article_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="inline")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alt_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    caption_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    credit_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_selector: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visual_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    observed_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visible_entities_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    style_signals_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    contextual_interpretation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    analysis_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


def ensure_article_storage_schema(bind: Engine) -> None:
    """Create new tables and add storage columns needed by the redesign."""

    Base.metadata.create_all(bind=bind, tables=[Article.__table__, ArticleImage.__table__])
    inspector = inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("article")}
    missing_statements = []
    if "markdown_rel_path" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN markdown_rel_path TEXT")
    if "hero_image_id" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN hero_image_id VARCHAR(36)")

    if not missing_statements:
        return

    with bind.begin() as connection:
        for statement in missing_statements:
            connection.execute(text(statement))
