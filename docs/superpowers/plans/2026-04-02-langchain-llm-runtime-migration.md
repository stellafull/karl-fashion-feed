# LangChain LLM Runtime Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct OpenAI SDK usage in backend runtime services with LangChain `create_agent` while preserving fail-fast business validation, Redis-backed LLM lease limiting, and the current RAG service interface.

**Architecture:** Introduce a `Configuration`-style LLM config plus a thin `ChatOpenAI` factory, then migrate each runtime service to a service-local `create_agent` call. Structured-output services will use `create_agent(..., tools=[], response_format=Schema)` and extract `result["structured_response"]`; RAG will use one tool-calling agent for retrieval and one no-tool agent for final synthesis while keeping `RagAnswerService.answer(...)` and `answer_stream(...)` stable.

**Tech Stack:** Python 3.12, LangChain 1.2.12, LangGraph 1.1.2, langchain-openai 1.1.12, SQLAlchemy, unittest, uv

---

## File Map

### Config and shared runtime files

- Modify: `backend/app/config/llm_config.py`
  - Replace `ModelConfig` dataclasses with a `Configuration` model that reads env plus optional runnable config.
- Create: `backend/app/service/langchain_model_factory.py`
  - Build retry-wrapped `ChatOpenAI` models for the story-summarization profile and RAG profile.

### Structured-output pipeline services

- Modify: `backend/app/service/event_frame_extraction_service.py`
  - Replace `AsyncOpenAI` with a service-local LangChain agent and keep DB attempt semantics unchanged.
- Modify: `backend/app/service/story_clustering_service.py`
  - Replace `AsyncOpenAI` with a service-local LangChain agent and keep full-coverage assertions.
- Modify: `backend/app/service/story_facet_assignment_service.py`
  - Replace brittle manual JSON parsing with `structured_response` extraction.
- Modify: `backend/app/service/digest_packaging_service.py`
  - Replace brittle manual JSON parsing with `structured_response` extraction.
- Modify: `backend/app/service/digest_report_writing_service.py`
  - Replace `AsyncOpenAI` with a service-local LangChain agent.
- Modify: `backend/app/service/digest_generation_service.py`
  - Propagate `Configuration` instead of `AsyncOpenAI`, and keep shared `LlmRateLimiter` wiring.

### RAG files

- Modify: `backend/app/service/RAG/rag_tools.py`
  - Add LangChain tool adapters that reuse existing retrieval and web-search methods while collecting domain results.
- Modify: `backend/app/service/RAG/rag_answer_service.py`
  - Replace the handwritten tool loop with one retrieval agent plus one synthesis agent.

### Tests

- Create: `backend/tests/test_event_frame_extraction_service.py`
  - Cover structured-output success and DB-attempt semantics.
- Modify: `backend/tests/test_story_clustering_service.py`
  - Replace fake OpenAI clients with fake LangChain agents.
- Modify: `backend/tests/test_story_facet_assignment_service.py`
  - Replace fake OpenAI clients with fake LangChain agents and keep batching assertions.
- Modify: `backend/tests/test_digest_packaging_service.py`
  - Replace fake OpenAI clients with fake LangChain agents and keep overlap assertions.
- Modify: `backend/tests/test_digest_report_writing_service.py`
  - Replace fake OpenAI clients with fake LangChain agents.
- Modify: `backend/tests/test_digest_generation_service.py`
  - Add dependency-propagation assertions for `Configuration` and `LlmRateLimiter`.
- Modify: `backend/tests/test_story_digest_runtime_integration.py`
  - Swap fake OpenAI clients for fake structured-output agents across the whole story-to-digest flow.
- Create: `backend/tests/test_rag_answer_service.py`
  - Cover tool-calling result collection, synthesis, streaming, and tool exposure.
- Modify: `backend/tests/test_llm_contracts.py`
  - Add configuration and LangChain runtime contract coverage.

## Task 1: Add Configuration and ChatOpenAI Factory

**Files:**
- Modify: `backend/app/config/llm_config.py`
- Create: `backend/app/service/langchain_model_factory.py`
- Modify: `backend/tests/test_llm_contracts.py`

- [ ] **Step 1: Sync the backend environment before touching runtime code**

Run:

```bash
cd /home/czy/karl-fashion-feed/backend
uv sync
```

Expected: `langchain-openai` is installed into `./.venv` and `uv` exits with code `0`.

- [ ] **Step 2: Write the failing configuration contract test**

Add this test block to `backend/tests/test_llm_contracts.py`:

