"""Database schema bootstrap for auth and chat tables."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from backend.app.core.database import Base
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession, LongTermMemory
from backend.app.models.user import User


def ensure_auth_chat_schema(bind: Engine) -> None:
    """Create auth and chat related tables if they don't exist."""
    tables_to_create = [
        User.__table__,
        ChatSession.__table__,
        ChatMessage.__table__,
        ChatAttachment.__table__,
        LongTermMemory.__table__,
    ]

    Base.metadata.create_all(bind=bind, tables=tables_to_create)
