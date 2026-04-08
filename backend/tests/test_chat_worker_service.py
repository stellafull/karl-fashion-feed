from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret")

from backend.app.models import (  # noqa: E402
    ChatAttachment,
    ChatMessage,
    ChatSession,
    LongTermMemory,
    User,
    ensure_auth_chat_schema,
)
from backend.app.schemas.rag_api import RagAnswerResponse  # noqa: E402
from backend.app.service.chat_worker_service import ChatWorkerService  # noqa: E402


class _CapturingRagService:
    def __init__(
        self,
        *,
        answer_text: str,
        stream_chunks: list[str] | None = None,
    ) -> None:
        self.answer_text = answer_text
        self.stream_chunks = list(stream_chunks or [])
        self.answer_calls: list[dict[str, object]] = []
        self.answer_stream_calls: list[dict[str, object]] = []

    async def answer(self, **kwargs: object) -> RagAnswerResponse:
        self.answer_calls.append(kwargs)
        return RagAnswerResponse(
            answer=self.answer_text,
            citations=[],
            packages=[],
            query_plans=[],
            web_results=[],
        )

    async def answer_stream(self, *, on_delta, **kwargs: object) -> RagAnswerResponse:
        self.answer_stream_calls.append(kwargs)
        for chunk in self.stream_chunks:
            await on_delta(chunk)
        return RagAnswerResponse(
            answer=self.answer_text,
            citations=[],
            packages=[],
            query_plans=[],
            web_results=[],
        )


