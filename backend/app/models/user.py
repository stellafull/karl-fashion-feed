"""User authentication and profile model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "user"

    # Primary identity
    user_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="用户主键ID",
    )

    # Authentication credentials
    login_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="本地登录名",
    )
    display_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="显示名称",
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
        comment="用户邮箱",
    )
    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="bcrypt哈希密码",
    )

    # Authentication source
    auth_source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="认证来源：local或sso",
    )

    # OAuth/SSO fields (future-ready)
    sso_provider: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="SSO提供商：google, github等",
    )
    sso_subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="SSO提供商的用户唯一标识",
    )

    # Account status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="账户是否激活",
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否为管理员",
    )

    # Timestamps
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="最后登录时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        comment="账户创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        comment="账户最后更新时间",
    )

    __table_args__ = (
        Index("ix_user_sso_lookup", "sso_provider", "sso_subject", unique=True),
    )
