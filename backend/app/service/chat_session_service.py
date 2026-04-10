"""Shared chat-session persistence helpers used by chat-style routers."""

from __future__ import annotations

import os
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession
from backend.app.models.user import User
from backend.app.schemas.chat import AttachmentResponse, MessageResponse

TERMINAL_MESSAGE_STATUSES = frozenset({"done", "failed", "interrupted"})
STORY_CONTEXT_BODY_MAX_CHARS = 6000


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
    user_response_json: dict[str, Any] | None = None,
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
        response_json=user_response_json,
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


def is_terminal_message_status(status: str) -> bool:
    """Return whether the assistant message is already in a terminal state."""
    return status in TERMINAL_MESSAGE_STATUSES


def build_interrupted_response_json(
    message: ChatMessage,
    *,
    default_message_type: str | None = None,
) -> dict[str, Any]:
    """Build a normalized interrupted response payload for persisted messages."""
    payload = message.response_json if isinstance(message.response_json, dict) else {}
    next_payload = dict(payload)
    if default_message_type and "message_type" not in next_payload:
        next_payload["message_type"] = default_message_type
    next_payload["phase"] = "interrupted"
    return next_payload


def mark_message_interrupted(
    message: ChatMessage,
    *,
    response_json: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """Mark one assistant message as interrupted in-place."""
    message.status = "interrupted"
    message.error_message = error_message
    message.completed_at = datetime.now(UTC).replace(tzinfo=None)
    if response_json is not None:
        message.response_json = response_json


def finalize_interrupted_message(
    db: Session,
    *,
    assistant_message_id: str,
    partial_content: str | None = None,
    default_message_type: str | None = None,
    error_message: str | None = None,
) -> MessageResponse:
    """Persist one assistant message as interrupted and return its response."""
    message = db.get(ChatMessage, assistant_message_id)
    if message is None:
        raise ValueError(f"Assistant message not found: {assistant_message_id}")
    if message.role != "assistant":
        raise ValueError(f"Message is not assistant role: {assistant_message_id}")

    if not is_terminal_message_status(message.status):
        if partial_content is not None:
            message.content_text = partial_content
        mark_message_interrupted(
            message,
            response_json=build_interrupted_response_json(
                message,
                default_message_type=default_message_type,
            ),
            error_message=error_message,
        )
        db.commit()

    return build_message_response(db, message)


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


def parse_story_context_json(story_context_json: str | None) -> dict[str, Any] | None:
    """Parse one optional story-context JSON payload from the client."""
    if story_context_json is None:
        return None

    normalized_payload = story_context_json.strip()
    if not normalized_payload:
        return None

    try:
        payload = json.loads(normalized_payload)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="story_context_json must be valid JSON",
        ) from error

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="story_context_json must decode to an object",
        )

    title = _normalize_story_context_text(payload.get("title"))
    summary = _normalize_story_context_text(payload.get("summary"))
    key_points_raw = payload.get("keyPoints")
    source_names_raw = payload.get("sourceNames")
    body_markdown = _normalize_story_context_text(
        payload.get("bodyMarkdown"),
        max_chars=STORY_CONTEXT_BODY_MAX_CHARS,
    )

    key_points = _normalize_story_context_list(key_points_raw)
    source_names = _normalize_story_context_list(source_names_raw)

    if not title and not summary and not key_points and not body_markdown:
        return None

    return {
        "title": title,
        "summary": summary,
        "key_points": key_points,
        "body_markdown": body_markdown,
        "source_names": source_names,
    }


def extract_story_context_text(message: ChatMessage) -> str | None:
    """Build one hidden story-context prompt from a user message payload."""
    payload = message.response_json if isinstance(message.response_json, dict) else {}
    story_context = payload.get("story_context")
    if not isinstance(story_context, dict):
        return None

    title = _normalize_story_context_text(story_context.get("title"))
    summary = _normalize_story_context_text(story_context.get("summary"))
    key_points = _normalize_story_context_list(story_context.get("key_points"))
    body_markdown = _normalize_story_context_text(
        story_context.get("body_markdown"),
        max_chars=STORY_CONTEXT_BODY_MAX_CHARS,
    )
    source_names = _normalize_story_context_list(story_context.get("source_names"))

    context_lines = ["专题上下文（系统注入，不属于用户显式提问）："]
    if title:
        context_lines.append(f"- 标题：{title}")
    if summary:
        context_lines.append(f"- 摘要：{summary}")
    if key_points:
        context_lines.append("- 关键点：")
        context_lines.extend(f"  - {item}" for item in key_points[:5])
    if source_names:
        context_lines.append(f"- 来源：{' / '.join(source_names[:8])}")
    if body_markdown:
        context_lines.append(f"- 正文摘要：{body_markdown}")

    if len(context_lines) == 1:
        return None
    return "\n".join(context_lines)


def build_user_message_response_json(
    *,
    story_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build hidden per-user-message metadata persisted with the message."""
    if story_context is None:
        return None
    return {"story_context": story_context}


def _normalize_story_context_text(
    value: Any,
    *,
    max_chars: int = 500,
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value[:max_chars]


def _normalize_story_context_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized_values: list[str] = []
    for item in value:
        normalized_item = _normalize_story_context_text(item, max_chars=200)
        if normalized_item:
            normalized_values.append(normalized_item)
    return normalized_values
