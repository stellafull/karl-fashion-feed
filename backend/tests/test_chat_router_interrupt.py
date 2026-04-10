from __future__ import annotations

import os
import unittest
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret")

from backend.app.core.auth_dependencies import get_current_user  # noqa: E402
from backend.app.core.database import get_db  # noqa: E402
from backend.app.models import (  # noqa: E402
    ChatMessage,
    ChatSession,
    User,
    ensure_auth_chat_schema,
)
from backend.app.router.chat_router import router as chat_router  # noqa: E402
from backend.app.service.chat_run_registry import get_chat_run_registry  # noqa: E402
from backend.app.service.chat_session_service import (  # noqa: E402
    build_interrupted_response_json,
    mark_message_interrupted,
)


class ChatRouterInterruptTest(unittest.TestCase):
    def _build_session_factory(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ensure_auth_chat_schema(engine)
        return sessionmaker(bind=engine, future=True)

    def _seed_user(self, db: Session) -> None:
        db.add(
            User(
                user_id="user-1",
                login_name="stylist",
                display_name="Stylist",
                email="stylist@example.com",
                password_hash="hash",
                auth_source="local",
                is_active=True,
                is_admin=False,
            )
        )
        db.commit()

    def _build_app(self, session_factory) -> FastAPI:
        app = FastAPI()
        app.include_router(chat_router)

        def override_get_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id="user-1"
        )
        return app

    def test_interrupt_endpoint_marks_running_message_interrupted(self) -> None:
        session_factory = self._build_session_factory()
        registry = get_chat_run_registry()

        with session_factory() as db:
            self._seed_user(db)
            db.add(
                ChatSession(
                    chat_session_id="session-1",
                    user_id="user-1",
                    title="Session",
                    created_at=datetime(2026, 4, 1, 9, 0, 0),
                    updated_at=datetime(2026, 4, 1, 9, 0, 0),
                )
            )
            db.add(
                ChatMessage(
                    chat_message_id="user-1-message",
                    chat_session_id="session-1",
                    role="user",
                    content_text="请开始生成",
                    status="done",
                    created_at=datetime(2026, 4, 1, 9, 0, 1),
                )
            )
            db.add(
                ChatMessage(
                    chat_message_id="assistant-1-message",
                    chat_session_id="session-1",
                    role="assistant",
                    content_text="部分内容",
                    status="running",
                    reply_to_message_id="user-1-message",
                    created_at=datetime(2026, 4, 1, 9, 0, 2),
                )
            )
            db.commit()

        app = self._build_app(session_factory)

        def cancel_running_message() -> None:
            with session_factory() as db:
                assistant_message = db.get(ChatMessage, "assistant-1-message")
                assert assistant_message is not None
                mark_message_interrupted(
                    assistant_message,
                    response_json=build_interrupted_response_json(
                        assistant_message,
                        default_message_type="chat",
                    ),
                )
                db.commit()

        registry.register("assistant-1-message", cancel_running_message)
        try:
            with TestClient(app) as client:
                response = client.post("/chat/messages/assistant-1-message/interrupt")

            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual("assistant-1-message", payload["chat_message_id"])
            self.assertEqual("interrupted", payload["status"])
            self.assertEqual("interrupted", payload["response_json"]["phase"])

            with session_factory() as db:
                assistant_message = db.get(ChatMessage, "assistant-1-message")
                self.assertIsNotNone(assistant_message)
                self.assertEqual("interrupted", assistant_message.status)
                self.assertIsNone(assistant_message.error_message)
                self.assertEqual("部分内容", assistant_message.content_text)
        finally:
            registry.unregister("assistant-1-message")
