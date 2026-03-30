# Story-Digest Runtime Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `strict_story`-based runtime with an explicit `article -> event_frame -> story -> digest` pipeline where `story` is a same-day canonical event layer and `digest` is a facet-scoped long-form reported article.

**Architecture:** This is one backend runtime refactor, not multiple independent projects. The shortest correct path is to replace `strict_story` with `story`, keep `event_frame` extraction as the article-local truth abstraction, add bounded-context `story` clustering plus `digest` packaging/writing stages, and keep dev-only sampling/debug artifacts inside `backend/app/scripts/` instead of polluting production runtime semantics.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy ORM, Celery, OpenAI-compatible structured outputs, `unittest`, `uv`, `@python-code-style`, `@test-driven-development`, `@verification-before-completion`

---

## Scope Check

Keep this as one plan. The approved spec is one shippable runtime redesign, not separable “schema”, “LLM”, and “scripts” projects:

- model names and memberships change together
- coordinator and Celery tasks must point at the new batch stages
- `digest` semantics only work if `story` clustering, facet assignment, and report writing land together
- dev-only debugging scope must be wired into the same services that emit raw LLM artifacts

Splitting this into separate implementation plans would create half-migrated runtime states that are not safe to ship.

## File Structure

### Models and schema bootstrap

- Create: `backend/app/models/story.py`
  - `Story`
  - `StoryFrame`
  - `StoryArticle`
  - `StoryFacet`
- Modify: `backend/app/models/digest.py`
  - rename `DigestStrictStory` to `DigestStory`
  - keep `DigestArticle`
- Modify: `backend/app/models/runtime.py`
  - rename batch-stage fields from `strict_story_*` to `story_*`
- Modify: `backend/app/models/article.py`
  - bootstrap new story tables
  - stop bootstrapping `strict_story` tables
  - fail fast if old runtime tables are still present in a conflicting shape
- Modify: `backend/app/models/__init__.py`
  - export the new `story` symbols
- Modify: `backend/app/models/README.md`
  - document the `article -> event_frame -> story -> digest` model
- Delete: `backend/app/models/strict_story.py`

### LLM contracts and debug artifacts

- Create: `backend/app/prompts/story_cluster_judgment_prompt.py`
- Create: `backend/app/prompts/facet_assignment_prompt.py`
- Create: `backend/app/prompts/digest_packaging_prompt.py`
- Create: `backend/app/prompts/digest_report_writing_prompt.py`
- Create: `backend/app/schemas/llm/story_cluster_judgment.py`
- Create: `backend/app/schemas/llm/facet_assignment.py`
- Create: `backend/app/schemas/llm/digest_packaging.py`
- Create: `backend/app/schemas/llm/digest_report_writing.py`
- Create: `backend/app/service/llm_debug_artifact_service.py`
- Modify: `backend/app/prompts/__init__.py`
- Modify: `backend/app/schemas/llm/__init__.py`
- Delete: `backend/app/prompts/strict_story_tiebreak_prompt.py`
- Delete: `backend/app/prompts/digest_generation_prompt.py`
- Delete: `backend/app/schemas/llm/strict_story_tiebreak.py`
- Delete: `backend/app/schemas/llm/digest_generation.py`

### Runtime services

- Create: `backend/app/service/story_clustering_service.py`
- Create: `backend/app/service/story_facet_assignment_service.py`
- Create: `backend/app/service/digest_packaging_service.py`
- Create: `backend/app/service/digest_report_writing_service.py`
- Modify: `backend/app/service/digest_generation_service.py`
  - orchestrate facet assignment, packaging, and writing
- Modify: `backend/app/service/event_frame_extraction_service.py`
  - keep extraction behavior
  - remove any remaining story-grouping semantics from comments or helper names
- Modify: `backend/app/service/daily_run_coordinator_service.py`
  - enqueue `story` batch stage instead of `strict_story`
  - keep `digest` as the final batch stage
- Delete: `backend/app/service/strict_story_packing_service.py`

### Task wiring, API, and scripts

- Modify: `backend/app/tasks/aggregation_tasks.py`
  - replace `pack_strict_stories_for_day` with `cluster_stories_for_day`
- Modify: `backend/app/tasks/__init__.py`
- Modify: `backend/app/router/digest_router.py`
  - keep public API shape
  - source links should read from canonical source URLs
- Modify: `backend/app/scripts/dev_run_today_digest_pipeline.py`
  - add dev-only `published_at=today` filtering
  - add raw LLM artifact output directory
- Modify: `backend/app/scripts/README.md`
- Modify: `backend/README.md`

### Tests

- Create: `backend/tests/test_story_models.py`
- Create: `backend/tests/test_llm_contracts.py`
- Create: `backend/tests/test_llm_debug_artifact_service.py`
- Create: `backend/tests/test_story_clustering_service.py`
- Create: `backend/tests/test_story_facet_assignment_service.py`
- Create: `backend/tests/test_digest_packaging_service.py`
- Create: `backend/tests/test_digest_report_writing_service.py`
- Modify: `backend/tests/test_celery_config.py`
  - keep existing coverage intact
- Create: `backend/tests/test_digest_generation_service.py`
- Create: `backend/tests/test_daily_run_coordinator_service.py`
- Create: `backend/tests/test_dev_run_today_digest_pipeline.py`
- Create: `backend/tests/test_story_digest_runtime_integration.py`

## Runtime Contract

Use these exact runtime nouns everywhere:

- `story`
- `story_frame`
- `story_article`
- `story_facet`
- `digest_story`

Use these exact batch stage names on `PipelineRun`:

- `story`
- `digest`

Use these exact facet values:

- `runway_series`
- `street_style`
- `trend_summary`
- `brand_market`

Use these exact service names:

- `StoryClusteringService`
- `StoryFacetAssignmentService`
- `DigestPackagingService`
- `DigestReportWritingService`
- `DigestGenerationService`

`signature_json` must not appear in any story-grouping logic, prompt payload, or rerun identity logic.

## Task 1: Replace `strict_story` ORM and runtime state with `story`

**Files:**
- Create: `backend/app/models/story.py`
- Modify: `backend/app/models/digest.py`
- Modify: `backend/app/models/runtime.py`
- Modify: `backend/app/models/article.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/README.md`
- Delete: `backend/app/models/strict_story.py`
- Test: `backend/tests/test_story_models.py`

- [ ] **Step 1: Write the failing schema/bootstrap tests**

