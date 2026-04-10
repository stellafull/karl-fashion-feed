from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret")

from backend.app.core.auth_dependencies import get_current_user  # noqa: E402
from backend.app.core.database import get_db  # noqa: E402
from backend.app.models import (  # noqa: E402
    ChatAttachment,
    ChatMessage,
    ChatSession,
    User,
    ensure_auth_chat_schema,
)
from backend.app.router.deep_research_router import (  # noqa: E402
    get_deep_research_service,
    router as deep_research_router,
)
from backend.app.schemas.chat import MessageResponse  # noqa: E402
from backend.app.service.chat_interrupt_service import (  # noqa: E402
    mark_message_interrupted as finalize_interrupted_message,
)
from backend.app.service.deep_research_graph_service import DeepResearchGraphService  # noqa: E402
from backend.app.service.deep_research_service import DeepResearchService  # noqa: E402


def _parse_sse_events(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        stripped_block = block.strip()
        if not stripped_block:
            continue
        event_name = ""
        payload: dict[str, object] | None = None
        for line in stripped_block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        events.append((event_name, payload or {}))
    return events


class _FakeGraph:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self._events = list(events)
        self.calls: list[dict[str, object]] = []

    async def astream(self, input_state, *, config, stream_mode):
        self.calls.append(
            {
                "input_state": input_state,
                "config": config,
                "stream_mode": stream_mode,
            }
        )
        for event in self._events:
            yield event


class _CapturingRouterService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def process_message_by_id(
        self,
        assistant_message_id: str,
        *,
        graph,
        thread_id: str,
        reuse_thread: bool,
        on_event,
    ) -> MessageResponse:
        self.calls.append(
            {
                "assistant_message_id": assistant_message_id,
                "graph": graph,
                "thread_id": thread_id,
                "reuse_thread": reuse_thread,
            }
        )
        await on_event("progress", {"node": "planner", "status": "done"})
        await on_event(
            "report",
            {
                "content": "路由烟雾测试报告",
                "final_result": {"status": "ok"},
            },
        )
        return MessageResponse(
            chat_message_id=assistant_message_id,
            role="assistant",
            content_text="路由烟雾测试报告",
            status="done",
            response_json={
                "message_type": "deep_research",
                "thread_id": thread_id,
                "phase": "final_report",
                "final_result": {"status": "ok"},
            },
            error_message=None,
            attachments=[],
            created_at=datetime.now(UTC).replace(tzinfo=None),
            completed_at=datetime.now(UTC).replace(tzinfo=None),
        )


class _ClarifyingRouterService:
    async def process_message_by_id(
        self,
        assistant_message_id: str,
        *,
        graph,
        thread_id: str,
        reuse_thread: bool,
        on_event,
    ) -> MessageResponse:
        await on_event("clarification", {"question": "请确认要聚焦的品牌"})
        return MessageResponse(
            chat_message_id=assistant_message_id,
            role="assistant",
            content_text="请确认要聚焦的品牌",
            status="done",
            response_json={
                "message_type": "deep_research",
                "thread_id": thread_id,
                "phase": "clarification",
                "final_result": None,
            },
            error_message=None,
            attachments=[],
            created_at=datetime.now(UTC).replace(tzinfo=None),
            completed_at=datetime.now(UTC).replace(tzinfo=None),
        )


class _InterruptedRouterService:
    async def process_message_by_id(
        self,
        assistant_message_id: str,
        *,
        graph,
        thread_id: str,
        reuse_thread: bool,
        on_event,
    ) -> MessageResponse:
        return MessageResponse(
            chat_message_id=assistant_message_id,
            role="assistant",
            content_text="",
            status="interrupted",
            response_json={
                "message_type": "deep_research",
                "thread_id": thread_id,
                "phase": "interrupted",
                "final_result": None,
            },
            error_message=None,
            attachments=[],
            created_at=datetime.now(UTC).replace(tzinfo=None),
            completed_at=datetime.now(UTC).replace(tzinfo=None),
        )


class _FailingRouterService:
    async def process_message_by_id(
        self,
        assistant_message_id: str,
        *,
        graph,
        thread_id: str,
        reuse_thread: bool,
        on_event,
    ) -> MessageResponse:
        raise RuntimeError("boom")


class DeepResearchGraphServiceTest(unittest.TestCase):
    def test_start_builds_graph_with_postgres_checkpointer_and_close_resets_state(self) -> None:
        fake_checkpointer = SimpleNamespace(setup=AsyncMock())
        fake_context = SimpleNamespace(
            __aenter__=AsyncMock(return_value=fake_checkpointer),
            __aexit__=AsyncMock(return_value=None),
        )
        built_graph = object()
        from_conn_string_mock = MagicMock(return_value=fake_context)
        fake_saver_class = SimpleNamespace(from_conn_string=from_conn_string_mock)
        build_graph_calls: list[object] = []

        def fake_build_research_graph(*, checkpointer):
            build_graph_calls.append(checkpointer)
            return built_graph

        with (
            patch(
                "backend.app.service.deep_research_graph_service.AsyncPostgresSaver",
                fake_saver_class,
            ),
            patch(
                "backend.app.service.deep_research_graph_service.build_research_graph",
                fake_build_research_graph,
            ),
        ):
            service = DeepResearchGraphService(checkpoint_dsn="postgresql://checkpoints")

            asyncio.run(service.start())
            self.assertIs(service.graph, built_graph)
            from_conn_string_mock.assert_called_once_with("postgresql://checkpoints")
            fake_context.__aenter__.assert_awaited_once()
            fake_checkpointer.setup.assert_awaited_once()
            self.assertEqual([fake_checkpointer], build_graph_calls)

            asyncio.run(service.start())
            from_conn_string_mock.assert_called_once()

            asyncio.run(service.close())
            fake_context.__aexit__.assert_awaited_once_with(None, None, None)
            with self.assertRaisesRegex(RuntimeError, "not started"):
                _ = service.graph


class DeepResearchServiceTest(unittest.TestCase):
    def _build_session_factory(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ensure_auth_chat_schema(engine)
        return sessionmaker(bind=engine, future=True, expire_on_commit=False)

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
                title="Deep research session",
                compact_context=compact_context,
                created_at=datetime(2026, 4, 1, 9, 0, 0),
                updated_at=datetime(2026, 4, 1, 9, 0, 0),
            )
        )

    def _write_attachment(self, root: Path, rel_path: str, content: bytes) -> None:
        full_path = root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    def test_process_message_by_id_reuses_chat_history_and_fallback_image_for_report(self) -> None:
        session_factory = self._build_session_factory()
        service = DeepResearchService()
        fake_graph = _FakeGraph(
            [
                ("updates", {"planner": {}}),
                ("custom", {"type": "section_done", "section_id": "sec-1"}),
                ("updates", {"synthesizer": {"full_report": "深研最终报告"}}),
                ("updates", {"final_check": {"final_result": {"status": "ok"}}}),
            ]
        )
        emitted_events: list[tuple[str, dict[str, object]]] = []

        async def on_event(name: str, payload: dict[str, object]) -> None:
            emitted_events.append((name, payload))

        with tempfile.TemporaryDirectory() as tmp_dir:
            attachment_root = Path(tmp_dir)
            self._write_attachment(
                attachment_root,
                "images/reference.png",
                b"reference-image",
            )

            with session_factory() as db:
                self._seed_user_and_session(
                    db,
                )
                db.add_all(
                    [
                        ChatMessage(
                            chat_message_id="user-image",
                            chat_session_id="session-1",
                            role="user",
                            content_text="上一轮上传的秀场图",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 1, 0),
                        ),
                        ChatMessage(
                            chat_message_id="assistant-prior",
                            chat_session_id="session-1",
                            role="assistant",
                            content_text="上一轮结论",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 2, 0),
                            completed_at=datetime(2026, 4, 1, 9, 2, 30),
                        ),
                        ChatMessage(
                            chat_message_id="user-current",
                            chat_session_id="session-1",
                            role="user",
                            content_text="继续做巴黎秀场深度研究",
                            status="done",
                            created_at=datetime(2026, 4, 1, 9, 5, 0),
                        ),
                        ChatMessage(
                            chat_message_id="assistant-current",
                            chat_session_id="session-1",
                            role="assistant",
                            content_text="",
                            status="queued",
                            reply_to_message_id="user-current",
                            created_at=datetime(2026, 4, 1, 9, 5, 1),
                        ),
                    ]
                )
                db.add(
                    ChatAttachment(
                        chat_attachment_id="attachment-reference",
                        chat_message_id="user-image",
                        attachment_type="image",
                        mime_type="image/png",
                        original_filename="reference.png",
                        storage_rel_path="images/reference.png",
                        size_bytes=len(b"reference-image"),
                    )
                )
                db.commit()

            with (
                patch("backend.app.service.deep_research_service.SessionLocal", session_factory),
                patch(
                    "backend.app.service.deep_research_service.auth_settings.CHAT_ATTACHMENT_ROOT",
                    tmp_dir,
                ),
            ):
                result = asyncio.run(
                    service.process_message_by_id(
                        "assistant-current",
                        graph=fake_graph,
                        thread_id="thread-42",
                        reuse_thread=False,
                        on_event=on_event,
                    )
                )

        self.assertEqual("done", result.status)
        self.assertEqual("深研最终报告", result.content_text)
        self.assertEqual(
            [
                ("progress", {"node": "planner", "status": "done"}),
                ("section_done", {"section_id": "sec-1"}),
                ("progress", {"node": "synthesizer", "status": "done"}),
                ("progress", {"node": "final_check", "status": "done"}),
                (
                    "report",
                    {
                        "content": "深研最终报告",
                        "final_result": {"status": "ok"},
                    },
                ),
            ],
            emitted_events,
        )
        self.assertEqual(1, len(fake_graph.calls))
        graph_call = fake_graph.calls[0]
        self.assertEqual(
            {"configurable": {"thread_id": "thread-42"}},
            graph_call["config"],
        )
        self.assertEqual(["updates", "custom"], graph_call["stream_mode"])

        messages = graph_call["input_state"]["messages"]
        self.assertEqual(3, len(messages))
        self.assertEqual(3, len(messages))
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertEqual("上一轮上传的秀场图", messages[0].content)
        self.assertIsInstance(messages[1], AIMessage)
        self.assertEqual("上一轮结论", messages[1].content)
        self.assertIsInstance(messages[2], HumanMessage)
        self.assertEqual("继续做巴黎秀场深度研究", messages[2].content)

        object_context = graph_call["input_state"]["object_context"]
        self.assertIsInstance(object_context, str)
        self.assertTrue(object_context.startswith("data:image/png;base64,"))
        encoded_payload = object_context.split(",", maxsplit=1)[1]
        self.assertEqual(b"reference-image", base64.b64decode(encoded_payload))

        with session_factory() as verify_db:
            assistant_message = verify_db.get(ChatMessage, "assistant-current")
            self.assertIsNotNone(assistant_message)
            self.assertEqual("done", assistant_message.status)
            self.assertEqual("深研最终报告", assistant_message.content_text)
            self.assertEqual(
                {
                    "message_type": "deep_research",
                    "thread_id": "thread-42",
                    "phase": "final_report",
                    "final_result": {"status": "ok"},
                },
                assistant_message.response_json,
            )

    def test_process_message_by_id_reuses_existing_thread_for_clarification(self) -> None:
        session_factory = self._build_session_factory()
        service = DeepResearchService()
        fake_graph = _FakeGraph(
            [
                (
                    "updates",
                    {
                        "clarify": {
                            "need_clarification": True,
                            "clarification_question": "你想聚焦哪个品牌？",
                        }
                    },
                )
            ]
        )
        emitted_events: list[tuple[str, dict[str, object]]] = []

        async def on_event(name: str, payload: dict[str, object]) -> None:
            emitted_events.append((name, payload))

        with session_factory() as db:
            self._seed_user_and_session(db)
            db.add_all(
                [
                    ChatMessage(
                        chat_message_id="user-current",
                        chat_session_id="session-1",
                        role="user",
                        content_text="继续上次研究",
                        status="done",
                        created_at=datetime(2026, 4, 1, 9, 5, 0),
                    ),
                    ChatMessage(
                        chat_message_id="assistant-current",
                        chat_session_id="session-1",
                        role="assistant",
                        content_text="",
                        status="queued",
                        reply_to_message_id="user-current",
                        created_at=datetime(2026, 4, 1, 9, 5, 1),
                    ),
                ]
            )
            db.commit()

        with patch("backend.app.service.deep_research_service.SessionLocal", session_factory):
            result = asyncio.run(
                service.process_message_by_id(
                    "assistant-current",
                    graph=fake_graph,
                    thread_id="thread-existing",
                    reuse_thread=True,
                    on_event=on_event,
                )
            )

        self.assertEqual("done", result.status)
        self.assertEqual("你想聚焦哪个品牌？", result.content_text)
        self.assertEqual(
            [
                ("progress", {"node": "clarify", "status": "done"}),
                ("clarification", {"question": "你想聚焦哪个品牌？"}),
            ],
            emitted_events,
        )
        self.assertEqual(1, len(fake_graph.calls))
        graph_call = fake_graph.calls[0]
        messages = graph_call["input_state"]["messages"]
        self.assertEqual(1, len(messages))
        self.assertIsInstance(messages[0], HumanMessage)
        self.assertEqual("继续上次研究", messages[0].content)
        self.assertIsNone(graph_call["input_state"]["object_context"])
        self.assertEqual(
            {"configurable": {"thread_id": "thread-existing"}},
            graph_call["config"],
        )

        with session_factory() as verify_db:
            assistant_message = verify_db.get(ChatMessage, "assistant-current")
            self.assertIsNotNone(assistant_message)
            self.assertEqual("done", assistant_message.status)
            self.assertEqual("你想聚焦哪个品牌？", assistant_message.content_text)
            self.assertEqual(
                {
                    "message_type": "deep_research",
                    "thread_id": "thread-existing",
                    "phase": "clarification",
                    "final_result": None,
                },
                assistant_message.response_json,
            )

    def test_process_message_by_id_marks_interrupt_when_graph_is_cancelled(self) -> None:
        session_factory = self._build_session_factory()
        service = DeepResearchService()

        class _InterruptingGraph:
            async def astream(self, input_state, *, config, stream_mode):
                yield ("updates", {"planner": {}})
                raise asyncio.CancelledError()

        with session_factory() as db:
            self._seed_user_and_session(db)
            db.add_all(
                [
                    ChatMessage(
                        chat_message_id="user-current",
                        chat_session_id="session-1",
                        role="user",
                        content_text="继续研究",
                        status="done",
                        created_at=datetime(2026, 4, 1, 9, 5, 0),
                    ),
                    ChatMessage(
                        chat_message_id="assistant-current",
                        chat_session_id="session-1",
                        role="assistant",
                        content_text="",
                        status="queued",
                        reply_to_message_id="user-current",
                        created_at=datetime(2026, 4, 1, 9, 5, 1),
                    ),
                ]
            )
            db.commit()

        with patch("backend.app.service.deep_research_service.SessionLocal", session_factory):
            result = asyncio.run(
                service.process_message_by_id(
                    "assistant-current",
                    graph=_InterruptingGraph(),
                    thread_id="thread-interrupted",
                    reuse_thread=False,
                )
            )

        self.assertEqual("interrupted", result.status)
        self.assertEqual("", result.content_text)
        self.assertEqual("interrupted", result.response_json["phase"])
        self.assertEqual("thread-interrupted", result.response_json["thread_id"])

        with session_factory() as verify_db:
            assistant_message = verify_db.get(ChatMessage, "assistant-current")
            self.assertIsNotNone(assistant_message)
            assert assistant_message is not None
            self.assertEqual("interrupted", assistant_message.status)
            self.assertEqual(
                {
                    "message_type": "deep_research",
                    "thread_id": "thread-interrupted",
                    "phase": "interrupted",
                },
                assistant_message.response_json,
            )
            self.assertIsNone(assistant_message.error_message)
            self.assertIsNotNone(assistant_message.completed_at)


