"""Image domain ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ArticleImage(Base):
    """Image fact model attached to an article but maintained as its own domain entity."""

    __tablename__ = "article_image"

    # Identity and ownership.
    image_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="图片主键ID",
    )
    article_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("article.article_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属文章ID",
    )

    # Source identity and dedupe anchor.
    source_url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始图片URL")
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True, comment="归一化图片URL")
    image_hash: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        index=True,
        comment="感知哈希，用于跨文章图片去重和分析复用",
    )

    # Placement inside the source article.
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="inline", comment="图片在文章中的角色")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="图片在文章中的顺序位置")

    # Raw text context captured from the page.
    alt_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="图片alt文本")
    caption_raw: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="图片caption原文")
    credit_raw: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="图片署名或来源原文")

    # Extraction trace. These may later be renamed to image-domain terms.
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="", comment="图片抽取来源类型")
    source_selector: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="图片抽取定位线索")
    context_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="图片附近上下文片段")

    # Asset fetch state and stable binary metadata.
    fetch_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="discovered",
        comment="图片抓取或资产处理状态",
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        comment="最近一次抓取或确认时间",
    )
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="图片MIME类型")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="图片宽度")
    height: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="图片高度")

    # Visual analysis workflow state.
    visual_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="视觉分析状态",
    )
    visual_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="视觉分析尝试次数",
    )

    # Visual analysis outputs. Keep "observed" separate from contextual interpretation.
    observed_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="仅描述肉眼可见事实的视觉说明",
    )
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="图片OCR文本")
    visible_entities_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="图片中可见实体列表",
    )
    style_signals_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="图片风格信号列表",
    )
    contextual_interpretation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="结合上下文得到的图片解释",
    )
    analysis_metadata_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="视觉分析附加元数据",
    )
