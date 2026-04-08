"""Deep-research router backed by existing chat/session persistence."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import get_db
from backend.app.models.chat import ChatMessage, ChatSession
from backend.app.models.user import User
from backend.app.schemas.chat import StreamMessageStartResponse
from backend.app.service.chat_session_service import (
    build_message_response,
    create_message_round,
    normalize_optional_text,
)
from backend.app.service.deep_research_service import DeepResearchService

router = APIRouter(prefix="/deep-research", tags=["deep_research"])

_deep_research_service = DeepResearchService()


def get_deep_research_service() -> DeepResearchService:
    """Return the singleton deep-research runtime service."""
    return _deep_research_service


@router.post("/messages/research")
async def create_deep_research_stream(
    request: Request,
    chat_session_id: Annotated[str | None, Form()] = None,
    content_text: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    thread_id: Annotated[str | None, Form()] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    deep_research_service: DeepResearchService = Depends(get_deep_research_service),
) -> StreamingResponse:
    """Create a deep-research message and stream graph progress over SSE."""
    if thread_id is not None:
        _validate_existing_research_thread(
            db=db,
            current_user=current_user,
            chat_session_id=chat_session_id,
            thread_id=thread_id,
        )
    created_new_session = chat_session_id is None
    normalized_content_text = normalize_optional_text(content_text)
    normalized_images = images or []
    resolved_thread_id = thread_id or str(uuid4())
    session, user_message, assistant_message = await create_message_round(
        db=db,
        current_user=current_user,
        chat_session_id=chat_session_id,
        content_text=normalized_content_text,
        images=normalized_images,
        assistant_status="running",
    )
    if created_new_session:
        session.title = _prefix_research_session_title(session.title)
    assistant_message.started_at = datetime.now(UTC).replace(tzinfo=None)
    assistant_message.response_json = {
        "message_type": "deep_research",
        "thread_id": resolved_thread_id,
        "phase": "running",
    }
    db.commit()

    start_payload = StreamMessageStartResponse(
        chat_session_id=session.chat_session_id,
        session_title=session.title,
        session_updated_at=session.updated_at,
        user_message=build_message_response(db, user_message),
        assistant_message=build_message_response(db, assistant_message),
    )
    graph_service = request.app.state.deep_research_graph_service

    async def event_stream():
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

        async def on_event(event_name: str, payload: dict) -> None:
            await queue.put((event_name, payload))

        async def run_processing() -> None:
            try:
                final_message = await deep_research_service.process_message_by_id(
                    assistant_message.chat_message_id,
                    graph=graph_service.graph,
                    thread_id=resolved_thread_id,
                    reuse_thread=thread_id is not None,
                    on_event=on_event,
                )
                event_name = (
                    "message_complete"
                    if final_message.status == "done"
                    else "message_error"
                )
                await queue.put((event_name, final_message.model_dump(mode="json")))
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
        yield _format_sse("message_start", start_payload.model_dump(mode="json"))
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


def _prefix_research_session_title(title: str) -> str:
    if title.startswith("Deep Research · "):
        return title
    return f"Deep Research · {title}"


def _validate_existing_research_thread(
    *,
    db: Session,
    current_user: User,
    chat_session_id: str | None,
    thread_id: str,
) -> None:
    if chat_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="chat_session_id is required when reusing a deep research thread",
        )

    session = db.get(ChatSession, chat_session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )
    if session.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this chat session",
        )

    assistant_payloads = db.execute(
        select(ChatMessage.response_json).where(
            ChatMessage.chat_session_id == chat_session_id,
            ChatMessage.role == "assistant",
        )
    ).scalars()
    if any(
        isinstance(payload, dict)
        and payload.get("message_type") == "deep_research"
        and payload.get("thread_id") == thread_id
        for payload in assistant_payloads
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Deep research thread not found in this chat session",
    )


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
