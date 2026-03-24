"""Chat worker service for processing queued assistant messages."""

from __future__ import annotations

import traceback
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession, LongTermMemory
from backend.app.schemas.rag_api import RagQueryRequest, RagRequestContext, RequestImageInput
from backend.app.schemas.rag_query import QueryFilters
from backend.app.service.RAG.rag_answer_service import RagAnswerService


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

        try:
            # Load context
            session = db.get(ChatSession, message.chat_session_id)
            if not session:
                raise ValueError("Session not found")

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

            # Reverse to chronological order
            recent_messages = list(reversed(recent_messages))

            # Load user memories
            user_memories = db.execute(
                select(LongTermMemory).where(LongTermMemory.user_id == session.user_id)
            ).scalars().all()

            # Load current user message attachments
            attachments = db.execute(
                select(ChatAttachment).where(
                    ChatAttachment.chat_message_id == user_message.chat_message_id
                )
            ).scalars().all()

            # Prepare context for RAG
            conversation_compact = session.compact_context
            recent_messages_data = [
                {"role": msg.role, "content": msg.content_text}
                for msg in recent_messages
            ]
            user_memories_data = [
                {
                    "type": mem.memory_type,
                    "key": mem.memory_key,
                    "value": mem.memory_value,
                }
                for mem in user_memories
            ]

            request_image = self._build_request_image(attachments)

            request = RagQueryRequest(
                query=user_message.content_text or None,
                filters=QueryFilters(),
                limit=10,
            )

            request_context = RagRequestContext(
                filters=QueryFilters(),
                limit=10,
                request_image=request_image,
            )

            response = await self.rag_service.answer(
                request=request,
                request_context=request_context,
                conversation_compact=conversation_compact,
                recent_messages=recent_messages_data,
                user_memories=user_memories_data,
            )

            # Success: update message
            message.content_text = response.answer
            message.response_json = response.model_dump(mode="json")
            message.status = "done"
            message.completed_at = datetime.now(UTC).replace(tzinfo=None)

            # Update compact context if needed
            done_count = db.execute(
                select(text("COUNT(*)"))
                .select_from(ChatMessage)
                .where(
                    ChatMessage.chat_session_id == session.chat_session_id,
                    ChatMessage.status == "done",
                )
            ).scalar()

            if done_count > 5:
                # TODO: Compress history using LLM
                # For now, just keep a simple summary
                session.compact_context = f"Previous conversation with {done_count - 5} messages"

            db.commit()
            return True

        except Exception as error:
            # Failure: update message with error
            message.status = "failed"
            message.error_message = (
                f"{type(error).__name__}: {error}\n{traceback.format_exc()}"
            )
            message.completed_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()
            return True

    def _build_request_image(
        self,
        attachments: list[ChatAttachment],
    ) -> RequestImageInput | None:
        """Load one uploaded image attachment into the RAG request context."""
        if not attachments:
            return None

        attachment = attachments[0]
        full_path = Path(auth_settings.CHAT_ATTACHMENT_ROOT) / attachment.storage_rel_path
        if not full_path.is_file():
            raise FileNotFoundError(f"chat attachment file not found: {full_path}")

        return RequestImageInput.from_bytes(
            mime_type=attachment.mime_type,
            content=full_path.read_bytes(),
        )
