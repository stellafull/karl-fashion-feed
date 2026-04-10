"""Chat router for messages, sessions, and attachments."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import get_db
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession
from backend.app.models.user import User
from backend.app.schemas.chat import (
    CreateMessageResponse,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
    StreamMessageStartResponse,
)
from backend.app.service.chat_worker_service import ChatWorkerService
from backend.app.service.chat_run_registry import get_chat_run_registry
from backend.app.service.chat_session_service import (
    build_user_message_response_json,
    build_interrupted_response_json,
    build_message_response,
    create_message_round,
    parse_story_context_json,
    is_terminal_message_status,
    mark_message_interrupted,
    normalize_optional_text,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=CreateMessageResponse)
async def create_message(
    chat_session_id: Annotated[str | None, Form()] = None,
    content_text: Annotated[str | None, Form()] = None,
    story_context_json: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateMessageResponse:
    """Create a new message in a chat session."""
    normalized_content_text = normalize_optional_text(content_text)
    normalized_images = images or []
    story_context = parse_story_context_json(story_context_json)

    session, user_message, assistant_message = await create_message_round(
        db=db,
        current_user=current_user,
        chat_session_id=chat_session_id,
        content_text=normalized_content_text,
        images=normalized_images,
        assistant_status="queued",
        user_response_json=build_user_message_response_json(
            story_context=story_context,
        ),
    )

    return CreateMessageResponse(
        chat_session_id=session.chat_session_id,
        user_message_id=user_message.chat_message_id,
        assistant_message_id=assistant_message.chat_message_id,
    )


@router.post("/messages/stream")
async def create_message_stream(
    request: Request,
    chat_session_id: Annotated[str | None, Form()] = None,
    content_text: Annotated[str | None, Form()] = None,
    story_context_json: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Create a message and stream the assistant answer over SSE."""
    normalized_content_text = normalize_optional_text(content_text)
    normalized_images = images or []
    story_context = parse_story_context_json(story_context_json)
    session, user_message, assistant_message = await create_message_round(
        db=db,
        current_user=current_user,
        chat_session_id=chat_session_id,
        content_text=normalized_content_text,
        images=normalized_images,
        assistant_status="running",
        user_response_json=build_user_message_response_json(
            story_context=story_context,
        ),
    )
    assistant_message.started_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()

    start_payload = StreamMessageStartResponse(
        chat_session_id=session.chat_session_id,
        session_title=session.title,
        session_updated_at=session.updated_at,
        user_message=build_message_response(db, user_message),
        assistant_message=build_message_response(db, assistant_message),
    )

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        chat_worker_service = ChatWorkerService()
        chat_run_registry = get_chat_run_registry()
        assistant_chunks: list[str] = []
        disconnected = False

        async def on_delta(delta: str) -> None:
            assistant_chunks.append(delta)
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
                if final_message.status == "done":
                    event_name = "message_complete"
                elif final_message.status == "interrupted":
                    event_name = "message_interrupted"
                else:
                    event_name = "message_error"
                await queue.put(
                    (
                        event_name,
                        final_message.model_dump(mode="json"),
                    )
                )
            except asyncio.CancelledError:
                interrupted_message = _finalize_interrupted_message(
                    db=db,
                    assistant_message_id=assistant_message.chat_message_id,
                    partial_content="".join(assistant_chunks),
                    default_message_type="chat",
                )
                await queue.put(
                    (
                        "message_interrupted",
                        interrupted_message.model_dump(mode="json"),
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
        chat_run_registry.register(
            assistant_message.chat_message_id,
            processing_task.cancel,
        )
        try:
            yield _format_sse(
                "message_start",
                start_payload.model_dump(mode="json"),
            )
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                except TimeoutError:
                    if await request.is_disconnected():
                        disconnected = True
                        break
                    continue
                if event is None:
                    break
                event_name, payload = event
                yield _format_sse(event_name, payload)
        finally:
            chat_run_registry.unregister(assistant_message.chat_message_id)
            if not processing_task.done():
                disconnected = True
                processing_task.cancel()
            with suppress(asyncio.CancelledError):
                await processing_task
            if disconnected:
                _finalize_interrupted_message(
                    db=db,
                    assistant_message_id=assistant_message.chat_message_id,
                    partial_content="".join(assistant_chunks),
                    default_message_type="chat",
                )

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

    return build_message_response(db, message)


@router.post("/messages/{message_id}/interrupt", response_model=MessageResponse)
async def interrupt_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Interrupt one active assistant message for the current user."""
    message = _get_owned_message(db, message_id=message_id, current_user=current_user)
    if message.role != "assistant":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only assistant messages can be interrupted",
        )

    if is_terminal_message_status(message.status):
        return build_message_response(db, message)

    if message.status == "queued":
        mark_message_interrupted(
            message,
            response_json=build_interrupted_response_json(
                message,
                default_message_type=_infer_message_type(message),
            ),
        )
        db.commit()
        return build_message_response(db, message)

    if get_chat_run_registry().cancel(message_id):
        interrupted_message = await _wait_for_terminal_message(db, message_id=message_id)
        if interrupted_message is not None:
            return build_message_response(db, interrupted_message)

    db.expire_all()
    refreshed_message = db.get(ChatMessage, message_id)
    if refreshed_message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    if not is_terminal_message_status(refreshed_message.status):
        mark_message_interrupted(
            refreshed_message,
            response_json=build_interrupted_response_json(
                refreshed_message,
                default_message_type=_infer_message_type(refreshed_message),
            ),
        )
        db.commit()

    return build_message_response(db, refreshed_message)


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
        message_responses.append(build_message_response(db, message))

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


def _format_sse(event: str, data: dict) -> str:
    """Serialize one SSE event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _finalize_interrupted_message(
    *,
    db: Session,
    assistant_message_id: str,
    partial_content: str | None = None,
    default_message_type: str | None = None,
) -> MessageResponse:
    message = db.get(ChatMessage, assistant_message_id)
    if message is None:
        raise ValueError(f"Assistant message not found: {assistant_message_id}")

    if message.status in {"done", "failed"}:
        return build_message_response(db, message)

    if partial_content is not None and message.content_text != partial_content:
        message.content_text = partial_content

    message_type = default_message_type or _infer_message_type(message)
    mark_message_interrupted(
        message,
        response_json=build_interrupted_response_json(
            message,
            default_message_type=message_type,
        ),
    )
    db.commit()
    return build_message_response(db, message)


def _get_owned_message(
    db: Session,
    *,
    message_id: str,
    current_user: User,
) -> ChatMessage:
    message = db.get(ChatMessage, message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    session = db.get(ChatSession, message.chat_session_id)
    if session is None or session.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this message",
        )
    return message


def _infer_message_type(message: ChatMessage) -> str:
    if isinstance(message.response_json, dict):
        message_type = message.response_json.get("message_type")
        if isinstance(message_type, str) and message_type.strip():
            return message_type.strip()
    return "chat"


async def _wait_for_terminal_message(
    db: Session,
    *,
    message_id: str,
    timeout_seconds: float = 1.0,
    poll_interval_seconds: float = 0.05,
) -> ChatMessage | None:
    elapsed_seconds = 0.0
    while elapsed_seconds < timeout_seconds:
        await asyncio.sleep(poll_interval_seconds)
        elapsed_seconds += poll_interval_seconds
        db.expire_all()
        message = db.get(ChatMessage, message_id)
        if message is None:
            return None
        if is_terminal_message_status(message.status):
            return message
    return None
