"""Chat router for messages, sessions, and attachments."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import get_db
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession
from backend.app.models.user import User
from backend.app.schemas.chat import (
    AttachmentResponse,
    CreateMessageResponse,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=CreateMessageResponse)
async def create_message(
    chat_session_id: Annotated[str | None, Form()] = None,
    content_text: Annotated[str | None, Form()] = None,
    image: Annotated[UploadFile | None, File()] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateMessageResponse:
    """Create a new message in a chat session."""
    normalized_content_text = _normalize_optional_text(content_text)

    # Validate: text and image cannot both be empty
    if normalized_content_text is None and not image:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either content_text or image must be provided",
        )

    # Validate: only one image per message
    if image and not image.content_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid image file",
        )

    if image and not image.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only image files are allowed",
        )

    # Get or create session
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
        # Create new session
        title = _generate_session_title(normalized_content_text, image is not None)
        session = ChatSession(
            user_id=current_user.user_id,
            title=title,
        )
        db.add(session)
        db.flush()

    # Create user message
    user_message = ChatMessage(
        chat_session_id=session.chat_session_id,
        role="user",
        content_text=normalized_content_text or "",
        status="done",
    )
    db.add(user_message)
    db.flush()

    # Save image attachment if provided
    if image:
        attachment_id = str(uuid4())
        file_ext = _get_file_extension(image.filename or "image.jpg")

        # Storage path: YYYY-MM-DD/{message_id}/{attachment_id}.{ext}
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        rel_path = f"{date_str}/{user_message.chat_message_id}/{attachment_id}{file_ext}"

        # Save file to disk
        full_path = Path(auth_settings.CHAT_ATTACHMENT_ROOT) / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        content = await image.read()
        full_path.write_bytes(content)

        # Create attachment record
        attachment = ChatAttachment(
            chat_attachment_id=attachment_id,
            chat_message_id=user_message.chat_message_id,
            attachment_type="image",
            mime_type=image.content_type,
            original_filename=image.filename or "image.jpg",
            storage_rel_path=rel_path,
            size_bytes=len(content),
        )
        db.add(attachment)

    # Create assistant placeholder message
    assistant_message = ChatMessage(
        chat_session_id=session.chat_session_id,
        role="assistant",
        content_text="",
        status="queued",
        reply_to_message_id=user_message.chat_message_id,
    )
    db.add(assistant_message)

    # Update session timestamp
    session.updated_at = datetime.now(UTC).replace(tzinfo=None)

    db.commit()

    return CreateMessageResponse(
        chat_session_id=session.chat_session_id,
        user_message_id=user_message.chat_message_id,
        assistant_message_id=assistant_message.chat_message_id,
    )


@router.get("/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Get a message by ID (for polling assistant status)."""
    message = db.get(ChatMessage, message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    # Check ownership
    session = db.get(ChatSession, message.chat_session_id)
    if not session or session.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this message",
        )

    # Load attachments
    attachments = db.execute(
        select(ChatAttachment).where(ChatAttachment.chat_message_id == message_id)
    ).scalars().all()

    attachment_responses = [
        AttachmentResponse(
            chat_attachment_id=att.chat_attachment_id,
            attachment_type=att.attachment_type,
            mime_type=att.mime_type,
            original_filename=att.original_filename,
            size_bytes=att.size_bytes,
            content_url=f"/api/v1/chat/attachments/{att.chat_attachment_id}/content",
        )
        for att in attachments
    ]

    return MessageResponse(
        chat_message_id=message.chat_message_id,
        role=message.role,
        content_text=message.content_text,
        status=message.status,
        response_json=message.response_json,
        error_message=message.error_message,
        attachments=attachment_responses,
        created_at=message.created_at,
        completed_at=message.completed_at,
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionListResponse:
    """List all chat sessions for current user."""
    sessions = db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.user_id)
        .order_by(ChatSession.updated_at.desc())
    ).scalars().all()

    return SessionListResponse(
        sessions=[SessionResponse.model_validate(s) for s in sessions]
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Get a chat session by ID."""
    session = db.get(ChatSession, session_id)
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

    return SessionResponse.model_validate(session)


@router.get("/sessions/{session_id}/messages", response_model=MessageListResponse)
async def list_session_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageListResponse:
    """List all messages in a chat session."""
    session = db.get(ChatSession, session_id)
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

    messages = db.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()

    # Load attachments for all messages
    message_responses = []
    for message in messages:
        attachments = db.execute(
            select(ChatAttachment).where(
                ChatAttachment.chat_message_id == message.chat_message_id
            )
        ).scalars().all()

        attachment_responses = [
            AttachmentResponse(
                chat_attachment_id=att.chat_attachment_id,
                attachment_type=att.attachment_type,
                mime_type=att.mime_type,
                original_filename=att.original_filename,
                size_bytes=att.size_bytes,
                content_url=f"/api/v1/chat/attachments/{att.chat_attachment_id}/content",
            )
            for att in attachments
        ]

        message_responses.append(
            MessageResponse(
                chat_message_id=message.chat_message_id,
                role=message.role,
                content_text=message.content_text,
                status=message.status,
                response_json=message.response_json,
                error_message=message.error_message,
                attachments=attachment_responses,
                created_at=message.created_at,
                completed_at=message.completed_at,
            )
        )

    return MessageListResponse(messages=message_responses)


@router.get("/attachments/{attachment_id}/content")
async def get_attachment_content(
    attachment_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get attachment file content (with ownership check)."""
    attachment = db.get(ChatAttachment, attachment_id)
    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found",
        )

    # Check ownership through message -> session -> user
    message = db.get(ChatMessage, attachment.chat_message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    session = db.get(ChatSession, message.chat_session_id)
    if not session or session.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this attachment",
        )

    # Return file
    full_path = Path(auth_settings.CHAT_ATTACHMENT_ROOT) / attachment.storage_rel_path
    if not full_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment file not found on disk",
        )

    return FileResponse(
        path=str(full_path),
        media_type=attachment.mime_type,
        filename=attachment.original_filename,
    )


def _generate_session_title(content_text: str | None, has_image: bool) -> str:
    """Generate session title from first message."""
    if content_text:
        return content_text[:30]
    if has_image:
        return "图片对话"
    return "New Chat"


def _get_file_extension(filename: str) -> str:
    """Extract file extension from filename."""
    ext = os.path.splitext(filename)[1]
    return ext if ext else ".jpg"


def _normalize_optional_text(content_text: str | None) -> str | None:
    """Normalize optional chat text by trimming surrounding whitespace."""
    if content_text is None:
        return None
    normalized_content_text = content_text.strip()
    return normalized_content_text or None
