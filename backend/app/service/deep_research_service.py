"""Backend-owned deep-research runtime over existing chat persistence."""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.database import SessionLocal
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession
from backend.app.schemas.chat import MessageResponse
from backend.app.schemas.rag_api import RequestImageInput
from backend.app.service.chat_session_service import build_message_response

AsyncEventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

_NODE_NAMES = {
    "clarify",
    "planner",
    "outline_reviser",
    "section_pipeline",
    "section_worker",
    "lead_writer",
    "synthesizer",
    "trend_triangulator",
    "reviewer",
    "reviser",
    "final_check",
}


class DeepResearchService:
    """Execute the vendored deep-research graph against persisted chat state."""

    async def process_message_by_id(
        self,
        assistant_message_id: str,
        *,
        graph: Any,
        thread_id: str,
        reuse_thread: bool,
        on_event: AsyncEventHandler | None = None,
    ) -> MessageResponse:
        """Process one persisted assistant placeholder as a deep-research run."""
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
                graph=graph,
                thread_id=thread_id,
                reuse_thread=reuse_thread,
                on_event=on_event,
            )

    async def _process_assistant_message(
        self,
        db: Session,
        *,
        message: ChatMessage,
        graph: Any,
        thread_id: str,
        reuse_thread: bool,
        on_event: AsyncEventHandler | None = None,
    ) -> MessageResponse:
        session = db.get(ChatSession, message.chat_session_id)
        if session is None:
            raise ValueError("Session not found")

        try:
            user_message = db.get(ChatMessage, message.reply_to_message_id)
            if user_message is None:
                raise ValueError("User message not found")

            attachments = self._load_request_attachments(
                db,
                session_id=session.chat_session_id,
                user_message=user_message,
            )
            object_context = self._build_object_context(attachments)
            config = {"configurable": {"thread_id": thread_id}}
            input_state = {
                "messages": self._build_research_messages(
                    db=db,
                    session=session,
                    user_message=user_message,
                    reuse_thread=reuse_thread,
                ),
                "object_context": object_context,
            }

            clarification_question: str | None = None
            full_report = ""
            final_result: dict[str, Any] | None = None

            async for mode, data in graph.astream(
                input_state,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                if mode == "custom":
                    await self._handle_custom_event(data, on_event=on_event)
                    continue
                if mode != "updates" or not isinstance(data, dict):
                    continue

                for node_name, state_update in data.items():
                    if node_name not in _NODE_NAMES or not isinstance(state_update, dict):
                        continue
                    if on_event is not None:
                        await on_event(
                            "progress",
                            {"node": node_name, "status": "done"},
                        )
                    if node_name == "clarify" and state_update.get("need_clarification"):
                        clarification_question = state_update.get(
                            "clarification_question", ""
                        )
                    if "full_report" in state_update:
                        full_report = state_update["full_report"] or full_report
                    if node_name == "final_check":
                        raw_final_result = state_update.get("final_result")
                        if isinstance(raw_final_result, dict):
                            final_result = raw_final_result

            if clarification_question and not full_report:
                if on_event is not None:
                    await on_event(
                        "clarification",
                        {"question": clarification_question},
                    )
                message.content_text = clarification_question
            else:
                if not full_report:
                    raise ValueError(
                        "Deep research graph completed without report output"
                    )
                if on_event is not None:
                    await on_event(
                        "report",
                        {
                            "content": full_report,
                            "final_result": final_result,
                        },
                    )
                message.content_text = full_report

            needs_clarification = bool(clarification_question and not full_report)
            response_payload = {
                "message_type": "deep_research",
                "thread_id": thread_id,
                "phase": "clarification" if needs_clarification else "final_report",
                "final_result": final_result,
            }
            message.response_json = response_payload
            message.status = "done"
            message.error_message = None
            message.completed_at = datetime.now(UTC).replace(tzinfo=None)
        except Exception as error:
            message.status = "failed"
            message.error_message = (
                f"{type(error).__name__}: {error}\n{traceback.format_exc()}"
            )
            message.completed_at = datetime.now(UTC).replace(tzinfo=None)

        db.commit()
        return build_message_response(db, message)

    async def _handle_custom_event(
        self,
        data: object,
        *,
        on_event: AsyncEventHandler | None,
    ) -> None:
        if on_event is None:
            return
        if not isinstance(data, dict) or data.get("type") != "section_done":
            return
        section_id = data.get("section_id")
        if not isinstance(section_id, str) or not section_id:
            return
        await on_event("section_done", {"section_id": section_id})

    def _build_research_messages(
        self,
        *,
        db: Session,
        session: ChatSession,
        user_message: ChatMessage,
        reuse_thread: bool,
    ) -> list[HumanMessage | AIMessage | SystemMessage]:
        if reuse_thread:
            if user_message.content_text:
                return [HumanMessage(content=user_message.content_text)]
            return [HumanMessage(content="请继续刚才的深度研究。")]

        recent_messages = list(
            reversed(
                db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.chat_session_id == session.chat_session_id,
                        ChatMessage.status == "done",
                        ChatMessage.created_at < user_message.created_at,
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(6)
                ).scalars().all()
            )
        )

        messages: list[HumanMessage | AIMessage | SystemMessage] = []
        if session.compact_context:
            messages.append(
                SystemMessage(content=f"会话历史摘要：{session.compact_context}")
            )
        for chat_message in recent_messages:
            if not chat_message.content_text:
                continue
            if chat_message.role == "assistant":
                messages.append(AIMessage(content=chat_message.content_text))
            else:
                messages.append(HumanMessage(content=chat_message.content_text))

        if user_message.content_text:
            messages.append(HumanMessage(content=user_message.content_text))
        else:
            messages.append(HumanMessage(content="请结合我上传的图片开展深度研究。"))

        return messages

    def _build_object_context(
        self,
        attachments: list[ChatAttachment],
    ) -> str | None:
        if not attachments:
            return None

        first_attachment = attachments[0]
        full_path = Path(auth_settings.CHAT_ATTACHMENT_ROOT) / first_attachment.storage_rel_path
        if not full_path.is_file():
            raise FileNotFoundError(f"chat attachment file not found: {full_path}")
        return RequestImageInput.from_bytes(
            mime_type=first_attachment.mime_type,
            content=full_path.read_bytes(),
        ).to_data_url()

    def _load_request_attachments(
        self,
        db: Session,
        *,
        session_id: str,
        user_message: ChatMessage,
    ) -> list[ChatAttachment]:
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
