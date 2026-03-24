"""Chat request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CreateMessageResponse(BaseModel):
    """Response after creating a new message."""

    chat_session_id: str
    user_message_id: str
    assistant_message_id: str


class StreamMessageStartResponse(BaseModel):
    """Initial payload emitted by the chat streaming endpoint."""

    chat_session_id: str
    session_title: str
    session_updated_at: datetime
    user_message: MessageResponse
    assistant_message: MessageResponse


class AttachmentResponse(BaseModel):
    """Attachment metadata response."""

    chat_attachment_id: str
    attachment_type: str
    mime_type: str
    original_filename: str
    size_bytes: int
    content_url: str


class MessageResponse(BaseModel):
    """Chat message response."""

    chat_message_id: str
    role: str
    content_text: str
    status: str
    response_json: dict | None
    error_message: str | None
    attachments: list[AttachmentResponse]
    created_at: datetime
    completed_at: datetime | None

    class Config:
        from_attributes = True


class SessionResponse(BaseModel):
    """Chat session response."""

    chat_session_id: str
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    """List of chat sessions."""

    sessions: list[SessionResponse]


class MessageListResponse(BaseModel):
    """List of chat messages."""

    messages: list[MessageResponse]