class ChatWorkerServiceTest(unittest.TestCase):
    def _build_session_factory(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ensure_auth_chat_schema(engine)
        return sessionmaker(bind=engine, future=True, expire_on_commit=False)

    def _write_attachment(self, root: Path, rel_path: str, content: bytes) -> None:
        full_path = root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    def _seed_user_and_session(
        self,
        db: Session,
        *,
        session_id: str = "session-1",
        compact_context: str | None = None,
    ) -> None:
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
        db.add(
            ChatSession(
                chat_session_id=session_id,
                user_id="user-1",
                title="Runway research",
                compact_context=compact_context,
                created_at=datetime(2026, 4, 1, 9, 0, 0),
                updated_at=datetime(2026, 4, 1, 9, 0, 0),
            )
        )

    def test_process_message_by_id_passes_session_context_and_latest_fallback_image(self) -> None:
        session_factory = self._build_session_factory()
        rag_service = _CapturingRagService(answer_text="深入分析")

        with tempfile.TemporaryDirectory() as tmp_dir:
            attachment_root = Path(tmp_dir)
            self._write_attachment(attachment_root, "images/older.png", b"older-image")
            self._write_attachment(attachment_root, "images/latest.png", b"latest-image")

            with session_factory() as db:
                self._seed_user_and_session(
                    db,
                    compact_context="Earlier summary for the stylist.",
                )
                db.add(
                    LongTermMemory(
                        memory_id="memory-1",
                        user_id="user-1",
                        memory_type="preference",
                        memory_key="brand",
                        memory_value="Chanel",
                        source="manual",
                    )
                )
                db.add_all(
                    [
                        ChatMessage(
                            chat_message_id="user-image-old",
                            chat_session_id="session-1",
                            role="user",
                            content_text="older look",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 1, 0),
                        ),
                        ChatMessage(
                            chat_message_id="assistant-old",
                            chat_session_id="session-1",
                            role="assistant",
                            content_text="older answer",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 2, 0),
                            completed_at=datetime(2026, 4, 1, 9, 2, 30),
                        ),
                        ChatMessage(
                            chat_message_id="user-image-latest",
                            chat_session_id="session-1",
                            role="user",
                            content_text="latest look",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 3, 0),
                        ),
                        ChatMessage(
                            chat_message_id="user-current",
                            chat_session_id="session-1",
                            role="user",
                            content_text="please research this styling direction",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 10, 0),
                        ),
                        ChatMessage(
                            chat_message_id="assistant-current",
                            chat_session_id="session-1",
                            role="assistant",
                            content_text="",
                            status="queued",
                            reply_to_message_id="user-current",
                            created_at=datetime(2026, 4, 1, 9, 10, 1),
                        ),
                    ]
                )
                db.add_all(
                    [
                        ChatAttachment(
                            chat_attachment_id="attachment-older",
                            chat_message_id="user-image-old",
                            attachment_type="image",
                            mime_type="image/png",
                            original_filename="older.png",
                            storage_rel_path="images/older.png",
                            size_bytes=len(b"older-image"),
                        ),
                        ChatAttachment(
                            chat_attachment_id="attachment-latest",
                            chat_message_id="user-image-latest",
                            attachment_type="image",
                            mime_type="image/png",
                            original_filename="latest.png",
                            storage_rel_path="images/latest.png",
                            size_bytes=len(b"latest-image"),
                        ),
                    ]
                )
                db.commit()

            with (
                patch("backend.app.service.chat_worker_service.SessionLocal", session_factory),
                patch(
                    "backend.app.service.chat_worker_service.auth_settings.CHAT_ATTACHMENT_ROOT",
                    tmp_dir,
                ),
            ):
                final_message = asyncio.run(
                    ChatWorkerService(rag_service=rag_service).process_message_by_id(
                        "assistant-current"
                    )
                )

        self.assertEqual("done", final_message.status)
        self.assertEqual("深入分析", final_message.content_text)
        self.assertEqual(1, len(rag_service.answer_calls))
        self.assertEqual([], rag_service.answer_stream_calls)

        answer_call = rag_service.answer_calls[0]
        request_context = answer_call["request_context"]
        self.assertEqual(
            "please research this styling direction",
            answer_call["request"].query,
        )
        self.assertEqual(
            "Earlier summary for the stylist.",
            answer_call["conversation_compact"],
        )
        self.assertEqual(
            [
                {"role": "user", "content": "older look"},
                {"role": "assistant", "content": "older answer"},
                {"role": "user", "content": "latest look"},
            ],
            answer_call["recent_messages"],
        )
        self.assertEqual(
            [{"type": "preference", "key": "brand", "value": "Chanel"}],
            answer_call["user_memories"],
        )
        self.assertEqual(1, len(request_context.request_images))
        self.assertEqual(
            b"latest-image",
            base64.b64decode(request_context.request_images[0].base64_data),
        )

        with session_factory() as verify_db:
            assistant_message = verify_db.get(ChatMessage, "assistant-current")
            session = verify_db.get(ChatSession, "session-1")
            self.assertIsNotNone(assistant_message)
            self.assertEqual("done", assistant_message.status)
            self.assertEqual("深入分析", assistant_message.content_text)
            self.assertEqual("深入分析", assistant_message.response_json["answer"])
            self.assertEqual("Earlier summary for the stylist.", session.compact_context)

    def test_process_message_by_id_streams_image_only_requests_with_current_attachment(self) -> None:
        session_factory = self._build_session_factory()
        rag_service = _CapturingRagService(
            answer_text="图像研究结论",
            stream_chunks=["图像", "研究", "结论"],
        )
        deltas: list[str] = []

        async def on_delta(delta: str) -> None:
            deltas.append(delta)

        with tempfile.TemporaryDirectory() as tmp_dir:
            attachment_root = Path(tmp_dir)
            self._write_attachment(attachment_root, "images/fallback.png", b"fallback-image")
            self._write_attachment(attachment_root, "images/current.png", b"current-image")

            with session_factory() as db:
                self._seed_user_and_session(db)
                db.add_all(
                    [
                        ChatMessage(
                            chat_message_id="user-fallback",
                            chat_session_id="session-1",
                            role="user",
                            content_text="older image context",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 1, 0),
                        ),
                        ChatMessage(
                            chat_message_id="user-current",
                            chat_session_id="session-1",
                            role="user",
                            content_text="",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 10, 0),
                        ),
                        ChatMessage(
                            chat_message_id="assistant-current",
                            chat_session_id="session-1",
                            role="assistant",
                            content_text="",
                            status="queued",
                            reply_to_message_id="user-current",
                            created_at=datetime(2026, 4, 1, 9, 10, 1),
                        ),
                    ]
                )
                db.add_all(
                    [
                        ChatAttachment(
                            chat_attachment_id="attachment-fallback",
                            chat_message_id="user-fallback",
                            attachment_type="image",
                            mime_type="image/png",
                            original_filename="fallback.png",
                            storage_rel_path="images/fallback.png",
                            size_bytes=len(b"fallback-image"),
                        ),
                        ChatAttachment(
                            chat_attachment_id="attachment-current",
                            chat_message_id="user-current",
                            attachment_type="image",
                            mime_type="image/png",
                            original_filename="current.png",
                            storage_rel_path="images/current.png",
                            size_bytes=len(b"current-image"),
                        ),
                    ]
                )
                db.commit()

            with (
                patch("backend.app.service.chat_worker_service.SessionLocal", session_factory),
                patch(
                    "backend.app.service.chat_worker_service.auth_settings.CHAT_ATTACHMENT_ROOT",
                    tmp_dir,
                ),
            ):
                final_message = asyncio.run(
                    ChatWorkerService(rag_service=rag_service).process_message_by_id(
                        "assistant-current",
                        on_delta=on_delta,
                    )
                )

        self.assertEqual(["图像", "研究", "结论"], deltas)
        self.assertEqual("done", final_message.status)
        self.assertEqual("图像研究结论", final_message.content_text)
        self.assertEqual([], rag_service.answer_calls)
        self.assertEqual(1, len(rag_service.answer_stream_calls))

        stream_call = rag_service.answer_stream_calls[0]
        self.assertIsNone(stream_call["request"].query)
        self.assertEqual(1, len(stream_call["request_context"].request_images))
        self.assertEqual(
            b"current-image",
            base64.b64decode(
                stream_call["request_context"].request_images[0].base64_data
            ),
        )

        with session_factory() as verify_db:
            assistant_message = verify_db.get(ChatMessage, "assistant-current")
            self.assertIsNotNone(assistant_message)
            self.assertEqual("done", assistant_message.status)
            self.assertEqual("图像研究结论", assistant_message.content_text)
            self.assertEqual("图像研究结论", assistant_message.response_json["answer"])
