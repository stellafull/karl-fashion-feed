"""Database schema bootstrap for auth and chat tables."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from backend.app.core.database import Base
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession, LongTermMemory
from backend.app.models.user import User


def ensure_auth_chat_schema(bind: Engine) -> None:
    """Create auth/chat tables and repair the user table shape."""
    tables_to_create = [
        User.__table__,
        ChatSession.__table__,
        ChatMessage.__table__,
        ChatAttachment.__table__,
        LongTermMemory.__table__,
    ]

    Base.metadata.create_all(bind=bind, tables=tables_to_create)
    _ensure_user_columns(bind)


def _apply_schema_statements(bind: Engine, statements: list[str]) -> None:
    if not statements:
        return

    with bind.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_user_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    if "user" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("user")}
    statements: list[str] = []

    if "feishu_user_id" not in columns:
        statements.append('ALTER TABLE "user" ADD COLUMN feishu_user_id VARCHAR(128)')
    if "feishu_open_id" not in columns:
        statements.append('ALTER TABLE "user" ADD COLUMN feishu_open_id VARCHAR(128)')
    if "feishu_union_id" not in columns:
        statements.append('ALTER TABLE "user" ADD COLUMN feishu_union_id VARCHAR(128)')
    if "feishu_avatar_url" not in columns:
        statements.append('ALTER TABLE "user" ADD COLUMN feishu_avatar_url VARCHAR(512)')

    if bind.dialect.name == "postgresql" and "login_name" in columns and not columns["login_name"].get(
        "nullable", True
    ):
        statements.append('ALTER TABLE "user" ALTER COLUMN login_name DROP NOT NULL')

    _apply_schema_statements(bind, statements)
    _ensure_user_indexes(bind)


def _ensure_user_indexes(bind: Engine) -> None:
    inspector = inspect(bind)
    if "user" not in inspector.get_table_names():
        return

    indexes = {index["name"] for index in inspector.get_indexes("user")}
    statements: list[str] = []
    if "ix_user_feishu_user_id" not in indexes:
        statements.append(
            'CREATE UNIQUE INDEX IF NOT EXISTS ix_user_feishu_user_id ON "user" (feishu_user_id)'
        )
    _apply_schema_statements(bind, statements)