```python
from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect

from backend.app.models.article import ensure_article_storage_schema


class StorySchemaBootstrapTest(unittest.TestCase):
    def test_ensure_article_storage_schema_creates_story_tables(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

        ensure_article_storage_schema(engine)

        table_names = set(inspect(engine).get_table_names())
        self.assertIn("story", table_names)
        self.assertIn("story_frame", table_names)
        self.assertIn("story_article", table_names)
        self.assertIn("story_facet", table_names)
        self.assertIn("digest_story", table_names)
        self.assertNotIn("strict_story", table_names)

    def test_pipeline_run_uses_story_stage_columns(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

        ensure_article_storage_schema(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("pipeline_run")}
        self.assertIn("story_status", columns)
        self.assertIn("story_attempts", columns)
        self.assertIn("story_error", columns)
        self.assertIn("story_updated_at", columns)
        self.assertIn("story_token", columns)
        self.assertNotIn("strict_story_status", columns)
```

- [ ] **Step 2: Run the bootstrap test to verify it fails against the current strict-story runtime**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_models -v
```

Expected: FAIL because `story` tables and `story_*` pipeline columns do not exist yet.

- [ ] **Step 3: Implement the new ORM and bootstrap contract**

`backend/app/models/story.py`

```python
from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Story(Base):
    __tablename__ = "story"

    story_key: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    synopsis_zh: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    article_membership_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("pipeline_run.run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    clustering_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    clustering_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow_naive)


class StoryFrame(Base):
    __tablename__ = "story_frame"

    story_key: Mapped[str] = mapped_column(ForeignKey("story.story_key", ondelete="CASCADE"), primary_key=True)
    event_frame_id: Mapped[str] = mapped_column(
        ForeignKey("article_event_frame.event_frame_id", ondelete="CASCADE"),
        primary_key=True,
        unique=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoryArticle(Base):
    __tablename__ = "story_article"

    story_key: Mapped[str] = mapped_column(ForeignKey("story.story_key", ondelete="CASCADE"), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("article.article_id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoryFacet(Base):
    __tablename__ = "story_facet"

    story_key: Mapped[str] = mapped_column(ForeignKey("story.story_key", ondelete="CASCADE"), primary_key=True)
    facet: Mapped[str] = mapped_column(String(64), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

`backend/app/models/digest.py`

```python
class DigestStory(Base):
    __tablename__ = "digest_story"

    digest_key: Mapped[str] = mapped_column(
        ForeignKey("digest.digest_key", ondelete="CASCADE"),
        primary_key=True,
    )
    story_key: Mapped[str] = mapped_column(
        ForeignKey("story.story_key", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

`backend/app/models/runtime.py`

```python
class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    story_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    story_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    story_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow_naive)
    story_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    digest_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
```

`backend/app/models/article.py`

```python
from backend.app.models.digest import Digest, DigestArticle, DigestStory
from backend.app.models.story import Story, StoryArticle, StoryFacet, StoryFrame

Base.metadata.create_all(
    bind=bind,
    tables=[
        Article.__table__,
        ArticleImage.__table__,
        PipelineRun.__table__,
        SourceRunState.__table__,
        ArticleEventFrame.__table__,
        Story.__table__,
        StoryFrame.__table__,
        StoryArticle.__table__,
        StoryFacet.__table__,
        Digest.__table__,
        DigestStory.__table__,
        DigestArticle.__table__,
    ],
)
```

- [ ] **Step 4: Run model tests again and verify they pass**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_models -v
```

Expected: PASS with `story` tables and `story_*` batch-stage fields present.

- [ ] **Step 5: Commit the schema replacement**

```bash
git add backend/app/models/story.py backend/app/models/digest.py backend/app/models/runtime.py backend/app/models/article.py backend/app/models/__init__.py backend/app/models/README.md backend/tests/test_story_models.py
git commit -m "refactor: replace strict story runtime tables with story tables"
```

## Task 2: Add new LLM contracts and raw debug artifact recording

**Files:**
- Create: `backend/app/prompts/story_cluster_judgment_prompt.py`
- Create: `backend/app/prompts/facet_assignment_prompt.py`
- Create: `backend/app/prompts/digest_packaging_prompt.py`
- Create: `backend/app/prompts/digest_report_writing_prompt.py`
- Create: `backend/app/schemas/llm/story_cluster_judgment.py`
- Create: `backend/app/schemas/llm/facet_assignment.py`
- Create: `backend/app/schemas/llm/digest_packaging.py`
- Create: `backend/app/schemas/llm/digest_report_writing.py`
- Create: `backend/app/service/llm_debug_artifact_service.py`
- Modify: `backend/app/prompts/__init__.py`
- Modify: `backend/app/schemas/llm/__init__.py`
- Delete: `backend/app/prompts/strict_story_tiebreak_prompt.py`
- Delete: `backend/app/prompts/digest_generation_prompt.py`
- Delete: `backend/app/schemas/llm/strict_story_tiebreak.py`
- Delete: `backend/app/schemas/llm/digest_generation.py`
- Test: `backend/tests/test_llm_contracts.py`
- Test: `backend/tests/test_llm_debug_artifact_service.py`

- [ ] **Step 1: Write failing tests for the new structured outputs and artifact recorder**

```python
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from backend.app.schemas.llm.digest_packaging import DigestPackagingSchema
from backend.app.schemas.llm.facet_assignment import FacetAssignmentSchema
from backend.app.schemas.llm.story_cluster_judgment import StoryClusterJudgmentSchema
from backend.app.service.llm_debug_artifact_service import LlmDebugArtifactRecorder


class LlmContractsTest(unittest.TestCase):
    def test_story_cluster_judgment_schema_parses_group_members(self) -> None:
        payload = '{"groups":[{"seed_event_frame_id":"f1","member_event_frame_ids":["f1","f2"],"synopsis_zh":"巴黎秀场同一事件","event_type":"runway_show","anchor_json":{"brand":"A"}}]}'
        parsed = StoryClusterJudgmentSchema.model_validate_json(payload)
        self.assertEqual(parsed.groups[0].member_event_frame_ids, ["f1", "f2"])

    def test_facet_assignment_schema_parses_multi_facet_membership(self) -> None:
        payload = '{"stories":[{"story_key":"s1","facets":["runway_series","trend_summary"]}]}'
        parsed = FacetAssignmentSchema.model_validate_json(payload)
        self.assertEqual(parsed.stories[0].facets, ["runway_series", "trend_summary"])

    def test_digest_packaging_schema_parses_overlapping_story_plans(self) -> None:
        payload = '{"digests":[{"facet":"trend_summary","story_keys":["s1","s2"],"article_ids":["a1","a2"],"editorial_angle":"秀场肩部轮廓趋势","title_zh":"肩部轮廓成为本季主线","dek_zh":"多场发布共同推高这一轮趋势"}]}'
        parsed = DigestPackagingSchema.model_validate_json(payload)
        self.assertEqual(parsed.digests[0].story_keys, ["s1", "s2"])


class LlmDebugArtifactRecorderTest(unittest.TestCase):
    def test_record_writes_prompt_and_response_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = LlmDebugArtifactRecorder(base_dir=Path(tmpdir), enabled=True)

            prompt_path, response_path = recorder.record(
                run_id="run-1",
                stage="story_cluster",
                object_key="frame-f1",
                prompt_text='{"frames":[]}',
                response_text='{"groups":[]}',
            )

            self.assertTrue(prompt_path.exists())
            self.assertTrue(response_path.exists())
```

- [ ] **Step 2: Run the new contract tests to verify they fail**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_llm_contracts backend.tests.test_llm_debug_artifact_service -v
```

Expected: FAIL because the new prompt/schema/recorder modules do not exist yet.

- [ ] **Step 3: Implement the new prompt/schema modules and recorder**

`backend/app/schemas/llm/story_cluster_judgment.py`

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class StoryClusterGroup(BaseModel):
    seed_event_frame_id: str = Field(min_length=1)
    member_event_frame_ids: list[str] = Field(min_length=1)
    synopsis_zh: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    anchor_json: dict = Field(default_factory=dict)


class StoryClusterJudgmentSchema(BaseModel):
    groups: list[StoryClusterGroup] = Field(default_factory=list)
```

`backend/app/schemas/llm/facet_assignment.py`

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class StoryFacetDecision(BaseModel):
    story_key: str = Field(min_length=1)
    facets: list[str] = Field(default_factory=list)


class FacetAssignmentSchema(BaseModel):
    stories: list[StoryFacetDecision] = Field(default_factory=list)
```

`backend/app/service/llm_debug_artifact_service.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class LlmDebugArtifactRecorder:
    base_dir: Path | None
    enabled: bool = False

    def record(
        self,
        *,
        run_id: str,
        stage: str,
        object_key: str,
        prompt_text: str,
        response_text: str,
    ) -> tuple[Path, Path]:
        if not self.enabled or self.base_dir is None:
            raise RuntimeError("LLM debug artifact recording is not enabled")

        target_dir = self.base_dir / run_id / stage / object_key
        target_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = target_dir / "prompt.json"
        response_path = target_dir / "response.json"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        response_path.write_text(response_text, encoding="utf-8")
        return prompt_path, response_path
```

`backend/app/prompts/digest_report_writing_prompt.py`

```python
def build_digest_report_writing_prompt() -> str:
    return """
你是时尚新闻编辑。输入是一篇 digest 已经选定的 story 与 source article 原文。
请输出一篇中文长稿，写成报道而不是摘要，不要编造输入中不存在的事实。
如有必要，可以在文中引用输入给出的 canonical_url。
""".strip()
```

- [ ] **Step 4: Run the contract and recorder tests again**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_llm_contracts backend.tests.test_llm_debug_artifact_service -v
```

Expected: PASS with the new schemas and artifact recorder writing prompt/response files.

- [ ] **Step 5: Commit the new LLM contract layer**

```bash
git add backend/app/prompts/story_cluster_judgment_prompt.py backend/app/prompts/facet_assignment_prompt.py backend/app/prompts/digest_packaging_prompt.py backend/app/prompts/digest_report_writing_prompt.py backend/app/schemas/llm/story_cluster_judgment.py backend/app/schemas/llm/facet_assignment.py backend/app/schemas/llm/digest_packaging.py backend/app/schemas/llm/digest_report_writing.py backend/app/service/llm_debug_artifact_service.py backend/tests/test_llm_contracts.py backend/tests/test_llm_debug_artifact_service.py
git commit -m "feat: add story and digest llm contracts"
```

## Task 3: Implement bounded-context same-day `story` clustering

**Files:**
- Create: `backend/app/service/story_clustering_service.py`
- Modify: `backend/app/service/event_frame_extraction_service.py`
- Modify: `backend/app/models/story.py`
- Test: `backend/tests/test_story_clustering_service.py`

- [ ] **Step 1: Write the failing clustering tests**

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models.article import Article, ensure_article_storage_schema
from backend.app.models.event_frame import ArticleEventFrame
from backend.app.service.story_clustering_service import StoryClusteringService


def build_frame(frame_id: str, article_id: str, *, event_type: str, brand: str, person: str = "") -> ArticleEventFrame:
    return ArticleEventFrame(
        event_frame_id=frame_id,
        article_id=article_id,
        business_date=date(2026, 3, 29),
        event_type=event_type,
        subject_json={"brand": brand, "person": person},
        action_text="发布新内容",
        object_text="",
        place_text="Paris",
        collection_text="FW26",
        season_text="FW26",
        show_context_text="",
        evidence_json=[{"quote": "Acme in Paris"}],
        signature_json={"brand": brand},
        extraction_confidence=0.9,
        extraction_status="done",
        extraction_error=None,
    )


def build_story_test_session_with_frames(*, business_day: date, frames: list[ArticleEventFrame]) -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    SessionFactory = sessionmaker(bind=engine, future=True)
    session = SessionFactory()
    article_ids = {frame.article_id for frame in frames}
    session.add_all(
        [
            Article(
                article_id=article_id,
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url=f"https://example.com/{article_id}",
                original_url=f"https://example.com/{article_id}",
                title_raw=f"Article {article_id}",
                summary_raw="",
                markdown_rel_path=f"2026/03/29/{article_id}.md",
            )
            for article_id in sorted(article_ids)
        ]
    )
    session.add_all(frames)
    session.commit()
    return session


def build_fake_llm_client(raw_content: str) -> SimpleNamespace:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw_content))]
    )
    completions = SimpleNamespace(create=lambda **_: response)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