```python
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.config.llm_config import Configuration


class LangChainConfigurationTest(unittest.TestCase):
    def test_from_runnable_config_reads_env_and_configurable_values(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "API_KEY": "env-key",
                "STORY_SUMMARIZATION_MODEL": "kimi-k2.5",
            },
            clear=False,
        ):
            config = Configuration.from_runnable_config(
                {
                    "configurable": {
                        "rag_model": "openai:gpt-4o-mini",
                        "max_react_tool_calls": 5,
                    }
                }
            )

        self.assertEqual("https://dashscope.aliyuncs.com/compatible-mode/v1", config.base_url)
        self.assertEqual("env-key", config.api_key)
        self.assertEqual("kimi-k2.5", config.story_summarization_model)
        self.assertEqual("openai:gpt-4o-mini", config.rag_model)
        self.assertEqual(5, config.max_react_tool_calls)
        self.assertEqual(3, config.max_structured_output_retries)
```

- [ ] **Step 3: Run the new configuration test and verify it fails on missing `Configuration`**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_llm_contracts.LangChainConfigurationTest.test_from_runnable_config_reads_env_and_configurable_values
```

Expected: `ImportError` or `AttributeError` because `Configuration` does not exist yet.

- [ ] **Step 4: Replace `ModelConfig` with a `Configuration` model**

Update `backend/app/config/llm_config.py` so the core shape looks like this:

```python
from __future__ import annotations

import os
from typing import Any

from dotenv import find_dotenv, load_dotenv
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

_ = load_dotenv(find_dotenv())

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class Configuration(BaseModel):
    """Runtime configuration for LangChain-backed LLM services."""

    base_url: str = Field(default=DEFAULT_BASE_URL)
    api_key: str | None = Field(default=None)
    max_structured_output_retries: int = Field(default=3)
    story_summarization_model: str = Field(default="kimi-k2.5")
    story_summarization_model_max_tokens: int = Field(default=8192)
    story_summarization_temperature: float = Field(default=0.0)
    story_summarization_timeout_seconds: int = Field(default=600)
    rag_model: str = Field(default="kimi-k2.5")
    rag_model_max_tokens: int = Field(default=4096)
    rag_temperature: float = Field(default=0.2)
    rag_timeout_seconds: int = Field(default=120)
    max_react_tool_calls: int = Field(default=3)

    @classmethod
    def from_runnable_config(
        cls,
        config: RunnableConfig | None = None,
    ) -> "Configuration":
        configurable = config.get("configurable", {}) if config else {}
        values: dict[str, Any] = {
            "base_url": os.getenv("BASE_URL") or os.getenv("OPENAI_BASE_URL") or configurable.get("base_url"),
            "api_key": os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or configurable.get("api_key"),
            "max_structured_output_retries": os.getenv("MAX_STRUCTURED_OUTPUT_RETRIES")
            or configurable.get("max_structured_output_retries"),
            "story_summarization_model": os.getenv("STORY_SUMMARIZATION_MODEL")
            or configurable.get("story_summarization_model"),
            "story_summarization_model_max_tokens": os.getenv("STORY_SUMMARIZATION_MODEL_MAX_TOKENS")
            or configurable.get("story_summarization_model_max_tokens"),
            "story_summarization_temperature": os.getenv("STORY_SUMMARIZATION_TEMPERATURE")
            or configurable.get("story_summarization_temperature"),
            "story_summarization_timeout_seconds": os.getenv("STORY_SUMMARIZATION_TIMEOUT_SECONDS")
            or configurable.get("story_summarization_timeout_seconds"),
            "rag_model": os.getenv("RAG_MODEL") or os.getenv("RAG_CHAT_MODEL") or configurable.get("rag_model"),
            "rag_model_max_tokens": os.getenv("RAG_MODEL_MAX_TOKENS")
            or configurable.get("rag_model_max_tokens"),
            "rag_temperature": os.getenv("RAG_TEMPERATURE") or configurable.get("rag_temperature"),
            "rag_timeout_seconds": os.getenv("RAG_TIMEOUT_SECONDS")
            or configurable.get("rag_timeout_seconds"),
            "max_react_tool_calls": os.getenv("MAX_REACT_TOOL_CALLS")
            or configurable.get("max_react_tool_calls"),
        }
        return cls(**{key: value for key, value in values.items() if value is not None})
```

- [ ] **Step 5: Add the failing model-factory test**

Append this to `backend/tests/test_llm_contracts.py`:

```python
from unittest.mock import patch

from backend.app.config.llm_config import Configuration
from backend.app.service.langchain_model_factory import build_story_model


class LangChainModelFactoryTest(unittest.TestCase):
    @patch("backend.app.service.langchain_model_factory.ChatOpenAI")
    def test_build_story_model_uses_base_url_and_retry(self, mock_chat_openai) -> None:
        runnable = mock_chat_openai.return_value
        runnable.with_retry.return_value = "retry-wrapped-model"

        result = build_story_model(
            Configuration(
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="env-key",
                story_summarization_model="kimi-k2.5",
                story_summarization_model_max_tokens=8192,
                story_summarization_temperature=0.0,
                story_summarization_timeout_seconds=600,
                max_structured_output_retries=3,
            )
        )

        self.assertEqual("retry-wrapped-model", result)
        mock_chat_openai.assert_called_once_with(
            model="kimi-k2.5",
            api_key="env-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.0,
            max_completion_tokens=8192,
            timeout=600,
            max_retries=0,
            use_responses_api=True,
        )
        runnable.with_retry.assert_called_once_with(stop_after_attempt=3)
