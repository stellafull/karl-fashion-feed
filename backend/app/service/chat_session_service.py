"""Shared chat-session persistence helpers used by chat-style routers."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession
from backend.app.models.user import User
from backend.app.schemas.chat import AttachmentResponse, MessageResponse


def normalize_optional_text(content_text: str | None) -> str | None:
    """Normalize optional chat text by trimming surrounding whitespace."""
    if content_text is None:
        return None

    normalized_content_text = content_text.strip()
    return normalized_content_text or None


async def create_message_round(
    *,
    db: Session,
    current_user: User,
    chat_session_id: str | None,
    content_text: str | None,
    images: list[UploadFile],
    assistant_status: str,
) -> tuple[ChatSession, ChatMessage, ChatMessage]:
    """Persist one user message and one assistant placeholder."""
    _validate_message_inputs(content_text, images)

    if chat_session_id:
        session = db.get(ChatSession, chat_session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat session not found",
            )
        if session.user_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this chat session",
            )
    else:
        session = ChatSession(
            user_id=current_user.user_id,
            title=_generate_session_title(content_text, bool(images)),
        )
        db.add(session)
        db.flush()

    user_message = ChatMessage(
        chat_session_id=session.chat_session_id,
        role="user",
        content_text=content_text or "",
        status="done",
    )
    db.add(user_message)
    db.flush()

    for image in images:
        attachment_id = str(uuid4())
        file_ext = _get_file_extension(image.filename or "image.jpg")
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        rel_path = f"{date_str}/{user_message.chat_message_id}/{attachment_id}{file_ext}"
        full_path = Path(auth_settings.CHAT_ATTACHMENT_ROOT) / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        content = await image.read()
        full_path.write_bytes(content)
        db.add(
            ChatAttachment(
                chat_attachment_id=attachment_id,
                chat_message_id=user_message.chat_message_id,
                attachment_type="image",
                mime_type=image.content_type,
                original_filename=image.filename or "image.jpg",
                storage_rel_path=rel_path,
                size_bytes=len(content),
            )
        )

    assistant_message = ChatMessage(
        chat_session_id=session.chat_session_id,
        role="assistant",
        content_text="",
        status=assistant_status,
        reply_to_message_id=user_message.chat_message_id,
    )
    db.add(assistant_message)
    session.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return session, user_message, assistant_message


def build_message_response(
    db: Session,
    message: ChatMessage,
) -> MessageResponse:
    """Serialize one chat message to the API shape."""
    return MessageResponse(
        chat_message_id=message.chat_message_id,
        role=message.role,
        content_text=message.content_text,
        status=message.status,
        response_json=message.response_json,
        error_message=message.error_message,
        attachments=_build_attachment_responses(
            db,
            chat_message_id=message.chat_message_id,
        ),
        created_at=message.created_at,
        completed_at=message.completed_at,
    )


def _build_attachment_responses(
    db: Session,
    *,
    chat_message_id: str,
) -> list[AttachmentResponse]:
    attachments = db.execute(
        select(ChatAttachment).where(ChatAttachment.chat_message_id == chat_message_id)
    ).scalars().all()
    return [
        AttachmentResponse(
            chat_attachment_id=attachment.chat_attachment_id,
            attachment_type=attachment.attachment_type,
            mime_type=attachment.mime_type,
            original_filename=attachment.original_filename,
            size_bytes=attachment.size_bytes,
            content_url=f"/api/v1/chat/attachments/{attachment.chat_attachment_id}/content",
        )
        for attachment in attachments
    ]


def _generate_session_title(content_text: str | None, has_image: bool) -> str:
    if content_text:
        return content_text[:30]
    if has_image:
        return "图片对话"
    return "New Chat"


def _get_file_extension(filename: str) -> str:
    ext = os.path.splitext(filename)[1]
    return ext if ext else ".jpg"


def _validate_message_inputs(
    content_text: str | None,
    images: list[UploadFile],
) -> None:
    if content_text is None and not images:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either content_text or at least one image must be provided",
        )

    for image in images:
        if not image.content_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid image file",
            )
        if not image.content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only image files are allowed",
            )