class StoryClusteringServiceTest(unittest.TestCase):
    def test_cluster_business_day_merges_same_event_even_when_event_types_differ(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[
                build_frame("f1", "a1", event_type="runway_show", brand="Acme", person="Jane"),
                build_frame("f2", "a2", event_type="campaign_launch", brand="Acme", person="Jane"),
            ],
        )
        fake_client = build_fake_llm_client(
            '{"groups":[{"seed_event_frame_id":"f1","member_event_frame_ids":["f1","f2"],"synopsis_zh":"Acme 巴黎秀场同一主事件","event_type":"runway_show","anchor_json":{"brand":"Acme","person":"Jane"}}]}'
        )

        stories = asyncio.run(
            StoryClusteringService(client=fake_client).cluster_business_day(
                session,
                business_day=date(2026, 3, 29),
                run_id="run-1",
            )
        )

        self.assertEqual(len(stories), 1)

    def test_cluster_business_day_fails_when_non_empty_input_produces_zero_stories(self) -> None:
        session = build_story_test_session_with_frames(
            business_day=date(2026, 3, 29),
            frames=[build_frame("f1", "a1", event_type="runway_show", brand="Acme")],
        )
        fake_client = build_fake_llm_client('{"groups":[]}')

        with self.assertRaisesRegex(RuntimeError, "produced zero stories"):
            asyncio.run(
                StoryClusteringService(client=fake_client).cluster_business_day(
                    session,
                    business_day=date(2026, 3, 29),
                    run_id="run-1",
                )
            )