```

- [ ] **Step 6: Run the new model-factory test and verify it fails on missing module**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_llm_contracts.LangChainModelFactoryTest.test_build_story_model_uses_base_url_and_retry
```

Expected: `ModuleNotFoundError` for `backend.app.service.langchain_model_factory`.

- [ ] **Step 7: Implement the thin `ChatOpenAI` factory**

Create `backend/app/service/langchain_model_factory.py` with this core code:

```python
from __future__ import annotations

from langchain_openai import ChatOpenAI

from backend.app.config.llm_config import Configuration


def _require_api_key(configuration: Configuration, *, service_name: str) -> str:
    if not configuration.api_key:
        raise RuntimeError(f"{service_name} requires configured API key")
    return configuration.api_key


def build_story_model(configuration: Configuration) -> ChatOpenAI:
    model = ChatOpenAI(
        model=configuration.story_summarization_model,
        api_key=_require_api_key(configuration, service_name="story runtime"),
        base_url=configuration.base_url,
        temperature=configuration.story_summarization_temperature,
        max_completion_tokens=configuration.story_summarization_model_max_tokens,
        timeout=configuration.story_summarization_timeout_seconds,
        max_retries=0,
        use_responses_api=True,
    )
    return model.with_retry(stop_after_attempt=configuration.max_structured_output_retries)


def build_rag_model(configuration: Configuration) -> ChatOpenAI:
    model = ChatOpenAI(
        model=configuration.rag_model,
        api_key=_require_api_key(configuration, service_name="rag runtime"),
        base_url=configuration.base_url,
        temperature=configuration.rag_temperature,
        max_completion_tokens=configuration.rag_model_max_tokens,
        timeout=configuration.rag_timeout_seconds,
        max_retries=0,
        use_responses_api=True,
    )
    return model.with_retry(stop_after_attempt=configuration.max_structured_output_retries)
```

- [ ] **Step 8: Run the configuration and factory tests and verify they pass**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_llm_contracts.LangChainConfigurationTest \
  backend.tests.test_llm_contracts.LangChainModelFactoryTest
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 9: Commit the configuration and factory work**

Run:

```bash
cd /home/czy/karl-fashion-feed
git add backend/app/config/llm_config.py backend/app/service/langchain_model_factory.py backend/tests/test_llm_contracts.py backend/pyproject.toml backend/uv.lock
git commit -m "refactor: add langchain llm configuration"
```

## Task 2: Migrate EventFrameExtractionService to `create_agent`

**Files:**
- Modify: `backend/app/service/event_frame_extraction_service.py`
- Create: `backend/tests/test_event_frame_extraction_service.py`

- [ ] **Step 1: Write the failing service test for structured output**

Create `backend/tests/test_event_frame_extraction_service.py` with this starting test:

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Article, ArticleEventFrame, ensure_article_storage_schema
from backend.app.service.event_frame_extraction_service import EventFrameExtractionService
from backend.app.schemas.llm.event_frame_extraction import EventFrameExtractionSchema


class _FakeStructuredAgent:
    def __init__(self, schema: EventFrameExtractionSchema) -> None:
        self.calls: list[dict[str, object]] = []
        self._schema = schema

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return {"structured_response": self._schema}


def _build_fake_rate_limiter():
    return type("Limiter", (), {"lease": lambda self, *_: nullcontext()})()
```

- [ ] **Step 2: Add the DB-attempt failure test before implementation**

Append this test method:

```python
    def test_extract_frames_marks_one_failed_attempt_for_one_service_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
            ensure_article_storage_schema(engine)
            session = sessionmaker(bind=engine, future=True)()
            self.addCleanup(session.close)
            markdown_root = Path(tmpdir)
            (markdown_root / "2026/03/30").mkdir(parents=True, exist_ok=True)
            (markdown_root / "2026/03/30/article-1.md").write_text("# Article\n\nAcme released FW26.", encoding="utf-8")
            article = Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://example.com/article-1",
                original_url="https://example.com/article-1",
                title_raw="Article 1",
                summary_raw="Summary 1",
                markdown_rel_path="2026/03/30/article-1.md",
                parse_status="done",
                event_frame_status="pending",
                event_frame_attempts=0,
                ingested_at=datetime(2026, 3, 30, 1, 0, tzinfo=UTC).replace(tzinfo=None),
            )
            session.add(article)
            session.commit()

            class _FailingAgent:
                async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
                    raise RuntimeError("upstream timeout")

            service = EventFrameExtractionService(
                agent=_FailingAgent(),
                markdown_service=None,
                rate_limiter=_build_fake_rate_limiter(),
                markdown_root=markdown_root,
            )

            frames = asyncio.run(service.extract_frames(session, article))

            session.refresh(article)
            self.assertEqual((), frames)
            self.assertEqual("failed", article.event_frame_status)
            self.assertEqual(1, article.event_frame_attempts)
