"""Chat router for messages, sessions, and attachments."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import SessionLocal, get_db
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession
from backend.app.models.user import User
from backend.app.schemas.chat import (
    AttachmentResponse,
    CreateMessageResponse,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
    StreamMessageStartResponse,
)
from backend.app.service.chat_worker_service import ChatWorkerService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=CreateMessageResponse)
async def create_message(
    chat_session_id: Annotated[str | None, Form()] = None,
    content_text: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateMessageResponse:
    """Create a new message in a chat session."""
    normalized_content_text = _normalize_optional_text(content_text)
    normalized_images = images or []

    session, user_message, assistant_message = await _create_message_round(
        db=db,
        current_user=current_user,
        chat_session_id=chat_session_id,
        content_text=normalized_content_text,
        images=normalized_images,
        assistant_status="queued",
    )

    return CreateMessageResponse(
        chat_session_id=session.chat_session_id,
        user_message_id=user_message.chat_message_id,
        assistant_message_id=assistant_message.chat_message_id,
    )


@router.post("/messages/stream")
async def create_message_stream(
    chat_session_id: Annotated[str | None, Form()] = None,
    content_text: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Create a message and stream the assistant answer over SSE."""
    normalized_content_text = _normalize_optional_text(content_text)
    normalized_images = images or []
    session, user_message, assistant_message = await _create_message_round(
        db=db,
        current_user=current_user,
        chat_session_id=chat_session_id,
        content_text=normalized_content_text,
        images=normalized_images,
        assistant_status="running",
    )
    assistant_message.started_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()

    start_payload = StreamMessageStartResponse(
        chat_session_id=session.chat_session_id,
        session_title=session.title,
        session_updated_at=session.updated_at,
        user_message=_build_message_response(db, user_message),
        assistant_message=_build_message_response(db, assistant_message),
    )

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        chat_worker_service = ChatWorkerService()

        async def on_delta(delta: str) -> None:
            await queue.put(
                (
                    "assistant_delta",
                    {"delta": delta},
                )
            )

        async def run_processing() -> None:
            try:
                final_message = await chat_worker_service.process_message_by_id(
                    assistant_message.chat_message_id,
                    on_delta=on_delta,
                )
                event_name = (
                    "message_complete"
                    if final_message.status == "done"
                    else "message_error"
                )
                await queue.put(
                    (
                        event_name,
                        final_message.model_dump(mode="json"),
                    )
                )
            except Exception as error:
                await queue.put(
                    (
                        "message_error",
                        {"detail": f"{type(error).__name__}: {error}"},
                    )
                )
            finally:
                await queue.put(None)

        processing_task = asyncio.create_task(run_processing())
        yield _format_sse(
            "message_start",
            start_payload.model_dump(mode="json"),
        )
        while True:
            event = await queue.get()
            if event is None:
                break
            event_name, payload = event
            yield _format_sse(event_name, payload)
        await processing_task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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

    return _build_message_response(db, message)


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
        message_responses.append(_build_message_response(db, message))

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


def _format_sse(event: str, data: dict) -> str:
    """Serialize one SSE event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _validate_message_inputs(
    content_text: str | None,
    images: list[UploadFile],
) -> None:
    """Validate the chat message form input."""
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


async def _create_message_round(
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


def _build_attachment_responses(
    db: Session,
    *,
    chat_message_id: str,
) -> list[AttachmentResponse]:
    """Load attachment DTOs for one chat message."""
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


def _build_message_response(
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