```

- [ ] **Step 2: Run the clustering tests to verify they fail**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_clustering_service -v
```

Expected: FAIL because `StoryClusteringService` does not exist.

- [ ] **Step 3: Implement frame cards, candidate blocking, small-window judgment, and cluster persistence**

`backend/app/service/story_clustering_service.py`

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ArticleEventFrame, Story, StoryArticle, StoryFrame
from backend.app.service.llm_debug_artifact_service import LlmDebugArtifactRecorder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameCard:
    event_frame_id: str
    article_id: str
    event_type: str
    anchor_json: dict
    action_text: str
    evidence_json: list[dict]


class StoryClusteringService:
    async def cluster_business_day(self, session: Session, business_day: date, *, run_id: str) -> list[Story]:
        frames = list(
            session.scalars(
                select(ArticleEventFrame)
                .where(ArticleEventFrame.business_date == business_day)
                .order_by(ArticleEventFrame.event_frame_id.asc())
            ).all()
        )
        if not frames:
            return []

        frame_cards = [self._build_frame_card(frame) for frame in frames]
        groups = await self._judge_candidate_groups(frame_cards, run_id=run_id)
        if not groups:
            raise RuntimeError(f"story clustering produced zero stories for non-empty input: {business_day.isoformat()}")
        return self._replace_day_stories(session, business_day, run_id=run_id, groups=groups)

    def _build_frame_card(self, frame: ArticleEventFrame) -> FrameCard:
        return FrameCard(
            event_frame_id=frame.event_frame_id,
            article_id=frame.article_id,
            event_type=frame.event_type,
            anchor_json={
                "brand": frame.subject_json.get("brand", ""),
                "person": frame.subject_json.get("person", ""),
                "collection": frame.collection_text,
                "season": frame.season_text,
                "place": frame.place_text,
            },
            action_text=frame.action_text,
            evidence_json=[dict(item) for item in frame.evidence_json],
        )
```

- [ ] **Step 4: Run the clustering tests and a focused regression on event-frame extraction**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_clustering_service -v
```

Expected: PASS for clustering with one merged story and a hard failure on non-empty input that produces zero stories.

- [ ] **Step 5: Commit the story-clustering stage**

```bash
git add backend/app/service/story_clustering_service.py backend/app/service/event_frame_extraction_service.py backend/tests/test_story_clustering_service.py
git commit -m "feat: add bounded-context story clustering"
```

## Task 4: Split `digest` generation into facet assignment, packaging, and long-form writing

**Files:**
- Create: `backend/app/service/story_facet_assignment_service.py`
- Create: `backend/app/service/digest_packaging_service.py`
- Create: `backend/app/service/digest_report_writing_service.py`
- Modify: `backend/app/service/digest_generation_service.py`
- Modify: `backend/app/models/digest.py`
- Test: `backend/tests/test_story_facet_assignment_service.py`
- Test: `backend/tests/test_digest_packaging_service.py`
- Test: `backend/tests/test_digest_report_writing_service.py`
- Test: `backend/tests/test_digest_generation_service.py`

- [ ] **Step 1: Write the failing digest-stage tests**

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Article, Digest, Story, StoryFacet, ensure_article_storage_schema
from backend.app.service.digest_generation_service import DigestGenerationService


class FakeFacetAssignmentService:
    async def assign_for_day(self, session, business_day: date) -> list[StoryFacet]:
        return list(session.query(StoryFacet).filter(StoryFacet.story_key.is_not(None)).all())


class FakePackagingService:
    def __init__(self, plans):
        self._plans = plans

    async def build_plans_for_day(self, session, business_day: date):
        return self._plans


class FakeReportWritingService:
    async def write_digest(self, session, plan, *, run_id: str) -> Digest:
        return Digest(
            digest_key=f"{plan.facet}-{len(plan.story_keys)}",
            business_date=plan.business_date,
            facet=plan.facet,
            title_zh=plan.title_zh,
            dek_zh=plan.dek_zh,
            body_markdown=f"# {plan.title_zh}\n\n长稿正文。",
            hero_image_url=None,
            source_article_count=len(plan.article_ids),
            source_names_json=list(plan.source_names),
            created_run_id=run_id,
            generation_status="done",
            generation_error=None,
        )


def build_digest_test_session(*, business_day: date, stories: list[Story], story_facets: list[StoryFacet], articles: list[Article]):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    SessionFactory = sessionmaker(bind=engine, future=True)
    session = SessionFactory()
    session.add_all(articles + stories + story_facets)
    session.commit()
    return session


def build_story(story_key: str) -> Story:
    return Story(
        story_key=story_key,
        business_date=date(2026, 3, 29),
        event_type="runway_show",
        synopsis_zh=f"{story_key} synopsis",
        anchor_json={"brand": "Acme"},
        article_membership_json=["a1"],
        created_run_id="run-1",
        clustering_status="done",
        clustering_error=None,
    )


def build_article(article_id: str, *, canonical_url: str, markdown_rel_path: str) -> Article:
    return Article(
        article_id=article_id,
        source_name="Vogue",
        source_type="rss",
        source_lang="en",
        category="fashion",
        canonical_url=canonical_url,
        original_url=canonical_url,
        title_raw=f"Article {article_id}",
        summary_raw="",
        markdown_rel_path=markdown_rel_path,
    )


