"""Regression tests for auth/chat routing and worker behavior."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.app_main import app
from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import Base, get_db
from backend.app.models.chat import ChatAttachment, ChatMessage, ChatSession, LongTermMemory
from backend.app.models.user import User
from backend.app.router.chat_router import router as chat_router  # noqa: F401
from backend.app.schemas.rag_api import RagAnswerResponse, RagRequestContext
from backend.app.service import chat_worker_service as chat_worker_module
from backend.app.service.chat_worker_service import ChatWorkerService


def build_session_local():
    """Create one shared in-memory SQLite session factory for a test case."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, session_local


def make_user(user_id: str = "user-1") -> User:
    """Build one minimal active local user."""
    return User(
        user_id=user_id,
        login_name="root",
        display_name="Root User",
        email="root@example.com",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
    )


class ChatRouterValidationTests(unittest.TestCase):
    """Validate chat request parsing and early rejection behavior."""

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_whitespace_only_message_without_image_returns_422(self) -> None:
        """Whitespace-only text should not be enqueued as a chat message."""

        def override_db():
            yield SimpleNamespace()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: make_user()

        client = TestClient(app)
        response = client.post("/api/v1/chat/messages", data={"content_text": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            "Either content_text or image must be provided",
        )


class ChatWorkerServiceTests(unittest.TestCase):
    """Cover queued assistant processing and multimodal request propagation."""

    def setUp(self) -> None:
        self.engine, self.session_local = build_session_local()

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_process_one_message_passes_attachment_image_to_rag(self) -> None:
        """The worker should load the uploaded image into the RAG request context."""

        captured_contexts: list[RagRequestContext] = []

        class FakeRagService:
            async def answer(
                self,
                *,
                request,
                request_context,
                conversation_compact,
                recent_messages,
                user_memories,
            ):
                captured_contexts.append(request_context)
                return RagAnswerResponse(answer="assistant answer")

        with tempfile.TemporaryDirectory() as temp_dir:
            attachment_root = Path(temp_dir)
            attachment_path = attachment_root / "2026-03-23" / "user-message" / "image.png"
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(b"fake-image-bytes")

            with self.session_local() as session:
                user = make_user()
                chat_session = ChatSession(
                    chat_session_id="session-1",
                    user_id=user.user_id,
                    title="Test Chat",
                    compact_context="summary",
                )
                user_message = ChatMessage(
                    chat_message_id="user-message",
                    chat_session_id=chat_session.chat_session_id,
                    role="user",
                    content_text="",
                    status="done",
                )
                assistant_message = ChatMessage(
                    chat_message_id="assistant-message",
                    chat_session_id=chat_session.chat_session_id,
                    role="assistant",
                    content_text="",
                    status="queued",
                    reply_to_message_id=user_message.chat_message_id,
                )
                attachment = ChatAttachment(
                    chat_attachment_id="attachment-1",
                    chat_message_id=user_message.chat_message_id,
                    attachment_type="image",
                    mime_type="image/png",
                    original_filename="image.png",
                    storage_rel_path="2026-03-23/user-message/image.png",
                    size_bytes=16,
                )
                memory = LongTermMemory(
                    memory_id="memory-1",
                    user_id=user.user_id,
                    memory_type="preference",
                    memory_key="favorite_brand",
                    memory_value="Prada",
                    source="manual",
                )
                session.add(user)
                session.add(chat_session)
                session.add(user_message)
                session.add(assistant_message)
                session.add(attachment)
                session.add(memory)
                session.commit()

            worker = ChatWorkerService(rag_service=FakeRagService())
            with (
                patch.object(chat_worker_module.auth_settings, "CHAT_ATTACHMENT_ROOT", temp_dir),
                self.session_local() as session,
            ):
                processed = asyncio.run(worker.process_one_message(session))

            self.assertTrue(processed)
            [request_context] = captured_contexts
            self.assertIsNotNone(request_context.request_image)
            assert request_context.request_image is not None
            self.assertEqual(request_context.request_image.mime_type, "image/png")

            with self.session_local() as session:
                assistant_message = session.get(ChatMessage, "assistant-message")
                assert assistant_message is not None
                self.assertEqual(assistant_message.status, "done")
                self.assertEqual(assistant_message.content_text, "assistant answer")