```

- [ ] **Step 3: Run the new tests and verify they fail on unsupported constructor args**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_event_frame_extraction_service
```

Expected: `TypeError` because `EventFrameExtractionService` does not accept `agent` yet.

- [ ] **Step 4: Add agent injection and `structured_response` extraction**

Update `backend/app/service/event_frame_extraction_service.py` with this core shape:

```python
from langchain.agents import create_agent

from backend.app.config.llm_config import Configuration
from backend.app.service.langchain_model_factory import build_story_model


class EventFrameExtractionService:
    def __init__(
        self,
        *,
        agent: object | None = None,
        configuration: Configuration | None = None,
        markdown_service: ArticleMarkdownService | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        markdown_root: Path | None = None,
    ) -> None:
        self._agent = agent
        self._configuration = configuration or Configuration()
        self._markdown_service = markdown_service or ArticleMarkdownService(markdown_root)
        self._rate_limiter = rate_limiter or LlmRateLimiter()

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_story_model(self._configuration),
                tools=[],
                system_prompt=build_event_frame_extraction_prompt(),
                response_format=EventFrameExtractionSchema,
            )
        return self._agent

    async def _infer_frames(self, article: Article) -> EventFrameExtractionSchema:
        markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
        with self._rate_limiter.lease("event_frame_extraction"):
            result = await self._get_agent().ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self._build_user_message(article=article, markdown=markdown),
                        }
                    ]
                }
            )
        return result["structured_response"]
```

