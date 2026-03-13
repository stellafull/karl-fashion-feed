"""Article persistence model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, JSON, String, Text
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
    source_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True, comment="数据来源")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="数据来源类型，如：rss、web等")
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="数据来源语言")
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="文章分类")
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="文章唯一URL")
    original_url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始URL")
    title_raw: Mapped[str] = mapped_column(Text, nullable=False, comment="原始标题")
    summary_raw: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="摘要")
    content_raw: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="正文")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True, comment="封面图URL")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="发布时间")
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