class DigestGenerationServiceTest(unittest.TestCase):
    def test_generate_for_day_allows_story_overlap_across_digest_plans(self) -> None:
        session = build_digest_test_session(
            business_day=date(2026, 3, 29),
            stories=[
                build_story("s1"),
                build_story("s2"),
            ],
            story_facets=[
                StoryFacet(story_key="s1", facet="runway_series", rank=0),
                StoryFacet(story_key="s1", facet="trend_summary", rank=1),
                StoryFacet(story_key="s2", facet="trend_summary", rank=0),
            ],
            articles=[
                build_article("a1", canonical_url="https://source/1", markdown_rel_path="2026/03/29/a1.md"),
                build_article("a2", canonical_url="https://source/2", markdown_rel_path="2026/03/29/a2.md"),
            ],
        )
        generation = DigestGenerationService(
            facet_assignment_service=FakeFacetAssignmentService(),
            packaging_service=FakePackagingService(
                plans=[
                    SimpleNamespace(
                        business_date=date(2026, 3, 29),
                        facet="runway_series",
                        story_keys=("s1",),
                        article_ids=("a1",),
                        source_names=("Vogue",),
                        editorial_angle="秀场单稿",
                        title_zh="Acme 秀场速写",
                        dek_zh="一篇秀场稿",
                    ),
                    SimpleNamespace(
                        business_date=date(2026, 3, 29),
                        facet="trend_summary",
                        story_keys=("s1", "s2"),
                        article_ids=("a1", "a2"),
                        source_names=("Vogue",),
                        editorial_angle="趋势综合稿",
                        title_zh="Acme 趋势总览",
                        dek_zh="两条 story 共用",
                    ),
                ]
            ),
            report_writing_service=FakeReportWritingService(),
        )

        digests = asyncio.run(generation.generate_for_day(session, date(2026, 3, 29), run_id="run-1"))

        self.assertEqual(len(digests), 2)

    def test_generate_for_day_raises_when_packaging_returns_zero_plans_for_non_empty_input(self) -> None:
        session = build_digest_test_session(
            business_day=date(2026, 3, 29),
            stories=[build_story("s1")],
            story_facets=[StoryFacet(story_key="s1", facet="brand_market", rank=0)],
            articles=[build_article("a1", canonical_url="https://source/1", markdown_rel_path="2026/03/29/a1.md")],
        )
        generation = DigestGenerationService(
            facet_assignment_service=FakeFacetAssignmentService(),
            packaging_service=FakePackagingService(plans=[]),
            report_writing_service=FakeReportWritingService(),
        )

        with self.assertRaisesRegex(RuntimeError, "packaging produced zero digest plans"):
            asyncio.run(generation.generate_for_day(session, date(2026, 3, 29), run_id="run-1"))
```

- [ ] **Step 2: Run the digest tests to verify they fail**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_facet_assignment_service backend.tests.test_digest_packaging_service backend.tests.test_digest_report_writing_service backend.tests.test_digest_generation_service -v
```

Expected: FAIL because the new services and overlap semantics do not exist.

- [ ] **Step 3: Implement the three digest subservices and orchestrator**

`backend/app/service/story_facet_assignment_service.py`

```python
class StoryFacetAssignmentService:
    async def assign_for_day(self, session: Session, business_day: date) -> list[StoryFacet]:
        stories = self._load_story_cards(session, business_day)
        if not stories:
            return []
        schema = await self._infer_facets(stories)
        return self._replace_story_facets(session, business_day, schema)
```

`backend/app/service/digest_packaging_service.py`

```python
class DigestPackagingService:
    async def build_plans_for_day(self, session: Session, business_day: date) -> list[ResolvedDigestPlan]:
        facet_story_cards = self._load_facet_story_cards(session, business_day)
        plans: list[ResolvedDigestPlan] = []
        for facet, story_cards in facet_story_cards.items():
            plans.extend(await self._package_facet(facet, story_cards))
        return plans
```

`backend/app/service/digest_report_writing_service.py`

```python
class DigestReportWritingService:
    async def write_digest(self, session: Session, plan: ResolvedDigestPlan, *, run_id: str) -> Digest:
        source_articles = self._load_source_articles(session, plan.article_ids)
        raw_body = await self._write_body(plan=plan, source_articles=source_articles)
        return Digest(
            digest_key=str(uuid4()),
            business_date=plan.business_date,
            facet=plan.facet,
            title_zh=plan.title_zh,
            dek_zh=plan.dek_zh,
            body_markdown=raw_body,
            source_article_count=len(plan.article_ids),
            source_names_json=list(plan.source_names),
            created_run_id=run_id,
            generation_status="done",
            generation_error=None,
        )
```

`backend/app/service/digest_generation_service.py`

```python
class DigestGenerationService:
    async def generate_for_day(self, session: Session, business_day: date, *, run_id: str) -> list[Digest]:
        await self._facet_assignment_service.assign_for_day(session, business_day)
        plans = await self._packaging_service.build_plans_for_day(session, business_day)
        if self._has_packaging_input(session, business_day) and not plans:
            raise RuntimeError(f"digest packaging produced zero digest plans: {business_day.isoformat()}")
        return self._replace_day_digests(session, business_day, run_id=run_id, plans=plans)
```

- [ ] **Step 4: Run the digest tests and verify overlap plus fail-fast behavior**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_facet_assignment_service backend.tests.test_digest_packaging_service backend.tests.test_digest_report_writing_service backend.tests.test_digest_generation_service -v
```

Expected: PASS with story overlap allowed and zero-plan packaging treated as an error when input exists.

- [ ] **Step 5: Commit the digest-stage split**

```bash
git add backend/app/service/story_facet_assignment_service.py backend/app/service/digest_packaging_service.py backend/app/service/digest_report_writing_service.py backend/app/service/digest_generation_service.py backend/tests/test_story_facet_assignment_service.py backend/tests/test_digest_packaging_service.py backend/tests/test_digest_report_writing_service.py backend/tests/test_digest_generation_service.py
git commit -m "feat: split digest runtime into assignment packaging and writing"
```

## Task 5: Rewire Celery aggregation and coordinator batch stages

**Files:**
- Modify: `backend/app/tasks/aggregation_tasks.py`
- Modify: `backend/app/tasks/__init__.py`
- Modify: `backend/app/service/daily_run_coordinator_service.py`
- Modify: `backend/app/models/runtime.py`
- Test: `backend/tests/test_daily_run_coordinator_service.py`

- [ ] **Step 1: Write failing coordinator tests for `story` and `digest` stage transitions**

```python
from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Article, PipelineRun, Story, ensure_article_storage_schema
from backend.app.service.daily_run_coordinator_service import DailyRunCoordinatorService