- [ ] **Step 5: Run the new event-frame tests and verify they pass**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_event_frame_extraction_service
```

Expected: `OK`.

- [ ] **Step 6: Commit the event-frame migration**

Run:

```bash
cd /home/czy/karl-fashion-feed
git add backend/app/service/event_frame_extraction_service.py backend/tests/test_event_frame_extraction_service.py
git commit -m "refactor: migrate event frame extraction to langchain"
```

## Task 3: Migrate StoryClusteringService, StoryFacetAssignmentService, and DigestPackagingService

**Files:**
- Modify: `backend/app/service/story_clustering_service.py`
- Modify: `backend/app/service/story_facet_assignment_service.py`
- Modify: `backend/app/service/digest_packaging_service.py`
- Modify: `backend/tests/test_story_clustering_service.py`
- Modify: `backend/tests/test_story_facet_assignment_service.py`
- Modify: `backend/tests/test_digest_packaging_service.py`

- [ ] **Step 1: Replace one fake OpenAI seam with a fake LangChain agent seam in tests**

In `backend/tests/test_story_facet_assignment_service.py`, replace the old fake client helper with:

```python
class _FakeStructuredAgent:
    def __init__(self, responses: list[object], *, call_log: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self._call_log = call_log

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self._call_log.append(payload)
        if not self._responses:
            raise AssertionError("fake agent exhausted queued responses")
        return {"structured_response": self._responses.pop(0)}
```

Apply the same pattern to `backend/tests/test_story_clustering_service.py` and `backend/tests/test_digest_packaging_service.py`.

- [ ] **Step 2: Rewrite one existing assertion around service payload shape**

In `backend/tests/test_story_facet_assignment_service.py`, change the batching assertion to inspect `payload["messages"][0]["content"]`:

```python
        batch_story_keys = []
        for payload in call_log:
            message = payload["messages"][0]
            request_payload = json.loads(message["content"])
            batch_story_keys.append(tuple(story["story_key"] for story in request_payload["stories"]))
        self.assertEqual([("story-1",), ("story-2",)], batch_story_keys)
```

- [ ] **Step 3: Run the three test files and verify they fail on missing `agent` constructor support**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_story_clustering_service \
  backend.tests.test_story_facet_assignment_service \
  backend.tests.test_digest_packaging_service
```

Expected: `TypeError` or assertion failures because the services still expect `client=...`.

- [ ] **Step 4: Migrate StoryClusteringService to `create_agent`**

Update the service so its agent path looks like this:

```python
class StoryClusteringService:
    def __init__(
        self,
        *,
        agent: object | None = None,
        configuration: Configuration | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        artifact_recorder: LlmDebugArtifactRecorder | None = None,
        max_window_size: int = 8,
    ) -> None:
        self._agent = agent
        self._configuration = configuration or Configuration()
        self._rate_limiter = rate_limiter or LlmRateLimiter()
        self._artifact_recorder = artifact_recorder or build_llm_debug_artifact_recorder_from_env()
        self._max_window_size = max_window_size

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_story_model(self._configuration),
                tools=[],
                system_prompt=build_story_cluster_judgment_prompt(),
                response_format=StoryClusterJudgmentSchema,
            )
        return self._agent

    async def _run_story_cluster_judgment(...):
        with self._rate_limiter.lease("story_cluster_judgment"):
            result = await self._get_agent().ainvoke(
                {"messages": [{"role": "user", "content": user_message}]}
            )
        return result["structured_response"]
```

- [ ] **Step 5: Migrate StoryFacetAssignmentService to `create_agent`**

Update the service so its agent path looks like this:

```python
class StoryFacetAssignmentService:
    def __init__(
        self,
        *,
        agent: object | None = None,
        configuration: Configuration | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        artifact_recorder: LlmDebugArtifactRecorder | None = None,
        max_stories_per_request: int = 50,
    ) -> None:
        self._agent = agent
        self._configuration = configuration or Configuration()
        self._rate_limiter = rate_limiter or LlmRateLimiter()
        self._artifact_recorder = artifact_recorder or build_llm_debug_artifact_recorder_from_env()
        self._max_stories_per_request = max_stories_per_request

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_story_model(self._configuration),
                tools=[],
                system_prompt=build_facet_assignment_prompt(),
                response_format=FacetAssignmentSchema,
            )
        return self._agent
```

- [ ] **Step 6: Migrate DigestPackagingService to `create_agent`**

Update the service so its agent path looks like this:

```python
class DigestPackagingService:
    def __init__(
        self,
        *,
        agent: object | None = None,
        configuration: Configuration | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        artifact_recorder: LlmDebugArtifactRecorder | None = None,
    ) -> None:
        self._agent = agent
        self._configuration = configuration or Configuration()
        self._rate_limiter = rate_limiter or LlmRateLimiter()
        self._artifact_recorder = artifact_recorder or build_llm_debug_artifact_recorder_from_env()

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_story_model(self._configuration),
                tools=[],
                system_prompt=build_digest_packaging_prompt(),
                response_format=DigestPackagingSchema,
            )
        return self._agent
```

- [ ] **Step 7: Run the three service test files and verify they pass**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_story_clustering_service \
  backend.tests.test_story_facet_assignment_service \
  backend.tests.test_digest_packaging_service
```

Expected: all three files report `OK`.

- [ ] **Step 8: Commit the story and packaging migration**

Run:

```bash
cd /home/czy/karl-fashion-feed
git add \
  backend/app/service/story_clustering_service.py \
  backend/app/service/story_facet_assignment_service.py \
  backend/app/service/digest_packaging_service.py \
  backend/tests/test_story_clustering_service.py \
  backend/tests/test_story_facet_assignment_service.py \
  backend/tests/test_digest_packaging_service.py
git commit -m "refactor: migrate story packaging services to langchain"
```

## Task 4: Migrate DigestReportWritingService and DigestGenerationService

**Files:**
- Modify: `backend/app/service/digest_report_writing_service.py`
- Modify: `backend/app/service/digest_generation_service.py`
- Modify: `backend/tests/test_digest_report_writing_service.py`
- Modify: `backend/tests/test_digest_generation_service.py`
- Modify: `backend/tests/test_story_digest_runtime_integration.py`

- [ ] **Step 1: Add a failing dependency-propagation test for DigestGenerationService**

Append this test to `backend/tests/test_digest_generation_service.py`:

```python
from unittest.mock import patch, sentinel

from backend.app.config.llm_config import Configuration


    @patch("backend.app.service.digest_generation_service.DigestReportWritingService")
    @patch("backend.app.service.digest_generation_service.DigestPackagingService")
    @patch("backend.app.service.digest_generation_service.StoryFacetAssignmentService")
    def test_default_subservices_receive_shared_configuration_and_rate_limiter(
        self,
        mock_facet_assignment,
        mock_packaging,
        mock_report_writing,
    ) -> None:
        configuration = Configuration(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key="env-key")

        DigestGenerationService(configuration=configuration, rate_limiter=sentinel.rate_limiter)

        mock_facet_assignment.assert_called_once_with(
            configuration=configuration,
            rate_limiter=sentinel.rate_limiter,
        )
        mock_packaging.assert_called_once_with(
            configuration=configuration,
            rate_limiter=sentinel.rate_limiter,
        )
        mock_report_writing.assert_called_once_with(
            configuration=configuration,
            rate_limiter=sentinel.rate_limiter,
        )
```

- [ ] **Step 2: Run the digest-generation test and verify it fails on unsupported constructor args**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_generation_service.DigestGenerationServiceTest.test_default_subservices_receive_shared_configuration_and_rate_limiter
```

Expected: `TypeError` because `DigestGenerationService` does not accept `configuration` yet.

- [ ] **Step 3: Migrate DigestReportWritingService to `create_agent`**

Update the report-writing service with this core pattern:

```python
class DigestReportWritingService:
    def __init__(
        self,
        *,
        agent: object | None = None,
        configuration: Configuration | None = None,
        markdown_root: Path | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        artifact_recorder: LlmDebugArtifactRecorder | None = None,
    ) -> None:
        self._agent = agent
        self._configuration = configuration or Configuration()
        self._markdown_service = ArticleMarkdownService(markdown_root)
        self._rate_limiter = rate_limiter or LlmRateLimiter()
        self._artifact_recorder = artifact_recorder or build_llm_debug_artifact_recorder_from_env()

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_story_model(self._configuration),
                tools=[],
                system_prompt=build_digest_report_writing_prompt(),
                response_format=DigestReportWritingSchema,
            )
        return self._agent
```

- [ ] **Step 4: Propagate `Configuration` through DigestGenerationService**

Update `backend/app/service/digest_generation_service.py` so the constructor shape becomes:

```python
class DigestGenerationService:
    def __init__(
        self,
        *,
        configuration: Configuration | None = None,
        rate_limiter: LlmRateLimiter | None = None,
        facet_assignment_service: StoryFacetAssignmentService | None = None,
        packaging_service: DigestPackagingService | None = None,
        report_writing_service: DigestReportWritingService | None = None,
    ) -> None:
        shared_rate_limiter = rate_limiter or LlmRateLimiter()
        shared_configuration = configuration or Configuration()
        self._facet_assignment_service = facet_assignment_service or StoryFacetAssignmentService(
            configuration=shared_configuration,
            rate_limiter=shared_rate_limiter,
        )
        self._packaging_service = packaging_service or DigestPackagingService(
            configuration=shared_configuration,
            rate_limiter=shared_rate_limiter,
        )
        self._report_writing_service = report_writing_service or DigestReportWritingService(
            configuration=shared_configuration,
            rate_limiter=shared_rate_limiter,
        )
```

- [ ] **Step 5: Replace the integration test seam with fake agents**

In `backend/tests/test_story_digest_runtime_integration.py`, replace the old fake OpenAI helpers with fake agents that return `{"structured_response": ...}`. For example:

```python
class _FakeStructuredAgent:
    def __init__(self, response, *, call_log: list[dict[str, object]]) -> None:
        self._response = response
        self._call_log = call_log

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self._call_log.append(payload)
        return {"structured_response": self._response}
```

- [ ] **Step 6: Run digest-report, digest-generation, and integration tests and verify they pass**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_report_writing_service \
  backend.tests.test_digest_generation_service \
  backend.tests.test_story_digest_runtime_integration
```

Expected: `OK`.

- [ ] **Step 7: Commit the digest runtime migration**

Run:

```bash
cd /home/czy/karl-fashion-feed
git add \
  backend/app/service/digest_report_writing_service.py \
  backend/app/service/digest_generation_service.py \
  backend/tests/test_digest_report_writing_service.py \
  backend/tests/test_digest_generation_service.py \
  backend/tests/test_story_digest_runtime_integration.py
git commit -m "refactor: migrate digest runtime to langchain"
```

## Task 5: Replace the RAG Handwritten Loop with LangChain Agents

**Files:**
- Modify: `backend/app/service/RAG/rag_tools.py`
- Modify: `backend/app/service/RAG/rag_answer_service.py`
- Create: `backend/tests/test_rag_answer_service.py`

- [ ] **Step 1: Write the failing RAG service test for tool-result collection**

Create `backend/tests/test_rag_answer_service.py` with this starting test:

```python
from __future__ import annotations

import asyncio
import unittest

from backend.app.schemas.rag_api import RagQueryRequest, RagRequestContext
from backend.app.schemas.rag_query import QueryFilters, QueryPlan, QueryResult
from backend.app.service.RAG.rag_answer_service import RagAnswerService


class _FakeResearchAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return {"messages": [{"role": "assistant", "content": "tool loop complete"}]}


class _FakeSynthesisAgent:
    def __init__(self, answer: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._answer = answer

    async def ainvoke(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return {"messages": [{"role": "assistant", "content": self._answer}]}
```

- [ ] **Step 2: Add the failing streaming test before implementation**

Append this test method:

```python
    def test_answer_stream_emits_incremental_text_from_astream_events(self) -> None:
        deltas: list[str] = []

        class _StreamingSynthesisAgent(_FakeSynthesisAgent):
            async def astream_events(self, payload: dict[str, object], version: str = "v2"):
                self.calls.append(payload)
                yield {"event": "on_chat_model_stream", "data": {"chunk": type("Chunk", (), {"content": "你好"})()}}
                yield {"event": "on_chat_model_stream", "data": {"chunk": type("Chunk", (), {"content": "世界"})()}}

        service = RagAnswerService(
            research_agent=_FakeResearchAgent(),
            synthesis_agent=_StreamingSynthesisAgent(answer="ignored"),
        )

        async def _on_delta(text: str) -> None:
            deltas.append(text)

        response = asyncio.run(
            service.answer_stream(
                request=RagQueryRequest(query="look up Acme", filters=QueryFilters(), limit=5),
                request_context=RagRequestContext(filters=QueryFilters(), limit=5, request_images=[]),
                on_delta=_on_delta,
            )
        )

        self.assertEqual(["你好", "世界"], deltas)
        self.assertEqual("你好世界", response.answer)
```

- [ ] **Step 3: Run the new RAG tests and verify they fail on unsupported constructor args**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_rag_answer_service
```

Expected: `TypeError` because `RagAnswerService` does not accept `research_agent` and `synthesis_agent`.

- [ ] **Step 4: Add LangChain tool adapters to `rag_tools.py`**

Modify `backend/app/service/RAG/rag_tools.py` with this core adapter pattern:

```python
from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool


@dataclass
class RagToolCollector:
    rag_results: list[QueryResult] = field(default_factory=list)
    web_results: list[WebSearchResult] = field(default_factory=list)


class RagTools:
    def build_langchain_tools(self, collector: RagToolCollector) -> list[StructuredTool]:
        def _record_query_result(result: QueryResult) -> str:
            collector.rag_results.append(result)
            return result.model_dump_json(indent=2)

        async def _record_web_result(query: str) -> str:
            result = await self.search_web(query=query)
            collector.web_results.extend(result)
            return self.serialize_tool_result(result)

        return [
            StructuredTool.from_function(
                func=lambda query: _record_query_result(self.search_fashion_articles(query=query)),
                name="search_fashion_articles",
                description="Search Chinese-grounded fashion articles and return text evidence.",
            ),
            StructuredTool.from_function(
                func=lambda text_query=None, image_ref=None: _record_query_result(
                    self.search_fashion_images(text_query=text_query, image_ref=image_ref)
                ),
                name="search_fashion_images",
                description="Search fashion images either by text or by the uploaded request image.",
            ),
            StructuredTool.from_function(
                func=lambda query, image_ref=None: _record_query_result(
                    self.search_fashion_fusion(query=query, image_ref=image_ref)
                ),
                name="search_fashion_fusion",
                description="Run text+image fusion retrieval over fashion evidence packages.",
            ),
            StructuredTool.from_function(
                coroutine=_record_web_result,
                name="search_web",
                description="Search the external web for latest information when internal RAG is insufficient.",
            ),
        ]
```

- [ ] **Step 5: Replace the handwritten loop with two service-local agents**

Update `backend/app/service/RAG/rag_answer_service.py` to follow this shape:

```python
class RagAnswerService:
    def __init__(
        self,
        *,
        configuration: Configuration | None = None,
        research_agent: object | None = None,
        synthesis_agent: object | None = None,
        tools_factory: Callable[[RagRequestContext], RagTools] | None = None,
    ) -> None:
        self._configuration = configuration or Configuration()
        self._research_agent = research_agent
        self._synthesis_agent = synthesis_agent
        self._tools_factory = (
            (lambda request_context: RagTools(request_context=request_context))
            if tools_factory is None
            else tools_factory
        )

    def _get_research_agent(self, request_context: RagRequestContext, collector: RagToolCollector):
        if self._research_agent is not None:
            return self._research_agent
        tools = self._tools_factory(request_context).build_langchain_tools(collector)
        return create_agent(
            model=build_rag_model(self._configuration),
            tools=tools,
            system_prompt=RAG_TOOL_LOOP_PROMPT,
        )

    def _get_synthesis_agent(self):
        if self._synthesis_agent is None:
            self._synthesis_agent = create_agent(
                model=build_rag_model(self._configuration),
                tools=[],
                system_prompt=RAG_ANSWER_SYNTHESIS_PROMPT,
            )
        return self._synthesis_agent
```

- [ ] **Step 6: Implement synthesis extraction and streaming translation**

Inside `backend/app/service/RAG/rag_answer_service.py`, add this extraction logic:

```python
    @staticmethod
    def _extract_agent_text(result: dict[str, object]) -> str:
        messages = result.get("messages", [])
        for message in reversed(messages):
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
        raise ValueError("rag answer synthesis returned empty content")

    async def _synthesize_answer_stream(...):
        answer_parts: list[str] = []
        async for event in self._get_synthesis_agent().astream_events(
            {"messages": [{"role": "user", "content": content}]},
            version="v2",
        ):
            if event.get("event") != "on_chat_model_stream":
                continue
            chunk = event.get("data", {}).get("chunk")
            delta_text = getattr(chunk, "content", "") or ""
            if not delta_text:
                continue
            answer_parts.append(delta_text)
            await on_delta(delta_text)

        answer = "".join(answer_parts).strip()
        if not answer:
            raise ValueError("rag answer synthesis returned empty content")
        return answer
```

- [ ] **Step 7: Add tool exposure for future higher-level agents**

In `backend/app/service/RAG/rag_answer_service.py`, add this adapter:

```python
from langchain_core.tools import StructuredTool


    def as_tool(self) -> StructuredTool:
        async def _answer(query: str) -> str:
            response = await self.answer(
                request=RagQueryRequest(query=query, filters=QueryFilters(), limit=10),
                request_context=RagRequestContext(filters=QueryFilters(), limit=10, request_images=[]),
            )
            return response.answer

        return StructuredTool.from_function(
            coroutine=_answer,
            name="fashion_rag_answer",
            description="Answer a fashion-domain question with internal retrieval and web search when necessary.",
        )
```

- [ ] **Step 8: Run the RAG tests and verify they pass**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_rag_answer_service
```

Expected: `OK`.

- [ ] **Step 9: Commit the RAG migration**

Run:

```bash
cd /home/czy/karl-fashion-feed
git add \
  backend/app/service/RAG/rag_tools.py \
  backend/app/service/RAG/rag_answer_service.py \
  backend/tests/test_rag_answer_service.py
git commit -m "refactor: migrate rag answer service to langchain"
```

## Task 6: Remove direct OpenAI runtime usage and run the full verification suite

**Files:**
- Modify: `backend/tests/test_story_digest_runtime_integration.py`
- Modify: any migrated service files still importing `AsyncOpenAI`

- [ ] **Step 1: Grep for forbidden runtime patterns before cleanup**

Run:

```bash
cd /home/czy/karl-fashion-feed
rg -n "AsyncOpenAI|chat\\.completions\\.create|model_validate_json\\(" backend/app/service backend/tests
```

Expected: remaining matches should be confined to non-runtime scripts or not appear at all. Any runtime-service match is still unfinished work.

- [ ] **Step 2: Remove the last direct runtime imports if grep still finds them**

The final migrated service headers should look like this pattern:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from langchain.agents import create_agent
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import Configuration
from backend.app.service.langchain_model_factory import build_story_model
from backend.app.service.llm_rate_limiter import LlmRateLimiter
```

- [ ] **Step 3: Run the complete runtime verification suite**

Run:

```bash
cd /home/czy/karl-fashion-feed
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_event_frame_extraction_service \
  backend.tests.test_story_clustering_service \
  backend.tests.test_story_facet_assignment_service \
  backend.tests.test_digest_packaging_service \
  backend.tests.test_digest_report_writing_service \
  backend.tests.test_digest_generation_service \
  backend.tests.test_story_digest_runtime_integration \
  backend.tests.test_rag_answer_service \
  backend.tests.test_llm_contracts
```

Expected: all tests pass with `OK`.

- [ ] **Step 4: Commit the verification cleanup**

Run:

```bash
cd /home/czy/karl-fashion-feed
git add \
  backend/app/config/llm_config.py \
  backend/app/service/langchain_model_factory.py \
  backend/app/service/event_frame_extraction_service.py \
  backend/app/service/story_clustering_service.py \
  backend/app/service/story_facet_assignment_service.py \
  backend/app/service/digest_packaging_service.py \
  backend/app/service/digest_report_writing_service.py \
  backend/app/service/digest_generation_service.py \
  backend/app/service/RAG/rag_tools.py \
  backend/app/service/RAG/rag_answer_service.py \
  backend/tests/test_event_frame_extraction_service.py \
  backend/tests/test_story_clustering_service.py \
  backend/tests/test_story_facet_assignment_service.py \
  backend/tests/test_digest_packaging_service.py \
  backend/tests/test_digest_report_writing_service.py \
  backend/tests/test_digest_generation_service.py \
  backend/tests/test_story_digest_runtime_integration.py \
  backend/tests/test_rag_answer_service.py \
  backend/tests/test_llm_contracts.py
git commit -m "test: verify langchain runtime migration"
```

## Self-Review

### Spec coverage

- `Configuration` with `base_url`: covered by Task 1.
- `create_agent(..., tools=[], response_format=Schema)` for structured-output services: covered by Tasks 2, 3, and 4.
- `DigestGenerationService` dependency propagation: covered by Task 4.
- `LlmRateLimiter` preservation: covered by Tasks 2, 3, and 4.
- DB-level attempt semantics for `EventFrameExtractionService`: covered by Task 2.
- RAG tool-calling migration and `astream_events(...)` streaming: covered by Task 5.
- Removal of direct `AsyncOpenAI` runtime usage: covered by Task 6.

### Placeholder scan

- No `TODO`, `TBD`, or deferred implementation placeholders remain.
- Every task contains concrete files, commands, and code snippets.

### Type consistency

- Shared runtime config type is consistently `Configuration`.
- Structured-output services consistently use `result["structured_response"]`.
- RAG migration consistently uses `research_agent` and `synthesis_agent`.
