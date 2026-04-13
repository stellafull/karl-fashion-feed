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

    user_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="用户主键ID",
    )
    login_name: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
        comment="仅 dev-root 使用的本地登录名",
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
        comment="仅本地调试账号使用的 bcrypt 哈希密码",
    )
    auth_source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="认证来源：feishu 或 local",
    )
    feishu_user_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="飞书组织内稳定 user_id",
    )
    feishu_open_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="飞书 open_id，仅作诊断参考",
    )
    feishu_union_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="飞书 union_id，仅作诊断参考",
    )
    feishu_avatar_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="飞书头像 URL",
    )
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

    @property
    def avatar_url(self) -> str | None:
        """Expose the current avatar URL for auth/profile responses."""
        return self.feishu_avatar_url

    __table_args__ = (
        Index("ix_user_feishu_user_id", "feishu_user_id", unique=True),
    )
