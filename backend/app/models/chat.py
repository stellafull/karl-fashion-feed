"""Chat session, message, attachment, and long-term memory models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ChatSession(Base):
    __tablename__ = "chat_session"

    chat_session_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="会话主键ID",
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户ID",
    )

    # Session metadata
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="会话标题",
    )
    compact_context: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="滚动压缩的历史摘要",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        comment="会话创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        comment="会话最后更新时间",
    )

    __table_args__ = (Index("ix_chat_session_user_updated", "user_id", "updated_at"),)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    chat_message_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="消息主键ID",
    )
    chat_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_session.chat_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属会话ID",
    )

    # Message content
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="消息角色：user或assistant",
    )
    content_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="消息内容文本",
    )

    # Queue status (for assistant messages)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="done",
        comment="消息状态：done/queued/running/failed",
    )

    # Message relationships
    reply_to_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("chat_message.chat_message_id", ondelete="SET NULL"),
        nullable=True,
        comment="回复的消息ID（assistant占位消息指向触发它的user消息）",
    )

    # Assistant response data
    response_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="assistant完成后的结构化响应；chat为RAG结果，deep research为线程元数据",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="执行失败时的错误信息",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        comment="消息创建时间",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="assistant消息开始处理时间",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="assistant消息完成时间",
    )

    __table_args__ = (
        Index("ix_chat_message_session_created", "chat_session_id", "created_at"),
        Index("ix_chat_message_status_created", "status", "created_at"),
    )


class ChatAttachment(Base):
    __tablename__ = "chat_attachment"

    chat_attachment_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="附件主键ID",
    )
    chat_message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_message.chat_message_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属消息ID",
    )

    # Attachment metadata
    attachment_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="附件类型：image",
    )
    mime_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="MIME类型",
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="原始文件名",
    )

    # Storage
    storage_rel_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="相对存储路径",
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="文件大小（字节）",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        comment="附件创建时间",
    )


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"

    memory_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="记忆主键ID",
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户ID",
    )

    # Memory content
    memory_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="记忆类型：preference, profile等",
    )
    memory_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="记忆键名",
    )
    memory_value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="记忆内容",
    )

    # Source tracking
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="manual",
        comment="来源：manual",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        comment="记忆创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        comment="记忆更新时间",
    )

    __table_args__ = (
        Index("ix_long_term_memory_user_type", "user_id", "memory_type"),
        Index(
            "ix_long_term_memory_user_key",
            "user_id",
            "memory_type",
            "memory_key",
            unique=True,
        ),
    )
