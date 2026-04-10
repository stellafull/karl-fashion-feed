"""Helpers for finalizing interrupted assistant messages via a new DB session."""

from __future__ import annotations

from backend.app.core.database import SessionLocal
from backend.app.schemas.chat import MessageResponse
from backend.app.service.chat_session_service import finalize_interrupted_message


def mark_message_interrupted(
    assistant_message_id: str,
    *,
    partial_content: str | None = None,
    default_message_type: str | None = None,
    error_message: str | None = None,
) -> MessageResponse:
    """Finalize one assistant message as interrupted using a fresh session."""
    with SessionLocal() as db:
        return finalize_interrupted_message(
            db,
            assistant_message_id=assistant_message_id,
            partial_content=partial_content,
            default_message_type=default_message_type,
            error_message=error_message,
        )