class DeepResearchRouterTest(unittest.TestCase):
    def _build_session_factory(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ensure_auth_chat_schema(engine)
        return sessionmaker(bind=engine, future=True)

    def _seed_user(self, db: Session) -> User:
        user = User(
            user_id="user-1",
            login_name="stylist",
            display_name="Stylist",
            email="stylist@example.com",
            password_hash="hash",
            auth_source="local",
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        db.commit()
        return user

    def _build_app(
        self,
        session_factory,
        *,
        current_user_id: str = "user-1",
        deep_research_service=None,
    ) -> FastAPI:
        app = FastAPI()
        app.include_router(deep_research_router)
        app.state.deep_research_graph_service = SimpleNamespace(graph="graph-instance")

        def override_get_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id=current_user_id
        )
        if deep_research_service is not None:
            app.dependency_overrides[get_deep_research_service] = (
                lambda: deep_research_service
            )
        return app

    def test_stream_endpoint_prefixes_new_session_and_forwards_service_events(self) -> None:
        session_factory = self._build_session_factory()
        fake_service = _CapturingRouterService()

        with session_factory() as db:
            self._seed_user(db)

        app = self._build_app(session_factory, deep_research_service=fake_service)

        with (
            patch(
                "backend.app.router.deep_research_router.uuid4",
                return_value="thread-new",
            ),
            TestClient(app) as client,
        ):
            response = client.post(
                "/deep-research/messages/stream",
                data={"content_text": "FW26 runway outlook"},
            )

        self.assertEqual(200, response.status_code)
        events = _parse_sse_events(response.text)
        self.assertEqual(
            ["message_start", "progress", "report", "message_complete"],
            [event_name for event_name, _ in events],
        )
        start_payload = events[0][1]
        self.assertEqual(
            "Deep Research · FW26 runway outlook",
            start_payload["session_title"],
        )
        self.assertEqual(
            {
                "message_type": "deep_research",
                "thread_id": "thread-new",
                "phase": "running",
            },
            start_payload["assistant_message"]["response_json"],
        )
        report_payload = events[2][1]
        self.assertEqual("路由烟雾测试报告", report_payload["content"])
        self.assertEqual({"status": "ok"}, report_payload["final_result"])

        self.assertEqual(1, len(fake_service.calls))
        self.assertEqual(
            {
                "assistant_message_id": start_payload["assistant_message"]["chat_message_id"],
                "graph": "graph-instance",
                "thread_id": "thread-new",
                "reuse_thread": False,
            },
            fake_service.calls[0],
        )

    def test_stream_endpoint_emits_clarification_event(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)

        app = self._build_app(
            session_factory,
            deep_research_service=_ClarifyingRouterService(),
        )

        with (
            patch(
                "backend.app.router.deep_research_router.uuid4",
                return_value="thread-clarify",
            ),
            TestClient(app) as client,
        ):
            response = client.post(
                "/deep-research/messages/stream",
                data={"content_text": "请帮我继续深度研究"},
            )

        self.assertEqual(200, response.status_code)
        events = _parse_sse_events(response.text)
        self.assertEqual(
            ["message_start", "clarification", "message_complete"],
            [event_name for event_name, _ in events],
        )
        clarification_payload = events[1][1]
        self.assertEqual("请确认要聚焦的品牌", clarification_payload["question"])
        final_payload = events[2][1]
        self.assertEqual("clarification", final_payload["response_json"]["phase"])
        self.assertEqual("thread-clarify", final_payload["response_json"]["thread_id"])

    def test_stream_endpoint_emits_message_interrupted_for_interrupted_service(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)

        app = self._build_app(
            session_factory,
            deep_research_service=_InterruptedRouterService(),
        )

        with TestClient(app) as client:
            response = client.post(
                "/deep-research/messages/stream",
                data={"content_text": "中断用例"},
            )

        self.assertEqual(200, response.status_code)
        events = _parse_sse_events(response.text)
        self.assertEqual(
            ["message_start", "message_interrupted"],
            [event_name for event_name, _ in events],
        )
        interrupted_payload = events[1][1]
        self.assertEqual("interrupted", interrupted_payload["status"])
        self.assertEqual("interrupted", interrupted_payload["response_json"]["phase"])

    def test_stream_endpoint_emits_message_error_when_service_raises(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)

        app = self._build_app(
            session_factory,
            deep_research_service=_FailingRouterService(),
        )

        with TestClient(app) as client:
            response = client.post(
                "/deep-research/messages/stream",
                data={"content_text": "失败用例"},
            )

        self.assertEqual(200, response.status_code)
        events = _parse_sse_events(response.text)
        self.assertEqual(
            ["message_start", "message_error"],
            [event_name for event_name, _ in events],
        )
        self.assertEqual(
            {"detail": "RuntimeError: boom"},
            events[1][1],
        )

    def test_finalize_interrupted_message_marks_deep_research_phase_interrupted(
        self,
    ) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)
            db.add(
                ChatSession(
                    chat_session_id="session-interrupt",
                    user_id="user-1",
                    title="Deep Research",
                    created_at=datetime(2026, 4, 1, 11, 0, 0),
                    updated_at=datetime(2026, 4, 1, 11, 0, 0),
                )
            )
            db.add(
                ChatMessage(
                    chat_message_id="assistant-interrupt",
                    chat_session_id="session-interrupt",
                    role="assistant",
                    content_text="",
                    status="running",
                    response_json={
                        "message_type": "deep_research",
                        "thread_id": "thread-interrupt",
                        "phase": "running",
                    },
                    created_at=datetime(2026, 4, 1, 11, 0, 1),
                )
            )
            db.commit()

        with patch("backend.app.service.chat_interrupt_service.SessionLocal", session_factory):
            result = finalize_interrupted_message(
                "assistant-interrupt",
                default_message_type="deep_research",
            )

        self.assertEqual("interrupted", result.status)
        self.assertIsNone(result.error_message)
        self.assertEqual(
            {
                "message_type": "deep_research",
                "thread_id": "thread-interrupt",
                "phase": "interrupted",
            },
            result.response_json,
        )

    def test_stream_endpoint_rejects_reused_thread_without_session_id(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)

        app = self._build_app(session_factory)

        with TestClient(app) as client:
            response = client.post(
                "/deep-research/messages/stream",
                data={"content_text": "继续研究", "thread_id": "thread-existing"},
            )

        self.assertEqual(422, response.status_code)
        self.assertEqual(
            {"detail": "chat_session_id is required when reusing a deep research thread"},
            response.json(),
        )

    def test_stream_endpoint_rejects_foreign_session_access(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)
            db.add(
                User(
                    user_id="user-2",
                    login_name="other",
                    display_name="Other",
                    email="other@example.com",
                    password_hash="hash",
                    auth_source="local",
                    is_active=True,
                    is_admin=False,
                )
            )
            db.add(
                ChatSession(
                    chat_session_id="foreign-session",
                    user_id="user-2",
                    title="Foreign",
                    created_at=datetime(2026, 4, 1, 10, 0, 0),
                    updated_at=datetime(2026, 4, 1, 10, 0, 0),
                )
            )
            db.commit()

        app = self._build_app(session_factory)

        with TestClient(app) as client:
            response = client.post(
                "/deep-research/messages/stream",
                data={
                    "chat_session_id": "foreign-session",
                    "content_text": "继续研究",
                    "thread_id": "thread-existing",
                },
            )

        self.assertEqual(403, response.status_code)
        self.assertEqual({"detail": "Access denied to this chat session"}, response.json())

    def test_stream_endpoint_rejects_unknown_thread_in_owned_session(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db, )
            db.add(
                ChatSession(
                    chat_session_id="session-existing",
                    user_id="user-1",
                    title="Existing session",
                    created_at=datetime(2026, 4, 1, 10, 0, 0),
                    updated_at=datetime(2026, 4, 1, 10, 0, 0),
                )
            )
            db.commit()

        app = self._build_app(session_factory)

        with TestClient(app) as client:
            response = client.post(
                "/deep-research/messages/stream",
                data={
                    "chat_session_id": "session-existing",
                    "content_text": "继续研究",
                    "thread_id": "thread-missing",
                },
            )

        self.assertEqual(404, response.status_code)
        self.assertEqual(
            {"detail": "Deep research thread not found in this chat session"},
            response.json(),
        )

    def test_stream_endpoint_rejects_stale_thread_reuse_without_pending_clarification(self) -> None:
        session_factory = self._build_session_factory()

        with session_factory() as db:
            self._seed_user(db)
            db.add(
                ChatSession(
                    chat_session_id="session-existing",
                    user_id="user-1",
                    title="Existing session",
                    created_at=datetime(2026, 4, 1, 10, 0, 0),
                    updated_at=datetime(2026, 4, 1, 10, 3, 0),
                )
            )
            db.add_all(
                [
                    ChatMessage(
                        chat_message_id="assistant-clarify",
                        chat_session_id="session-existing",
                        role="assistant",
                        content_text="请确认聚焦品牌",
                        status="done",
                        response_json={
                            "message_type": "deep_research",
                            "thread_id": "thread-existing",
                            "phase": "clarification",
                        },
                        created_at=datetime(2026, 4, 1, 10, 1, 0),
                        completed_at=datetime(2026, 4, 1, 10, 1, 30),
                    ),
                    ChatMessage(
                        chat_message_id="assistant-final",
                        chat_session_id="session-existing",
                        role="assistant",
                        content_text="最终报告",
                        status="done",
                        response_json={
                            "message_type": "deep_research",
                            "thread_id": "thread-existing",
                            "phase": "final_report",
                        },
                        created_at=datetime(2026, 4, 1, 10, 2, 0),
                        completed_at=datetime(2026, 4, 1, 10, 2, 30),
                    ),
                ]
            )
            db.commit()

        app = self._build_app(session_factory)

        with TestClient(app) as client:
            response = client.post(
                "/deep-research/messages/stream",
                data={
                    "chat_session_id": "session-existing",
                    "content_text": "继续研究",
                    "thread_id": "thread-existing",
                },
            )

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            {"detail": "Deep research thread is not awaiting clarification in this chat session"},
            response.json(),
        )