def build_runtime_test_session_with_articles(*, event_frame_statuses: list[str], story_status: str, digest_status: str):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    SessionFactory = sessionmaker(bind=engine, future=True)
    session = SessionFactory()
    session.add(
        PipelineRun(
            run_id="run-1",
            business_date=datetime(2026, 3, 29, tzinfo=UTC).date(),
            run_type="digest_daily",
            status="running",
            story_status=story_status,
            digest_status=digest_status,
        )
    )
    session.add_all(
        [
            Article(
                article_id=f"a{index}",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url=f"https://example.com/a{index}",
                original_url=f"https://example.com/a{index}",
                title_raw=f"Article {index}",
                summary_raw="",
                markdown_rel_path=f"2026/03/29/a{index}.md",
                ingested_at=datetime(2026, 3, 29, 0, index, 0),
                parse_status="done",
                event_frame_status=status,
            )
            for index, status in enumerate(event_frame_statuses, start=1)
        ]
    )
    session.commit()
    return session


def build_runtime_test_session_with_stories(*, story_count: int, digest_count: int, digest_status: str):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    SessionFactory = sessionmaker(bind=engine, future=True)
    session = SessionFactory()
    session.add(
        PipelineRun(
            run_id="run-1",
            business_date=datetime(2026, 3, 29, tzinfo=UTC).date(),
            run_type="digest_daily",
            status="running",
            story_status="done",
            digest_status=digest_status,
        )
    )
    session.add_all(
        [
            Story(
                story_key=f"s{index}",
                business_date=datetime(2026, 3, 29, tzinfo=UTC).date(),
                event_type="runway_show",
                synopsis_zh=f"story {index}",
                anchor_json={"brand": "Acme"},
                article_membership_json=[],
                created_run_id="run-1",
                clustering_status="done",
                clustering_error=None,
            )
            for index in range(story_count)
        ]
    )
    session.commit()
    return session


class DailyRunCoordinatorServiceTest(unittest.TestCase):
    def test_tick_queues_story_batch_after_event_frame_stage_drains(self) -> None:
        session = build_runtime_test_session_with_articles(
            event_frame_statuses=["done", "done"],
            story_status="pending",
            digest_status="pending",
        )
        service = DailyRunCoordinatorService(session_factory=lambda: session)

        with patch("backend.app.service.daily_run_coordinator_service.cluster_stories_for_day.delay") as mocked_delay:
            service.tick(now=datetime(2026, 3, 29, 1, 0, tzinfo=UTC))

        mocked_delay.assert_called_once()

    def test_tick_never_marks_run_done_when_digest_output_is_empty_after_non_empty_story_input(self) -> None:
        session = build_runtime_test_session_with_stories(story_count=2, digest_count=0, digest_status="failed")
        service = DailyRunCoordinatorService(session_factory=lambda: session)

        with self.assertRaisesRegex(RuntimeError, "unexpectedly empty final digest set"):
            service.drain_until_idle(
                run_id="run-1",
                business_day=datetime(2026, 3, 29, tzinfo=UTC).date(),
            )
```

- [ ] **Step 2: Run the coordinator tests to verify they fail**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_daily_run_coordinator_service -v
```

Expected: FAIL because the coordinator still uses `strict_story` stage names and the old aggregation task names.

- [ ] **Step 3: Replace strict-story task wiring with story-stage wiring**

`backend/app/tasks/aggregation_tasks.py`

```python
@celery_app.task(name="aggregation.cluster_stories_for_day")
def cluster_stories_for_day(business_day_iso: str, run_id: str, ownership_token: int) -> None:
    business_day = date.fromisoformat(business_day_iso)
    service = StoryClusteringService()
    with SessionLocal() as session:
        ensure_article_storage_schema(session.get_bind())
        _claim_batch_stage(
            session=session,
            run_id=run_id,
            business_day=business_day,
            stage="story",
            ownership_token=ownership_token,
        )
        try:
            asyncio.run(service.cluster_business_day(session, business_day, run_id=run_id))
            _finalize_batch_stage_success(
                session=session,
                run_id=run_id,
                business_day=business_day,
                stage="story",
                ownership_token=ownership_token,
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            _finalize_batch_stage_failure(
                session=session,
                run_id=run_id,
                business_day=business_day,
                stage="story",
                ownership_token=ownership_token,
                exc=exc,
            )
            raise
```

`backend/app/service/daily_run_coordinator_service.py`

```python
from backend.app.tasks.aggregation_tasks import cluster_stories_for_day, generate_digests_for_day

run = PipelineRun(
    business_date=business_day,
    run_type=RUN_TYPE_DAILY_DIGEST,
    status="running",
    started_at=observed_at,
    story_updated_at=observed_at,
    story_token=0,
    digest_updated_at=observed_at,
    digest_token=0,
    metadata_json={},
)
```

- [ ] **Step 4: Run coordinator and aggregation tests**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_daily_run_coordinator_service -v
```

Expected: PASS with `story` batch-stage queueing and hard failure on empty final digest outputs after non-empty story input.

- [ ] **Step 5: Commit the runtime control-plane refactor**

```bash
git add backend/app/tasks/aggregation_tasks.py backend/app/tasks/__init__.py backend/app/service/daily_run_coordinator_service.py backend/tests/test_daily_run_coordinator_service.py
git commit -m "refactor: switch batch runtime from strict story to story stage"
```

## Task 6: Add script-local dev sampling and raw LLM artifact dumps

**Files:**
- Modify: `backend/app/scripts/dev_run_today_digest_pipeline.py`
- Modify: `backend/app/scripts/README.md`
- Modify: `backend/README.md`
- Modify: `backend/app/router/digest_router.py`
- Test: `backend/tests/test_dev_run_today_digest_pipeline.py`
- Test: `backend/tests/test_digest_router.py`

- [ ] **Step 1: Write the failing dev-script tests**

```python
from __future__ import annotations

