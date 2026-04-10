"""Chat worker service for processing queued assistant messages."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.database import SessionLocal
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession, LongTermMemory
from backend.app.schemas.rag_api import RagQueryRequest, RagRequestContext, RequestImageInput
from backend.app.schemas.rag_query import QueryFilters
from backend.app.schemas.chat import MessageResponse
from backend.app.service.RAG.rag_answer_service import RagAnswerService
from backend.app.service.chat_session_service import (
    build_interrupted_response_json,
    extract_story_context_text,
    mark_message_interrupted,
)

AsyncDeltaHandler = Callable[[str], Awaitable[None]]


class ChatWorkerService:
    """Service for processing queued assistant messages."""

    def __init__(self, rag_service: RagAnswerService | None = None):
        self.rag_service = rag_service or RagAnswerService()

    async def process_one_message(self, db: Session) -> bool:
        """
        Claim and process one queued assistant message.

        Returns True if a message was processed, False if no messages available.
        """
        # Claim one queued assistant message using row lock
        message = db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.role == "assistant",
                ChatMessage.status == "queued",
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()

        if not message:
            return False

        # Update to running
        message.status = "running"
        message.started_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()

        await self._process_assistant_message(db, message=message)
        return True

    async def process_message_by_id(
        self,
        assistant_message_id: str,
        *,
        on_delta: AsyncDeltaHandler | None = None,
    ) -> MessageResponse:
        """Process one assistant message immediately and return its final state."""
        with SessionLocal() as db:
            message = db.get(ChatMessage, assistant_message_id)
            if message is None:
                raise ValueError(f"Assistant message not found: {assistant_message_id}")
            if message.role != "assistant":
                raise ValueError(f"Message is not assistant role: {assistant_message_id}")
            if message.status == "queued":
                message.status = "running"
                message.started_at = datetime.now(UTC).replace(tzinfo=None)
                db.commit()
            return await self._process_assistant_message(
                db,
                message=message,
                on_delta=on_delta,
            )

    async def _process_assistant_message(
        self,
        db: Session,
        *,
        message: ChatMessage,
        on_delta: AsyncDeltaHandler | None = None,
    ) -> MessageResponse:
        session = db.get(ChatSession, message.chat_session_id)
        if not session:
            raise ValueError("Session not found")

        streamed_answer_parts: list[str] = []

        try:
            # Get current user message
            user_message = db.get(ChatMessage, message.reply_to_message_id)
            if not user_message:
                raise ValueError("User message not found")

            # Load recent 5 done messages (excluding current round)
            recent_messages = db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.chat_session_id == session.chat_session_id,
                    ChatMessage.status == "done",
                    ChatMessage.created_at < user_message.created_at,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(5)
            ).scalars().all()

            recent_messages = list(reversed(recent_messages))

            user_memories = db.execute(
                select(LongTermMemory).where(LongTermMemory.user_id == session.user_id)
            ).scalars().all()

            attachments = self._load_request_attachments(
                db,
                session_id=session.chat_session_id,
                user_message=user_message,
            )

            conversation_compact = session.compact_context
            story_context_text = extract_story_context_text(user_message)
            if story_context_text:
                conversation_compact = (
                    f"{story_context_text}\n\n{conversation_compact}"
                    if conversation_compact
                    else story_context_text
                )
            recent_messages_data = [
                {"role": chat_message.role, "content": chat_message.content_text}
                for chat_message in recent_messages
            ]
            user_memories_data = [
                {
                    "type": memory.memory_type,
                    "key": memory.memory_key,
                    "value": memory.memory_value,
                }
                for memory in user_memories
            ]

            request_images = self._build_request_images(attachments)
            request = RagQueryRequest(
                query=user_message.content_text or None,
                filters=QueryFilters(),
                limit=10,
            )
            request_context = RagRequestContext(
                filters=QueryFilters(),
                limit=10,
                request_images=request_images,
            )

            async def handle_delta(delta: str) -> None:
                streamed_answer_parts.append(delta)
                if on_delta is not None:
                    await on_delta(delta)

            if on_delta is None:
                response = await self.rag_service.answer(
                    request=request,
                    request_context=request_context,
                    conversation_compact=conversation_compact,
                    recent_messages=recent_messages_data,
                    user_memories=user_memories_data,
                )
            else:
                response = await self.rag_service.answer_stream(
                    request=request,
                    request_context=request_context,
                    conversation_compact=conversation_compact,
                    recent_messages=recent_messages_data,
                    user_memories=user_memories_data,
                    on_delta=handle_delta,
                )

            message.content_text = response.answer
            message.response_json = response.model_dump(mode="json")
            message.status = "done"
            message.error_message = None
            message.completed_at = datetime.now(UTC).replace(tzinfo=None)

            done_count = db.execute(
                select(text("COUNT(*)"))
                .select_from(ChatMessage)
                .where(
                    ChatMessage.chat_session_id == session.chat_session_id,
                    ChatMessage.status == "done",
                )
            ).scalar()

            if done_count > 5:
                session.compact_context = (
                    f"Previous conversation with {done_count - 5} messages"
                )

        except asyncio.CancelledError:
            partial_answer = "".join(streamed_answer_parts).strip()
            if partial_answer:
                message.content_text = partial_answer
            mark_message_interrupted(
                message,
                response_json=build_interrupted_response_json(
                    message,
                    default_message_type="chat",
                ),
            )
        except Exception as error:
            message.status = "failed"
            message.error_message = (
                f"{type(error).__name__}: {error}\n{traceback.format_exc()}"
            )
            message.completed_at = datetime.now(UTC).replace(tzinfo=None)

        db.commit()
        return MessageResponse(
            chat_message_id=message.chat_message_id,
            role=message.role,
            content_text=message.content_text,
            status=message.status,
            response_json=message.response_json,
            error_message=message.error_message,
            attachments=[],
            created_at=message.created_at,
            completed_at=message.completed_at,
        )

    def _build_request_images(
        self,
        attachments: list[ChatAttachment],
    ) -> list[RequestImageInput]:
        """Load uploaded image attachments into the RAG request context."""
        request_images: list[RequestImageInput] = []
        for attachment in attachments:
            full_path = Path(auth_settings.CHAT_ATTACHMENT_ROOT) / attachment.storage_rel_path
            if not full_path.is_file():
                raise FileNotFoundError(f"chat attachment file not found: {full_path}")

            request_images.append(
                RequestImageInput.from_bytes(
                    mime_type=attachment.mime_type,
                    content=full_path.read_bytes(),
                )
            )

        return request_images

    def _load_request_attachments(
        self,
        db: Session,
        *,
        session_id: str,
        user_message: ChatMessage,
    ) -> list[ChatAttachment]:
        """Load current-turn attachments, or reuse the latest earlier user image."""
        current_attachments = db.execute(
            select(ChatAttachment).where(
                ChatAttachment.chat_message_id == user_message.chat_message_id
            )
        ).scalars().all()
        if current_attachments:
            return current_attachments

        fallback_message_id = db.execute(
            select(ChatMessage.chat_message_id)
            .join(
                ChatAttachment,
                ChatAttachment.chat_message_id == ChatMessage.chat_message_id,
            )
            .where(
                ChatMessage.chat_session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.created_at < user_message.created_at,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if fallback_message_id is None:
            return []

        return db.execute(
            select(ChatAttachment).where(
                ChatAttachment.chat_message_id == fallback_message_id
            )
        ).scalars().all()