import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Article, Digest, DigestArticle, ensure_article_storage_schema
from backend.app.router.digest_router import build_digest_detail_response
from backend.app.scripts.dev_run_today_digest_pipeline import _build_parser, filter_dev_articles_by_published_today


class DevRunTodayDigestPipelineScriptTest(unittest.TestCase):
    def test_parser_accepts_published_today_only_and_llm_artifact_dir(self) -> None:
        args = _build_parser().parse_args(
            ["--skip-collect", "--published-today-only", "--llm-artifact-dir", "/tmp/llm-artifacts"]
        )

        self.assertTrue(args.published_today_only)
        self.assertEqual(args.llm_artifact_dir, Path("/tmp/llm-artifacts"))

    def test_filter_excludes_articles_with_null_published_at(self) -> None:
        articles = [
            {"article_id": "a1", "published_at": "2026-03-29T08:00:00"},
            {"article_id": "a2", "published_at": None},
        ]

        filtered = filter_dev_articles_by_published_today(articles, business_day="2026-03-29")

        self.assertEqual([item["article_id"] for item in filtered], ["a1"])


class DigestRouterTest(unittest.TestCase):
    def test_digest_detail_prefers_canonical_source_links(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ensure_article_storage_schema(engine)
        SessionFactory = sessionmaker(bind=engine, future=True)
        session = SessionFactory()
        session.add(
            Digest(
                digest_key="digest-1",
                business_date=date(2026, 3, 29),
                facet="brand_market",
                title_zh="标题",
                dek_zh="导语",
                body_markdown="正文",
                hero_image_url=None,
                source_article_count=1,
                source_names_json=["Vogue"],
                created_run_id="run-1",
                generation_status="done",
                generation_error=None,
            )
        )
        session.add(
            Article(
                article_id="a1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="fashion",
                canonical_url="https://canonical.example/a1",
                original_url="https://original.example/a1",
                title_raw="Source Title",
                summary_raw="",
                markdown_rel_path="2026/03/29/a1.md",
            )
        )
        session.add(DigestArticle(digest_key="digest-1", article_id="a1", rank=0))
        session.commit()

        payload = build_digest_detail_response(session, digest_key="digest-1")

        self.assertEqual(payload.sources[0].link, "https://canonical.example/a1")
```

- [ ] **Step 2: Run the dev-script tests to verify they fail**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_dev_run_today_digest_pipeline -v
```

Expected: FAIL because the script does not expose the new debug-only options yet.

- [ ] **Step 3: Add script-only filtering and artifact output**

`backend/app/scripts/dev_run_today_digest_pipeline.py`

```python
parser.add_argument(
    "--published-today-only",
    action="store_true",
    help="Dev-only scope: only include articles whose published_at falls inside the current business day.",
)
parser.add_argument(
    "--llm-artifact-dir",
    type=Path,
    default=None,
    help="Directory for raw prompt/response artifacts during dev runs.",
)
```

```python
def filter_dev_articles_by_published_today(articles: list[dict[str, Any]], *, business_day: str) -> list[dict[str, Any]]:
    return [
        article
        for article in articles
        if article["published_at"] is not None and article["published_at"].startswith(business_day)
    ]
```

```python
if args.llm_artifact_dir is not None:
    os.environ["KARL_LLM_DEBUG_ARTIFACT_DIR"] = str(args.llm_artifact_dir)
```

`backend/app/router/digest_router.py`

```python
sources = [
    DigestDetailSource(
        name=article.source_name,
        title=article.title_raw,
        link=article.canonical_url,
        lang=article.source_lang,
    )
    for article in rows
]
```

- [ ] **Step 4: Run the dev-script tests and a focused digest-router regression**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_dev_run_today_digest_pipeline -v
uv run --project backend python -m unittest backend.tests.test_digest_router -v
```

Expected: PASS with script-local dev filtering only and canonical source links in digest detail.

- [ ] **Step 5: Commit the dev-debugging entrypoint changes**

```bash
git add backend/app/scripts/dev_run_today_digest_pipeline.py backend/app/scripts/README.md backend/README.md backend/app/router/digest_router.py backend/tests/test_dev_run_today_digest_pipeline.py
git commit -m "feat: add dev-only published-today digest debug flow"
```

## Task 7: Remove obsolete strict-story codepaths and lock the redesign with integration coverage

**Files:**
- Delete: `backend/app/service/strict_story_packing_service.py`
- Delete: `backend/app/prompts/strict_story_tiebreak_prompt.py`
- Delete: `backend/app/schemas/llm/strict_story_tiebreak.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_story_digest_runtime_integration.py`

- [ ] **Step 1: Write the failing integration test for the full runtime**

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Article, ArticleEventFrame, Story, StoryFacet, ensure_article_storage_schema
from backend.app.service.digest_generation_service import DigestGenerationService
from backend.app.service.story_clustering_service import StoryClusteringService


def build_full_runtime_session(*, business_day: date, articles: list[Article], frames: list[ArticleEventFrame]):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    ensure_article_storage_schema(engine)
    SessionFactory = sessionmaker(bind=engine, future=True)
    session = SessionFactory()
    session.add_all(articles + frames)
    session.commit()
    return session


def build_article(article_id: str, *, canonical_url: str, markdown_rel_path: str) -> Article:
    return Article(
        article_id=article_id,
        source_name="Vogue",
        source_type="rss",
        source_lang="en",
        category="fashion",
        canonical_url=canonical_url,
        original_url=canonical_url,
        title_raw=f"Article {article_id}",
        summary_raw="",
        markdown_rel_path=markdown_rel_path,
    )


def build_frame(frame_id: str, article_id: str, *, event_type: str, brand: str) -> ArticleEventFrame:
    return ArticleEventFrame(
        event_frame_id=frame_id,
        article_id=article_id,
        business_date=date(2026, 3, 29),
        event_type=event_type,
        subject_json={"brand": brand},
        action_text="发布",
        object_text="",
        place_text="Paris",
        collection_text="FW26",
        season_text="FW26",
        show_context_text="",
        evidence_json=[{"quote": "Acme"}],
        signature_json={"brand": brand},
        extraction_confidence=0.9,
        extraction_status="done",
        extraction_error=None,
    )


class FakeFacetAssignmentService:
    async def assign_for_day(self, session, business_day: date):
        story = session.query(Story).order_by(Story.story_key.asc()).first()
        assert story is not None
        session.add(StoryFacet(story_key=story.story_key, facet="trend_summary", rank=0))
        session.commit()
        return list(session.query(StoryFacet).all())


class FakePackagingService:
    async def build_plans_for_day(self, session, business_day: date):
        story = session.query(Story).order_by(Story.story_key.asc()).first()
        assert story is not None
        return [
            SimpleNamespace(
                business_date=business_day,
                facet="trend_summary",
                story_keys=(story.story_key,),
                article_ids=("a1", "a2"),
                source_names=("Vogue",),
                editorial_angle="Acme 趋势稿",
                title_zh="Acme 趋势稿",
                dek_zh="两篇原文写成长稿",
            )
        ]


class FakeReportWritingService:
    def __init__(self, body: str) -> None:
        self._body = body

    async def write_digest(self, session, plan, *, run_id: str):
        from backend.app.models import Digest

        return Digest(
            digest_key="digest-1",
            business_date=plan.business_date,
            facet=plan.facet,
            title_zh=plan.title_zh,
            dek_zh=plan.dek_zh,
            body_markdown=self._body,
            hero_image_url=None,
            source_article_count=len(plan.article_ids),
            source_names_json=list(plan.source_names),
            created_run_id=run_id,
            generation_status="done",
            generation_error=None,
        )


class StoryDigestRuntimeIntegrationTest(unittest.TestCase):
    def test_same_day_runtime_clusters_stories_then_writes_long_form_digests(self) -> None:
        session = build_full_runtime_session(
            business_day=date(2026, 3, 29),
            articles=[
                build_article("a1", canonical_url="https://source/1", markdown_rel_path="2026/03/29/a1.md"),
                build_article("a2", canonical_url="https://source/2", markdown_rel_path="2026/03/29/a2.md"),
            ],
            frames=[
                build_frame("f1", "a1", event_type="runway_show", brand="Acme"),
                build_frame("f2", "a2", event_type="analysis", brand="Acme"),
            ],
        )
        cluster_service = StoryClusteringService(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **_: SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(
                                        content='{"groups":[{"seed_event_frame_id":"f1","member_event_frame_ids":["f1","f2"],"synopsis_zh":"Acme 同一事件","event_type":"runway_show","anchor_json":{"brand":"Acme"}}]}'
                                    )
                                )
                            ]
                        )
                    )
                )
            )
        )
        digest_service = DigestGenerationService(
            facet_assignment_service=FakeFacetAssignmentService(),
            packaging_service=FakePackagingService(),
            report_writing_service=FakeReportWritingService(body="# 正文\n\n这是一篇更长的报道。"),
        )

        stories = asyncio.run(cluster_service.cluster_business_day(session, date(2026, 3, 29), run_id="run-1"))
        digests = asyncio.run(digest_service.generate_for_day(session, date(2026, 3, 29), run_id="run-1"))

        self.assertEqual(len(stories), 1)
        self.assertEqual(len(digests), 1)
        self.assertIn("更长的报道", digests[0].body_markdown)
```

- [ ] **Step 2: Run the integration test to verify the old codepath still leaks**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_digest_runtime_integration -v
```

Expected: FAIL until all obsolete `strict_story` imports and services are removed.

- [ ] **Step 3: Delete obsolete strict-story modules and update imports**

```python
from backend.app.models.story import Story, StoryArticle, StoryFacet, StoryFrame
from backend.app.models.digest import Digest, DigestArticle, DigestStory

__all__ = [
    "Article",
    "ArticleEventFrame",
    "ArticleImage",
    "Digest",
    "DigestArticle",
    "DigestStory",
    "PipelineRun",
    "SourceRunState",
    "Story",
    "StoryArticle",
    "StoryFacet",
    "StoryFrame",
    "User",
    "ensure_article_storage_schema",
    "ensure_auth_chat_schema",
]
```

- [ ] **Step 4: Run the end-to-end test suite for the redesign**

Run:

```bash
uv run --project backend python -m unittest backend.tests.test_story_models backend.tests.test_llm_contracts backend.tests.test_llm_debug_artifact_service backend.tests.test_story_clustering_service backend.tests.test_story_facet_assignment_service backend.tests.test_digest_packaging_service backend.tests.test_digest_report_writing_service backend.tests.test_digest_generation_service backend.tests.test_daily_run_coordinator_service backend.tests.test_dev_run_today_digest_pipeline backend.tests.test_story_digest_runtime_integration -v
```

Expected: PASS with no remaining `strict_story` runtime dependencies.

- [ ] **Step 5: Commit the cleanup and integration lock**

```bash
git add backend/app/models/__init__.py backend/tests/test_story_digest_runtime_integration.py
git rm backend/app/models/strict_story.py backend/app/service/strict_story_packing_service.py backend/app/prompts/strict_story_tiebreak_prompt.py backend/app/schemas/llm/strict_story_tiebreak.py
git commit -m "refactor: remove obsolete strict story runtime codepaths"
```

## Verification Checklist

- Run the full redesign test suite from Task 7.
- Run the dev script once with `--skip-collect --published-today-only --llm-artifact-dir /tmp/karl-llm-debug` and verify prompt/response files are written.
- Run the API locally and verify `/api/v1/digests/feed` still returns persisted digests and `/api/v1/digests/{digest_key}` exposes canonical source links.
- Confirm that a non-empty story set cannot finish with an empty digest set marked as success.
- Confirm that the same `story` can appear in more than one persisted `digest_story` row.

## Self-Review

### Spec coverage

- `story` replaces `strict_story`: covered by Task 1 and Task 7.
- bounded-context story clustering: covered by Task 3.
- facet assignment, packaging, and final report writing split: covered by Task 4.
- same story can appear in multiple digests: covered by Task 4 tests.
- production runtime unchanged while dev sampling stays script-local: covered by Task 6.
- raw prompt/response debug artifacts: covered by Task 2 and Task 6.
- fail-fast empty digest behavior: covered by Task 4 and Task 5.

### Placeholder scan

- No `TODO`, `TBD`, or “similar to previous task” shortcuts remain.
- All code-changing tasks include concrete file paths, code snippets, commands, and commit messages.

### Type consistency

- The plan uses `Story`, `StoryFrame`, `StoryArticle`, `StoryFacet`, and `DigestStory` consistently.
- The plan uses `StoryClusteringService`, `StoryFacetAssignmentService`, `DigestPackagingService`, `DigestReportWritingService`, and `DigestGenerationService` consistently.
- The only batch stage names used are `story` and `digest`.
