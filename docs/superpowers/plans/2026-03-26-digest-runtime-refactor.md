# Digest Runtime Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `story` pipeline with the new `article -> article_event_frame -> strict_story -> digest` pipeline, add the single-machine `Postgres + Redis + Celery` runtime, and ship a same-day review script that emits manual-review-ready digest artifacts.

**Architecture:** This is one backend refactor, not two independent projects. The content model redesign and the runtime execution redesign meet in the same codepath: Postgres remains the only business truth, Redis is only broker/coordination state, front stages run as object-level Celery tasks, and `strict_story` packing plus `digest` generation remain business-day batch jobs. The plan intentionally removes story-era dual paths instead of preserving compatibility shims.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy ORM, PostgreSQL, Redis, Celery, aiohttp, Qdrant, `unittest`, `@python-code-style`, `@test-driven-development`, `@verification-before-completion`

---

## Scope Check

This plan intentionally covers both approved specs together because they are not independently shippable:

- `docs/superpowers/specs/2026-03-26-digest-story-pipeline-design.md`
- `docs/superpowers/specs/2026-03-26-runtime-execution-architecture-design.md`

Do not split this into a “schema plan” and a “runtime plan”. The new ORM contract, queue topology, coordinator logic, API contract, and today-review script all depend on the same pipeline semantics.

## File Structure

### Models and schema bootstrap

- Create: `backend/app/models/runtime.py`
  - `PipelineRun`
  - `SourceRunState`
  - run-level batch-stage state for `strict_story` packing and `digest` generation
- Create: `backend/app/models/event_frame.py`
  - `ArticleEventFrame`
- Create: `backend/app/models/strict_story.py`
  - `StrictStory`
  - `StrictStoryFrame`
  - `StrictStoryArticle`
- Create: `backend/app/models/digest.py`
  - `Digest`
  - `DigestStrictStory`
  - `DigestArticle`
- Modify: `backend/app/models/article.py`
  - replace story-era enrichment fields with parse/event-frame runtime state
  - keep original truth-source article metadata and source markdown path
- Modify: `backend/app/models/image.py`
  - keep source-text truth fields used by image retrieval
  - stop documenting visual analysis as a required runtime stage
- Modify: `backend/app/models/__init__.py`
  - export the new model registry
- Modify: `backend/app/models/README.md`
  - update the documented backend data model
- Delete: `backend/app/models/story.py`
  - remove the old public read model instead of extending it

### LLM prompt and schema contracts

- Create: `backend/app/prompts/event_frame_extraction_prompt.py`
- Create: `backend/app/prompts/strict_story_tiebreak_prompt.py`
- Create: `backend/app/prompts/digest_generation_prompt.py`
- Create: `backend/app/schemas/llm/event_frame_extraction.py`
- Create: `backend/app/schemas/llm/strict_story_tiebreak.py`
- Create: `backend/app/schemas/llm/digest_generation.py`
- Delete: `backend/app/prompts/article_enrichment_prompt.py`
- Delete: `backend/app/prompts/story_cluster_review_prompt.py`
- Delete: `backend/app/prompts/story_generation_prompt.py`
- Delete: `backend/app/prompts/image_analysis_prompt.py`
- Delete: `backend/app/schemas/llm/article_enrichment.py`
- Delete: `backend/app/schemas/llm/story_cluster_review.py`
- Delete: `backend/app/schemas/llm/story_generation.py`
- Delete: `backend/app/schemas/llm/image_analysis.py`
- Delete: `backend/app/schemas/llm/story_taxonomy.py`

### Content and runtime services

- Modify: `backend/app/service/article_collection_service.py`
  - add one-source runtime entrypoint that updates `SourceRunState`
  - allow reuse of an existing SQLAlchemy session inside worker tasks
- Modify: `backend/app/service/article_parse_service.py`
  - align parse stage status semantics with the new runtime contract
  - stop carrying visual-analysis retry semantics forward
- Create: `backend/app/service/event_frame_extraction_service.py`
  - sparse `0..3` event frames per article from parsed source markdown
- Create: `backend/app/service/strict_story_packing_service.py`
  - business-day event packing and `strict_story_key` reuse
- Create: `backend/app/service/digest_generation_service.py`
  - digest selection, `digest_key` reuse, persisted public body generation
- Create: `backend/app/service/llm_rate_limiter.py`
  - Redis-backed shared token/lease coordination
- Create: `backend/app/service/daily_run_coordinator_service.py`
  - run bootstrap, stage re-scan, stale reclaim, aggregation triggering
- Modify: `backend/app/service/RAG/article_rag_service.py`
  - index the full parsed corpus instead of the story-publishable subset
  - image retrieval uses source-provided text with multimodal image embeddings
- Modify: `backend/app/service/RAG/AGENTS.md`
  - replace story-era retrieval rules with digest/runtime rules
- Delete: `backend/app/service/article_enrichment_service.py`
- Delete: `backend/app/service/article_cluster_service.py`
- Delete: `backend/app/service/story_generation_service.py`
- Delete: `backend/app/service/image_analysis_service.py`
- Delete: `backend/app/service/scheduler_service.py`

### Runtime task wiring

- Modify: `backend/pyproject.toml`
  - add Celery dependency
- Create: `backend/app/config/celery_config.py`
- Create: `backend/app/tasks/__init__.py`
- Create: `backend/app/tasks/celery_app.py`
- Create: `backend/app/tasks/content_tasks.py`
- Create: `backend/app/tasks/aggregation_tasks.py`

### API layer

- Create: `backend/app/router/digest_router.py`
  - `/digests/feed`
  - `/digests/{digest_key}`
- Create: `backend/app/schemas/digest_feed.py`
- Modify: `backend/app/router/__init__.py`
  - export `digest_router`
- Modify: `backend/app/app_main.py`
  - register `digest_router`
- Delete: `backend/app/router/story_router.py`
- Delete: `backend/app/schemas/story_feed.py`

### Scripts and operational docs

- Create: `backend/app/scripts/run_celery_worker.py`
- Create: `backend/app/scripts/run_daily_coordinator.py`
- Create: `backend/app/scripts/dev_run_today_digest_pipeline.py`
- Modify: `backend/app/scripts/README.md`
- Modify: `backend/README.md`
- Delete: `backend/app/scripts/dev_rebuild_stories_for_date.py`

### Tests

- Create: `tests/test_digest_models.py`
- Create: `tests/test_source_collection_service.py`
- Create: `tests/test_article_parse_service.py`
- Create: `tests/test_event_frame_extraction_service.py`
- Create: `tests/test_strict_story_packing_service.py`
- Create: `tests/test_digest_generation_service.py`
- Create: `tests/test_digest_api.py`
- Create: `tests/test_article_rag_service.py`
- Create: `tests/test_content_tasks.py`
- Create: `tests/test_runtime_coordinator_service.py`
- Create: `tests/test_today_digest_pipeline_script.py`
- Create: `tests/test_digest_runtime_integration.py`
- Modify: `tests/test_pipeline_refactor.py`
  - keep only cross-cutting regression coverage still relevant after the refactor

## Runtime Contract

### Article stage-state fields

Use these exact `article` field families:

- parse
  - `parse_status`
  - `parse_attempts`
  - `parse_error`
  - `parse_updated_at`
- event frame extraction
  - `event_frame_status`
  - `event_frame_attempts`
  - `event_frame_error`
  - `event_frame_updated_at`

Do not keep `should_publish`, `reject_reason`, `cluster_text`, any `enrichment_*` runtime contract, or any article-level normalization / Chinese intermediate material on `article`.

Chinese generation belongs only in downstream prompts such as event-frame extraction output and final digest generation. The parse-stage truth source remains `markdown_rel_path`.

### Persisted object minimum fields

`article_event_frame` must persist at least:

- `event_frame_id`
- `article_id`
- `business_date`
- `event_type`
- `subject_json`
- `action_text`
- `object_text`
- `place_text`
- `collection_text`
- `season_text`
- `show_context_text`
- `evidence_json`
- `signature_json`
- `extraction_confidence`
- `extraction_status`
- `extraction_error`

`strict_story` must persist at least:

- `strict_story_key`
- `business_date`
- `synopsis_zh`
- `signature_json`
- `created_run_id`
- `packing_status`
- `packing_error`

`digest` must persist at least:

- `digest_key`
- `business_date`
- `facet`
- `title_zh`
- `dek_zh`
- `body_markdown`
- `hero_image_url`
- `source_article_count`
- `source_names_json`
- `created_run_id`
- `generation_status`
- `generation_error`

### Batch-stage fields on `pipeline_run`

Use dedicated `pipeline_run` columns for the only two batch stages:

- strict story packing
  - `strict_story_status`
  - `strict_story_attempts`
  - `strict_story_error`
  - `strict_story_updated_at`
- digest generation
  - `digest_status`
  - `digest_attempts`
  - `digest_error`
  - `digest_updated_at`

Also keep run-level counts and failure summaries in `pipeline_run.metadata_json`, updated by the coordinator and batch jobs after each tick or batch completion.

Do not invent a generic `run_stage_state` table. There are only two batch stages in scope, so explicit `pipeline_run` columns are the shortest correct path.

### Source collection state

Track per-run, per-source collection state in `source_run_state`:

- `run_id`
- `source_name`
- `status`
- `attempts`
- `error`
- `updated_at`
- `discovered_count`
- `inserted_count`

### Allowed status values

Use these exact values everywhere stage state appears:

- `pending`
- `queued`
- `running`
- `done`
- `failed`
- `abandoned`

### Retry rule

- maximum attempts per object-stage pair is `3`
- increment attempts only when the business operation actually starts and fails
- waiting for a rate-limit lease does not increment attempts
- after the third failed attempt, mark the stage `abandoned`

### Replacement rule

This refactor is replacement-only:

- delete the `story` read model
- delete story-era services, prompts, schemas, and scripts
- do not ship dual-read or dual-write codepaths
- if local schema bootstrap cannot safely preserve a story-era database, reset the local database instead of adding compatibility branches

## Implementation Tasks

### Task 1: Establish the New ORM and Schema Bootstrap Contract

**Files:**
- Create: `backend/app/models/runtime.py`
- Create: `backend/app/models/event_frame.py`
- Create: `backend/app/models/strict_story.py`
- Create: `backend/app/models/digest.py`
- Modify: `backend/app/models/article.py`
- Modify: `backend/app/models/image.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/README.md`
- Test: `tests/test_digest_models.py`

- [ ] **Step 1: Write the failing model-contract tests**

```python
from backend.app.models import (
    Article,
    ArticleEventFrame,
    Digest,
    DigestArticle,
    DigestStrictStory,
    PipelineRun,
    SourceRunState,
    StrictStory,
    StrictStoryArticle,
    StrictStoryFrame,
)


def test_article_stage_columns_match_digest_runtime_contract():
    expected = {
        "markdown_rel_path",
        "parse_status",
        "parse_attempts",
        "parse_error",
        "parse_updated_at",
        "event_frame_status",
        "event_frame_attempts",
        "event_frame_error",
        "event_frame_updated_at",
    }
    assert expected.issubset(Article.__table__.columns.keys())


def test_pipeline_run_owns_explicit_batch_stage_columns():
    expected = {
        "business_date",
        "strict_story_status",
        "strict_story_attempts",
        "strict_story_error",
        "strict_story_updated_at",
        "digest_status",
        "digest_attempts",
        "digest_error",
        "digest_updated_at",
    }
    assert expected.issubset(PipelineRun.__table__.columns.keys())


def test_new_digest_runtime_tables_replace_story_read_model():
    assert ArticleEventFrame.__tablename__ == "article_event_frame"
    assert StrictStoryFrame.__tablename__ == "strict_story_frame"
    assert DigestArticle.__tablename__ == "digest_article"
    assert SourceRunState.__tablename__ == "source_run_state"
```

- [ ] **Step 2: Run the model-contract test file to verify it fails**

Run: `python -m unittest -v tests.test_digest_models`
Expected: FAIL with `ImportError` or missing columns such as `event_frame_status` and `strict_story_status`

- [ ] **Step 3: Implement the new ORM files and replace the schema bootstrap**

```python
class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    run_id = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_date = mapped_column(Date, nullable=False, index=True)
    run_type = mapped_column(String(32), nullable=False, default="digest_daily", index=True)
    status = mapped_column(String(32), nullable=False, default="pending", index=True)
    strict_story_status = mapped_column(String(32), nullable=False, default="pending")
    strict_story_attempts = mapped_column(Integer, nullable=False, default=0)
    strict_story_error = mapped_column(Text, nullable=True)
    strict_story_updated_at = mapped_column(DateTime, nullable=False, default=_utcnow_naive)
    digest_status = mapped_column(String(32), nullable=False, default="pending")
    digest_attempts = mapped_column(Integer, nullable=False, default=0)
    digest_error = mapped_column(Text, nullable=True)
    digest_updated_at = mapped_column(DateTime, nullable=False, default=_utcnow_naive)
    started_at = mapped_column(DateTime, nullable=False, default=_utcnow_naive)
    finished_at = mapped_column(DateTime, nullable=True)
    metadata_json = mapped_column(JSON, nullable=False, default=dict)


class SourceRunState(Base):
    __tablename__ = "source_run_state"

    run_id = mapped_column(ForeignKey("pipeline_run.run_id", ondelete="CASCADE"), primary_key=True)
    source_name = mapped_column(String(120), primary_key=True)
    status = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempts = mapped_column(Integer, nullable=False, default=0)
    error = mapped_column(Text, nullable=True)
    updated_at = mapped_column(DateTime, nullable=False, default=_utcnow_naive)
    discovered_count = mapped_column(Integer, nullable=False, default=0)
    inserted_count = mapped_column(Integer, nullable=False, default=0)


def ensure_article_storage_schema(bind: Engine) -> None:
    from backend.app.models.digest import Digest, DigestArticle, DigestStrictStory
    from backend.app.models.event_frame import ArticleEventFrame
    from backend.app.models.image import ArticleImage
    from backend.app.models.runtime import PipelineRun, SourceRunState
    from backend.app.models.strict_story import StrictStory, StrictStoryArticle, StrictStoryFrame

    _drop_story_tables(bind)
    Base.metadata.create_all(bind=bind)
    _ensure_article_columns(bind)
```

- [ ] **Step 4: Run the model-contract tests again**

Run: `python -m unittest -v tests.test_digest_models`
Expected: PASS

- [ ] **Step 5: Commit the schema foundation**

```bash
git add backend/app/models/article.py backend/app/models/image.py backend/app/models/runtime.py backend/app/models/event_frame.py backend/app/models/strict_story.py backend/app/models/digest.py backend/app/models/__init__.py backend/app/models/README.md tests/test_digest_models.py
git commit -m "feat: add digest runtime schema contract"
```

### Task 2: Add One-Source Collection Execution Backed by `SourceRunState`

**Files:**
- Modify: `backend/app/service/article_collection_service.py`
- Modify: `backend/app/models/runtime.py`
- Test: `tests/test_source_collection_service.py`

- [ ] **Step 1: Write the failing source-collection tests**

```python
def test_collect_source_persists_articles_and_marks_source_done():
    result = asyncio.run(
        service.collect_source(
            session,
            run_id="run-1",
            source_name="Vogue",
        )
    )
    assert result.inserted == 2
    state = session.get(SourceRunState, {"run_id": "run-1", "source_name": "Vogue"})
    assert state.status == "done"
    assert state.inserted_count == 2


def test_collect_source_dedupes_by_canonical_url_inside_one_run():
    result = asyncio.run(service.collect_source(session, run_id="run-1", source_name="Vogue"))
    assert result.skipped_in_batch == 1


def test_collect_source_abandons_after_third_failure():
    state = SourceRunState(run_id="run-1", source_name="Vogue", status="failed", attempts=2)
    session.add(state)
    session.commit()
    with self.assertRaises(RuntimeError):
        asyncio.run(service.collect_source(session, run_id="run-1", source_name="Vogue"))
    refreshed = session.get(SourceRunState, {"run_id": "run-1", "source_name": "Vogue"})
    assert refreshed.status == "abandoned"
```

- [ ] **Step 2: Run the source-collection test file to verify it fails**

Run: `python -m unittest -v tests.test_source_collection_service`
Expected: FAIL because `ArticleCollectionService` does not yet expose `collect_source(...)` or update `SourceRunState`

- [ ] **Step 3: Add a one-source collection path that updates `SourceRunState`**

```python
class ArticleCollectionService:
    async def collect_source(
        self,
        session: Session,
        *,
        run_id: str,
        source_name: str,
    ) -> CollectionResult:
        state = self._get_or_create_source_state(session, run_id=run_id, source_name=source_name)
        if state.attempts >= 3:
            state.status = "abandoned"
            session.flush()
            raise RuntimeError(f"source already exhausted retries: {source_name}")

        try:
            collected = await self._collector.collect_articles(source_names=[source_name], limit_sources=1)
            result = self.store_articles(collected, session=session)
            state.status = "done"
            state.error = None
            state.discovered_count = result.total_collected
            state.inserted_count = result.inserted
            state.updated_at = _utcnow_naive()
            session.flush()
            return result
        except Exception as exc:
            state.attempts += 1
            state.status = "abandoned" if state.attempts >= 3 else "failed"
            state.error = f"{exc.__class__.__name__}: {exc}"
            state.updated_at = _utcnow_naive()
            session.flush()
            raise
```

- [ ] **Step 4: Run the source-collection tests again**

Run: `python -m unittest -v tests.test_source_collection_service`
Expected: PASS

- [ ] **Step 5: Commit the source-collection runtime path**

```bash
git add backend/app/service/article_collection_service.py backend/app/models/runtime.py tests/test_source_collection_service.py
git commit -m "feat: add source run state backed collection path"
```

### Task 3: Align the Parse Stage with the New Runtime Contract

**Files:**
- Modify: `backend/app/service/article_parse_service.py`
- Modify: `backend/app/models/article.py`
- Test: `tests/test_article_parse_service.py`

- [ ] **Step 1: Write the failing parse-stage tests**

```python
def test_parse_article_updates_parse_updated_at_instead_of_story_era_fields():
    result = asyncio.run(service.parse_articles(article_ids=["article-1"]))
    assert result.parsed == 1
    article = reload_article("article-1")
    assert article.parse_status == "done"
    assert article.parse_updated_at is not None


def test_parse_failure_abandons_after_third_attempt():
    article = make_article("article-1", parse_status="failed", parse_attempts=2)
    persist(article)
    result = service._persist_outcomes([("article-1", None, RuntimeError("parse boom"))])
    assert result.failed == 1
    refreshed = reload_article("article-1")
    assert refreshed.parse_status == "abandoned"
    assert refreshed.parse_attempts == 3
```

- [ ] **Step 2: Run the parse-stage tests to verify they fail**

Run: `python -m unittest -v tests.test_article_parse_service`
Expected: FAIL because `ArticleParseService` still writes `parsed_at` and does not expose the final runtime field names

- [ ] **Step 3: Refactor `ArticleParseService` to use the runtime stage fields**

```python
if error is not None or parsed is None:
    stored.parse_attempts += 1
    stored.parse_status = "abandoned" if stored.parse_attempts >= MAX_PARSE_ATTEMPTS else "failed"
    stored.parse_error = _format_error(error)
    stored.parse_updated_at = _utcnow_naive()
    failed_count += 1
    continue

stored.markdown_rel_path = relative_path
stored.hero_image_id = hero_image_id
stored.parse_status = "done"
stored.parse_error = None
stored.parse_updated_at = _utcnow_naive()
parsed_count += 1
```

- [ ] **Step 4: Run the parse-stage tests again**

Run: `python -m unittest -v tests.test_article_parse_service`
Expected: PASS

- [ ] **Step 5: Commit the parse-stage refactor**

```bash
git add backend/app/service/article_parse_service.py backend/app/models/article.py tests/test_article_parse_service.py
git commit -m "refactor: align parse stage with runtime contract"
```

### Task 4: Remove Article-Level Normalization Assumptions

**Files:**
- Modify: `backend/app/models/article.py`
- Modify: `backend/app/models/README.md`
- Modify: `backend/app/service/RAG/article_rag_service.py`
- Modify: `backend/app/service/RAG/query_service.py`
- Test: `tests/test_digest_models.py`
- Test: `tests/test_article_rag_service.py`
- Delete: `backend/app/service/business_day_service.py`
- Delete: `backend/app/prompts/article_normalization_prompt.py`
- Delete: `backend/app/schemas/llm/article_normalization.py`
- Delete: `backend/app/service/article_normalization_service.py`
- Delete: `tests/test_business_day_service.py`
- Delete: `tests/test_article_normalization_service.py`

- [ ] **Step 1: Write the failing no-normalization pivot tests**

```python
def test_article_contract_excludes_normalization_and_article_level_chinese_fields():
    assert "normalization_status" not in Article.__table__.columns.keys()
    assert "title_zh" not in Article.__table__.columns.keys()
    assert "summary_zh" not in Article.__table__.columns.keys()
    assert "body_zh_rel_path" not in Article.__table__.columns.keys()


def test_rag_text_lane_reads_markdown_rel_path_directly():
    result = service.upsert_articles(["article-1"])
    assert result.eligible_articles == 1
    assert read_markdown.call_args.kwargs["relative_path"] == "2026-03-26/article-1.md"
```

- [ ] **Step 2: Run the focused pivot tests to verify they fail**

Run: `python -m unittest -v tests.test_digest_models tests.test_article_rag_service`
Expected: FAIL because the repo still models normalization-era fields or still reads `body_zh_rel_path`

- [ ] **Step 3: Remove normalization-stage files and article-level Chinese intermediate fields**

```python
class Article(Base):
    __tablename__ = "article"
    # keep only truth-source fields plus parse/event-frame runtime state


def _fail_on_legacy_article_columns(bind: Engine) -> None:
    if existing_columns & LEGACY_ARTICLE_COLUMNS:
        raise RuntimeError("article table contains legacy normalization-era columns; reset local runtime DB state before bootstrap")
```

- [ ] **Step 4: Rebase RAG on parse outputs**

```python
eligible_articles = [
    article
    for article in articles
    if article.parse_status == "done"
]

markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
```

Keep this exact image-lane rule:

- retrieval content uses source-text image signals only
- dense embedding remains multimodal and may still consume `image_url`
- no OCR / observed description / contextual interpretation in retrieval content

- [ ] **Step 5: Run the focused pivot tests again**

Run: `python -m unittest -v tests.test_digest_models tests.test_article_rag_service`
Expected: PASS

- [ ] **Step 6: Commit the no-normalization pivot**

```bash
git add backend/app/models/article.py backend/app/models/README.md backend/app/service/RAG/article_rag_service.py backend/app/service/RAG/query_service.py tests/test_digest_models.py tests/test_article_rag_service.py
git rm backend/app/service/business_day_service.py backend/app/prompts/article_normalization_prompt.py backend/app/schemas/llm/article_normalization.py backend/app/service/article_normalization_service.py tests/test_business_day_service.py tests/test_article_normalization_service.py
git commit -m "refactor: remove article normalization persistence"
```

### Task 5: Add Sparse Event Frame Extraction

**Files:**
- Create: `backend/app/prompts/event_frame_extraction_prompt.py`
- Create: `backend/app/schemas/llm/event_frame_extraction.py`
- Create: `backend/app/service/event_frame_extraction_service.py`
- Modify: `backend/app/models/event_frame.py`
- Modify: `backend/app/models/article.py`
- Test: `tests/test_event_frame_extraction_service.py`

- [ ] **Step 1: Write the failing event-frame tests**

```python
def test_extract_event_frames_caps_output_at_three():
    frames = asyncio.run(service.extract_frames(session, article))
    assert len(frames) == 3
    assert article.event_frame_status == "done"


def test_zero_frames_is_a_valid_done_state():
    frames = asyncio.run(service.extract_frames(session, article))
    assert frames == ()
    assert article.event_frame_status == "done"


def test_event_frame_failure_becomes_abandoned_after_three_attempts():
    article.event_frame_attempts = 2
    result = asyncio.run(service.extract_frames(session, article))
    assert result == ()
    assert article.event_frame_status == "abandoned"
```

- [ ] **Step 2: Run the event-frame tests to verify they fail**

Run: `python -m unittest -v tests.test_event_frame_extraction_service`
Expected: FAIL with `ModuleNotFoundError` or missing `event_frame_status`

- [ ] **Step 3: Implement the extraction prompt/schema contract**

```python
class ExtractedEventFrame(BaseModel):
    event_type: str
    subject_json: dict = Field(default_factory=dict)
    action_text: str = ""
    object_text: str = ""
    place_text: str | None = None
    collection_text: str | None = None
    season_text: str | None = None
    show_context_text: str | None = None
    evidence_json: list[dict] = Field(default_factory=list)
    signature_json: dict = Field(default_factory=dict)
    extraction_confidence: float


class EventFrameExtractionSchema(BaseModel):
    frames: list[ExtractedEventFrame] = Field(default_factory=list)
```

- [ ] **Step 4: Implement sparse per-article extraction**

```python
class EventFrameExtractionService:
    async def extract_frames(self, session: Session, article: Article) -> tuple[ArticleEventFrame, ...]:
        if article.parse_status != "done":
            raise ValueError(f"parse must be done before frame extraction: {article.article_id}")
        if article.event_frame_attempts >= 3:
            article.event_frame_status = "abandoned"
            article.event_frame_updated_at = _utcnow_naive()
            session.flush()
            return ()

        try:
            payload = await self._infer_frames(article)
            frames = tuple(self._build_frame(article, frame) for frame in payload.frames[:3])
            session.execute(delete(ArticleEventFrame).where(ArticleEventFrame.article_id == article.article_id))
            session.add_all(frames)
            article.event_frame_status = "done"
            article.event_frame_error = None
        except Exception as exc:
            article.event_frame_attempts += 1
            article.event_frame_status = "abandoned" if article.event_frame_attempts >= 3 else "failed"
            article.event_frame_error = f"{exc.__class__.__name__}: {exc}"
            article.event_frame_updated_at = _utcnow_naive()
            session.flush()
            return ()

        article.event_frame_updated_at = _utcnow_naive()
        session.flush()
        return frames
```

- [ ] **Step 5: Run the event-frame tests again**

Run: `python -m unittest -v tests.test_event_frame_extraction_service`
Expected: PASS

- [ ] **Step 6: Commit sparse frame extraction**

```bash
git add backend/app/prompts/event_frame_extraction_prompt.py backend/app/schemas/llm/event_frame_extraction.py backend/app/service/event_frame_extraction_service.py backend/app/models/event_frame.py backend/app/models/article.py tests/test_event_frame_extraction_service.py
git commit -m "feat: add sparse event frame extraction"
```

### Task 6: Implement `strict_story` Packing for One Business Day

**Files:**
- Create: `backend/app/prompts/strict_story_tiebreak_prompt.py`
- Create: `backend/app/schemas/llm/strict_story_tiebreak.py`
- Create: `backend/app/service/strict_story_packing_service.py`
- Modify: `backend/app/models/strict_story.py`
- Test: `tests/test_strict_story_packing_service.py`

- [ ] **Step 1: Write the failing strict-story packing tests**

```python
def test_pack_day_groups_frames_into_strict_stories():
    stories = asyncio.run(service.pack_business_day(session, business_day, run_id="run-1"))
    assert len(stories) == 2


def test_pack_day_reuses_strict_story_key_when_signature_and_membership_match():
    first = asyncio.run(service.pack_business_day(session, business_day, run_id="run-1"))
    second = asyncio.run(service.pack_business_day(session, business_day, run_id="run-1"))
    assert [item.strict_story_key for item in first] == [item.strict_story_key for item in second]


def test_rerun_removes_stale_strict_story_rows_for_same_day():
    first = asyncio.run(service.pack_business_day(session, business_day, run_id="run-1"))
    delete_one_frame(session, business_day)
    second = asyncio.run(service.pack_business_day(session, business_day, run_id="run-1"))
    assert len(second) < len(first)
    assert no_stale_strict_story_rows_remain(session, business_day)
```

- [ ] **Step 2: Run the strict-story packing tests to verify they fail**

Run: `python -m unittest -v tests.test_strict_story_packing_service`
Expected: FAIL with `ModuleNotFoundError: backend.app.service.strict_story_packing_service`

- [ ] **Step 3: Implement the tie-break prompt/schema**

```python
class StrictStoryTieBreakChoice(BaseModel):
    reuse_strict_story_key: str | None = None
    synopsis_zh: str


class StrictStoryTieBreakSchema(BaseModel):
    choice: StrictStoryTieBreakChoice
```

- [ ] **Step 4: Implement business-day strict-story packing**

```python
class StrictStoryPackingService:
    async def pack_business_day(self, session: Session, business_day: date, *, run_id: str) -> list[StrictStory]:
        frames = self._load_day_frames(session, business_day)
        candidate_groups = self._group_by_signature(frames)
        existing = self._load_existing_stories(session, business_day)
        resolved = await self._resolve_story_keys(candidate_groups, existing)
        self._replace_day_rows(session, business_day, run_id=run_id, resolved=resolved)
        return resolved
```

Use this conservative first-pass `strict_story_key` reuse rule:

- only compare groups with compatible signature payloads
- among those, choose the existing story with the highest frame-membership overlap ratio
- reuse the key directly only when overlap ratio is `>= 0.5`
- if multiple candidates remain plausible, use the tie-break prompt
- otherwise mint a new `strict_story_key`

- [ ] **Step 5: Run the strict-story packing tests again**

Run: `python -m unittest -v tests.test_strict_story_packing_service`
Expected: PASS

- [ ] **Step 6: Commit strict-story packing**

```bash
git add backend/app/prompts/strict_story_tiebreak_prompt.py backend/app/schemas/llm/strict_story_tiebreak.py backend/app/service/strict_story_packing_service.py backend/app/models/strict_story.py tests/test_strict_story_packing_service.py
git commit -m "feat: add strict story packing"
```

### Task 7: Generate Digests and Replace the Public Read API

**Files:**
- Create: `backend/app/prompts/digest_generation_prompt.py`
- Create: `backend/app/schemas/llm/digest_generation.py`
- Create: `backend/app/service/digest_generation_service.py`
- Create: `backend/app/router/digest_router.py`
- Create: `backend/app/schemas/digest_feed.py`
- Modify: `backend/app/router/__init__.py`
- Modify: `backend/app/app_main.py`
- Modify: `backend/app/models/digest.py`
- Test: `tests/test_digest_generation_service.py`
- Test: `tests/test_digest_api.py`
- Delete: `backend/app/router/story_router.py`
- Delete: `backend/app/schemas/story_feed.py`

- [ ] **Step 1: Write the failing digest-generation tests**

```python
def test_generate_digests_persists_body_markdown_and_memberships():
    digests = asyncio.run(service.generate_for_day(session, business_day, run_id="run-1"))
    assert len(digests) == 2
    assert all(digest.body_markdown for digest in digests)


def test_generate_digests_reuses_digest_key_for_same_facet_and_members():
    first = asyncio.run(service.generate_for_day(session, business_day, run_id="run-1"))
    second = asyncio.run(service.generate_for_day(session, business_day, run_id="run-1"))
    assert [item.digest_key for item in first] == [item.digest_key for item in second]


def test_rerun_removes_stale_digests_for_same_day():
    first = asyncio.run(service.generate_for_day(session, business_day, run_id="run-1"))
    delete_one_digest_candidate(session, business_day)
    second = asyncio.run(service.generate_for_day(session, business_day, run_id="run-1"))
    assert len(second) < len(first)
    assert no_stale_digest_rows_remain(session, business_day)
```

- [ ] **Step 2: Run the digest-generation tests to verify they fail**

Run: `python -m unittest -v tests.test_digest_generation_service`
Expected: FAIL with `ModuleNotFoundError: backend.app.service.digest_generation_service`

- [ ] **Step 3: Write the failing digest API tests**

```python
def test_digest_feed_returns_public_cards_only():
    response = client.get("/api/v1/digests/feed")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["digests"][0]) >= {
        "id",
        "facet",
        "title",
        "dek",
        "image",
        "published",
        "article_count",
        "source_count",
        "source_names",
    }
    assert "topics" not in payload


def test_digest_detail_returns_flattened_sources_without_strict_story_internals():
    response = client.get("/api/v1/digests/digest-1")
    payload = response.json()
    assert set(payload) >= {
        "id",
        "facet",
        "title",
        "dek",
        "body_markdown",
        "hero_image",
        "published",
        "sources",
    }
    assert "strict_stories" not in payload
```

- [ ] **Step 4: Run the digest API tests to verify they fail**

Run: `python -m unittest -v tests.test_digest_api`
Expected: FAIL because `/api/v1/digests/...` is not yet registered

- [ ] **Step 5: Implement the digest prompt/schema and generation service**

```python
class DigestPlan(BaseModel):
    facet: str
    strict_story_keys: list[str] = Field(default_factory=list)
    title_zh: str
    dek_zh: str
    body_markdown: str


class DigestGenerationSchema(BaseModel):
    digests: list[DigestPlan] = Field(default_factory=list)


class DigestGenerationService:
    async def generate_for_day(self, session: Session, business_day: date, *, run_id: str) -> list[Digest]:
        strict_stories = self._load_day_strict_stories(session, business_day)
        plans = await self._select_digest_plans(strict_stories)
        return self._replace_day_digests(session, business_day, run_id=run_id, plans=plans)
```

Use this conservative first-pass `digest_key` reuse rule:

- compare digests only within the same `business_day` and `facet`
- treat “nearly same membership set” as exact same `strict_story` membership set in v1
- if membership set changes, mint a new `digest_key`
- replace same-day digest rows wholesale so stale rows are removed

- [ ] **Step 6: Implement the public digest router and swap `app_main.py`**

```python
router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("/feed", response_model=DigestFeedResponse)
async def get_digest_feed(db: Session = Depends(get_db)) -> DigestFeedResponse:
    return build_digest_feed_response(db)


@router.get("/{digest_key}", response_model=DigestDetailResponse)
async def get_digest_detail(digest_key: str, db: Session = Depends(get_db)) -> DigestDetailResponse:
    return build_digest_detail_response(db, digest_key=digest_key)
```

- [ ] **Step 7: Run the digest-generation and digest API tests again**

Run: `python -m unittest -v tests.test_digest_generation_service tests.test_digest_api`
Expected: PASS

- [ ] **Step 8: Commit digest generation and API replacement**

```bash
git add backend/app/prompts/digest_generation_prompt.py backend/app/schemas/llm/digest_generation.py backend/app/service/digest_generation_service.py backend/app/router/digest_router.py backend/app/schemas/digest_feed.py backend/app/router/__init__.py backend/app/app_main.py backend/app/models/digest.py tests/test_digest_generation_service.py tests/test_digest_api.py
git rm backend/app/router/story_router.py backend/app/schemas/story_feed.py
git commit -m "feat: add digest read model and api"
```

### Task 8: Refactor RAG to Index the Full Parsed Corpus and Remove Image2Text Dependency

**Files:**
- Modify: `backend/app/service/RAG/article_rag_service.py`
- Modify: `backend/app/service/RAG/AGENTS.md`
- Test: `tests/test_article_rag_service.py`
- Delete: `backend/app/service/image_analysis_service.py`

- [ ] **Step 1: Write the failing RAG tests**

```python
def test_upsert_articles_indexes_normalized_articles_not_publishable_subset():
    with patch.object(service._markdown_service, "read_markdown", wraps=service._markdown_service.read_markdown) as read_markdown:
        result = service.upsert_articles(["article-1", "article-2"])
    assert result.indexed_articles == 2
    assert result.text_units == 4
    assert read_markdown.call_args.kwargs["relative_path"] == "2026-03-26/article-1.md"


def test_image_retrieval_uses_source_text_without_visual_analysis():
    content = build_image_retrieval_content(article, image)
    assert "caption" in content
    assert image.caption_raw in content


def test_images_without_source_text_projection_are_skipped():
    image.caption_raw = ""
    image.alt_text = ""
    image.credit_raw = ""
    image.context_snippet = ""
    records = service._build_image_records([article])
    assert records == []
```

- [ ] **Step 2: Run the RAG tests to verify they fail**

Run: `python -m unittest -v tests.test_article_rag_service`
Expected: FAIL because `ArticleRagService` still filters on `should_publish`, still depends on `visual_status == "done"`, or still expects article-level normalization outputs

- [ ] **Step 3: Refactor the RAG text and image lanes**

```python
@dataclass(frozen=True)
class RagInsertResult:
    indexed_articles: int
    text_units: int
    image_units: int
    upserted_units: int


indexed_articles = [
    article
    for article in articles
    if article.parse_status == "done"
]

for article in indexed_articles:
    if not article.markdown_rel_path:
        raise ValueError(f"markdown_rel_path is required for RAG text lane: {article.article_id}")
    markdown = self._markdown_service.read_markdown(relative_path=article.markdown_rel_path)
    chunks = split_markdown_into_text_chunks(markdown, source_id=article.article_id)


for image in images:
    article = article_by_id.get(image.article_id)
    if article is None:
        continue
    if not has_image_text_projection(image):
        continue
```

- [ ] **Step 4: Update the RAG design doc to match the refactor**

```markdown
- `digest` 只服务阅读，不进入 RAG 真相层。
- shared collection 收录 `parse_status=done` 的 article。
- image lane 的 retrieval content 只使用 source-provided text，不依赖 image2text。
- image lane 的 dense embedding 仍然保留多模态 image embedding。
```

- [ ] **Step 5: Run the RAG tests again**

Run: `python -m unittest -v tests.test_article_rag_service`
Expected: PASS

- [ ] **Step 6: Commit the retrieval refactor**

```bash
git add backend/app/service/RAG/article_rag_service.py backend/app/service/RAG/AGENTS.md tests/test_article_rag_service.py
git rm backend/app/service/image_analysis_service.py
git commit -m "feat: index normalized corpus for rag"
```

### Task 9: Add Celery, Redis Rate Limiting, and Object-Level Content Tasks

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/config/celery_config.py`
- Create: `backend/app/service/llm_rate_limiter.py`
- Create: `backend/app/tasks/__init__.py`
- Create: `backend/app/tasks/celery_app.py`
- Create: `backend/app/tasks/content_tasks.py`
- Modify: `backend/app/service/article_parse_service.py`
- Modify: `backend/app/service/event_frame_extraction_service.py`
- Modify: `backend/app/service/strict_story_packing_service.py`
- Modify: `backend/app/service/digest_generation_service.py`
- Test: `tests/test_content_tasks.py`

- [ ] **Step 1: Write the failing Celery/runtime tests**

```python
def test_all_content_tasks_are_registered():
    assert sorted(name for name in celery_app.tasks if name.startswith("content.")) == [
        "content.collect_source",
        "content.extract_event_frames",
        "content.parse_article",
    ]


def test_parse_task_marks_article_done_in_eager_mode():
    celery_app.conf.task_always_eager = True
    content_tasks.parse_article.delay("article-1")
    article = session.get(Article, "article-1")
    assert article.parse_status == "done"


def test_rate_limit_wait_does_not_increment_attempts():
    limiter = FakeLimiter(block_first_n=2)
    asyncio.run(service.extract_frames(session, article))
    assert article.event_frame_attempts == 0
```

- [ ] **Step 2: Run the Celery/runtime tests to verify they fail**

Run: `python -m unittest -v tests.test_content_tasks`
Expected: FAIL with missing `celery_config`, `llm_rate_limiter`, or `content_tasks`

- [ ] **Step 3: Add Celery settings and a Redis-backed lease limiter**

```python
def build_celery_broker_url() -> str:
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


class LlmRateLimiter:
    @contextmanager
    def lease(self, bucket: str) -> Iterator[None]:
        token = self._acquire(bucket)
        try:
            yield
        finally:
            self._release(bucket, token)
```

- [ ] **Step 4: Wire the limiter into every LLM-backed stage**

```python
with self._rate_limiter.lease("event_frame_extraction"):
    response = await self._client.beta.chat.completions.parse(...)
```

- [ ] **Step 5: Add the object-level Celery tasks**

```python
@celery_app.task(name="content.collect_source")
def collect_source(source_name: str, run_id: str) -> None:
    run_collect_source(source_name=source_name, run_id=run_id)


@celery_app.task(name="content.parse_article")
def parse_article(article_id: str) -> None:
    run_parse_article(article_id=article_id)


@celery_app.task(name="content.extract_event_frames")
def extract_event_frames(article_id: str) -> None:
    run_extract_event_frames(article_id=article_id)
```

- [ ] **Step 6: Run the Celery/runtime tests again**

Run: `python -m unittest -v tests.test_content_tasks`
Expected: PASS

- [ ] **Step 7: Commit the content-task runtime scaffold**

```bash
git add backend/pyproject.toml backend/app/config/celery_config.py backend/app/service/llm_rate_limiter.py backend/app/tasks/__init__.py backend/app/tasks/celery_app.py backend/app/tasks/content_tasks.py backend/app/service/article_parse_service.py backend/app/service/event_frame_extraction_service.py backend/app/service/strict_story_packing_service.py backend/app/service/digest_generation_service.py tests/test_content_tasks.py
git commit -m "feat: add celery content tasks and llm limiter"
```

### Task 10: Implement the Daily Run Coordinator and Batch Trigger Logic

**Files:**
- Create: `backend/app/service/daily_run_coordinator_service.py`
- Create: `backend/app/tasks/aggregation_tasks.py`
- Modify: `backend/app/models/runtime.py`
- Test: `tests/test_runtime_coordinator_service.py`
- Delete: `backend/app/service/scheduler_service.py`

- [ ] **Step 1: Write the failing coordinator tests**

```python
def test_tick_creates_or_resumes_the_current_business_day_run():
    run_id = coordinator.tick(now=fixed_now)
    run = session.get(PipelineRun, run_id)
    assert run.business_date == date(2026, 3, 26)


def test_tick_requeues_retryable_failed_article_stages():
    article.parse_status = "failed"
    article.parse_attempts = 1
    coordinator.tick(now=fixed_now)
    assert queued_task_names == ["content.parse_article"]


def test_tick_reclaims_stale_running_rows_before_requeue():
    article.event_frame_status = "running"
    article.event_frame_updated_at = old_timestamp
    coordinator.tick(now=fixed_now)
    assert article.event_frame_status == "failed"
    assert article.event_frame_attempts == 1


def test_tick_enqueues_pack_then_generate_only_once_drained():
    coordinator.tick(now=fixed_now)
    assert queued_task_names[-1] == "aggregation.pack_strict_stories_for_day"
```

- [ ] **Step 2: Run the coordinator tests to verify they fail**

Run: `python -m unittest -v tests.test_runtime_coordinator_service`
Expected: FAIL with missing `DailyRunCoordinatorService`

- [ ] **Step 3: Implement run bootstrap and stale reclaim**

```python
class DailyRunCoordinatorService:
    def tick(self, *, now: datetime | None = None) -> str:
        business_day = business_day_for_runtime(now or datetime.now(UTC))
        run = self._ensure_run_for_day(business_day)
        self._reclaim_stale_source_states(run.run_id)
        self._reclaim_stale_article_states()
        self._enqueue_retryable_sources(run.run_id)
        self._enqueue_retryable_articles(stage="parse")
        self._enqueue_retryable_articles(stage="event_frame")
        self._refresh_run_metadata(run)
        self._enqueue_batch_jobs_if_ready(run)
        return run.run_id
```

- [ ] **Step 4: Implement batch-task gating with explicit `pipeline_run` state**

```python
def _enqueue_batch_jobs_if_ready(self, run: PipelineRun) -> None:
    if self._front_stages_drained(run):
        self._enqueue_unique_pack(run)
    if run.strict_story_status == "done" and run.digest_status in {"pending", "failed"}:
        self._enqueue_unique_digest(run)
```

- [ ] **Step 5: Run the coordinator tests again**

Run: `python -m unittest -v tests.test_runtime_coordinator_service`
Expected: PASS

- [ ] **Step 6: Commit the coordinator and batch tasks**

```bash
git add backend/app/service/daily_run_coordinator_service.py backend/app/tasks/aggregation_tasks.py backend/app/models/runtime.py tests/test_runtime_coordinator_service.py
git rm backend/app/service/scheduler_service.py
git commit -m "feat: add daily coordinator and batch triggers"
```

### Task 11: Add Runtime Scripts and the Today-Digest Review Bundle

**Files:**
- Create: `backend/app/scripts/run_celery_worker.py`
- Create: `backend/app/scripts/run_daily_coordinator.py`
- Create: `backend/app/scripts/dev_run_today_digest_pipeline.py`
- Modify: `backend/app/scripts/README.md`
- Test: `tests/test_today_digest_pipeline_script.py`

- [ ] **Step 1: Write the failing review-script tests**

```python
def test_dev_run_today_digest_pipeline_outputs_review_bundle():
    result = run_script("--skip-collect")
    assert result.exit_code == 0
    assert "review bundle:" in result.stdout
    assert (output_dir / "digests.json").exists()
    assert (output_dir / "articles.json").exists()
    assert (output_dir / "summary.md").exists()
```

- [ ] **Step 2: Run the review-script tests to verify they fail**

Run: `python -m unittest -v tests.test_today_digest_pipeline_script`
Expected: FAIL with missing `dev_run_today_digest_pipeline.py`

- [ ] **Step 3: Implement the worker and coordinator entry scripts**

```python
def main() -> None:
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--queues=content,aggregation",
        ]
    )
```

- [ ] **Step 4: Implement the same-day digest review script**

```python
async def main() -> None:
    business_day = business_day_for_runtime(datetime.now(UTC))
    with temporary_celery_eager_mode():
        coordinator = DailyRunCoordinatorService(source_names=args.source_names, limit_sources=args.limit_sources)
        run_id = coordinator.tick(now=datetime.now(UTC))
        coordinator.drain_until_idle(run_id=run_id, business_day=business_day, skip_collect=args.skip_collect)
    review_dir = write_review_bundle(
        business_day=business_day,
        digests=load_day_digests(business_day),
        articles=load_day_articles(business_day),
    )
    print(f"review bundle: {review_dir}")
```

- [ ] **Step 5: Document the new runtime commands and remove stale README entries**

```markdown
- `run_celery_worker.py`: 启动 Celery worker
- `run_daily_coordinator.py`: 启动单机 coordinator loop
- `dev_run_today_digest_pipeline.py`: 本地同步跑今天的 digest pipeline 并输出 review bundle
```

- [ ] **Step 6: Run the review-script tests again**

Run: `python -m unittest -v tests.test_today_digest_pipeline_script`
Expected: PASS

- [ ] **Step 7: Commit the runtime scripts**

```bash
git add backend/app/scripts/run_celery_worker.py backend/app/scripts/run_daily_coordinator.py backend/app/scripts/dev_run_today_digest_pipeline.py backend/app/scripts/README.md tests/test_today_digest_pipeline_script.py
git commit -m "feat: add digest runtime scripts"
```

### Task 12: Remove Story-Era Modules, Update Docs, and Verify the Refactor End-to-End

**Files:**
- Create: `tests/test_digest_runtime_integration.py`
- Modify: `backend/README.md`
- Modify: `backend/app/models/README.md`
- Modify: `tests/test_pipeline_refactor.py`
- Delete: `backend/app/models/story.py`
- Delete: `backend/app/service/article_enrichment_service.py`
- Delete: `backend/app/service/article_cluster_service.py`
- Delete: `backend/app/service/story_generation_service.py`
- Delete: `backend/app/prompts/article_enrichment_prompt.py`
- Delete: `backend/app/prompts/story_cluster_review_prompt.py`
- Delete: `backend/app/prompts/story_generation_prompt.py`
- Delete: `backend/app/prompts/image_analysis_prompt.py`
- Delete: `backend/app/schemas/llm/article_enrichment.py`
- Delete: `backend/app/schemas/llm/story_cluster_review.py`
- Delete: `backend/app/schemas/llm/story_generation.py`
- Delete: `backend/app/schemas/llm/image_analysis.py`
- Delete: `backend/app/schemas/llm/story_taxonomy.py`
- Delete: `backend/app/scripts/dev_rebuild_stories_for_date.py`

- [ ] **Step 1: Write the failing end-to-end integration test**

```python
def test_business_day_runtime_persists_digests_without_story_tables():
    result = run_seeded_business_day()
    assert result.digest_count == 1
    assert result.strict_story_count == 1
    assert result.pipeline_status == "done"
    assert "story" not in result.table_names
    assert "story_article" not in result.table_names
```

- [ ] **Step 2: Run the integration test to verify it fails**

Run: `python -m unittest -v tests.test_digest_runtime_integration`
Expected: FAIL while the runtime still imports story-era modules or still leaves story tables in schema bootstrap

- [ ] **Step 3: Update the backend docs to the new digest/runtime semantics**

```markdown
- `article` 是事实真相源
- `article_event_frame` 是最小可回放事件单元
- `strict_story` 只服务内部 event packing
- `digest` 是唯一 public read model
- Redis 只负责 broker、锁、rate limiting，不保存业务真相
```

- [ ] **Step 4: Delete the remaining story-era modules and shrink old regression coverage**

```python
from backend.app.router import digest_router

app.include_router(digest_router, prefix="/api/v1")
```

- [ ] **Step 5: Run the focused integration test again**

Run: `python -m unittest -v tests.test_digest_runtime_integration`
Expected: PASS

- [ ] **Step 6: Run the full automated verification suite**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: PASS

- [ ] **Step 7: Run the product-state review script and capture the artifact path**

Run: `python backend/app/scripts/dev_run_today_digest_pipeline.py --source Vogue --source WWD`
Expected:
- exit code `0`
- prints `review bundle: ...`
- writes `digests.json`, `articles.json`, and `summary.md`

- [ ] **Step 8: Commit the final cleanup**

```bash
git add backend/README.md backend/app/models/README.md tests/test_pipeline_refactor.py tests/test_digest_runtime_integration.py
git rm backend/app/models/story.py backend/app/service/article_enrichment_service.py backend/app/service/article_cluster_service.py backend/app/service/story_generation_service.py backend/app/prompts/article_enrichment_prompt.py backend/app/prompts/story_cluster_review_prompt.py backend/app/prompts/story_generation_prompt.py backend/app/prompts/image_analysis_prompt.py backend/app/schemas/llm/article_enrichment.py backend/app/schemas/llm/story_cluster_review.py backend/app/schemas/llm/story_generation.py backend/app/schemas/llm/image_analysis.py backend/app/schemas/llm/story_taxonomy.py backend/app/scripts/dev_rebuild_stories_for_date.py
git commit -m "refactor: replace story pipeline with digest runtime"
```

## Manual Product Review Checklist

Run this after Task 12, Step 7:

- [ ] Open `summary.md` in the review bundle and confirm digest count matches the day’s article volume.
- [ ] Read every digest body and verify it is a reader-facing article, not a bullet dump.
- [ ] Spot-check at least three digests against their backing article links for factual drift.
- [ ] Inspect one digest that merged multiple `strict_story` members and confirm the combination adds real reader value.
- [ ] Inspect one article that produced zero frames and confirm the zero-frame outcome is explainable from source content.
- [ ] If grouping quality is poor, inspect `articles.json` and `digests.json` before changing prompts or thresholds.

## Verification Commands

Use these commands during execution, especially before the final commit:

```bash
python -m unittest -v tests.test_digest_models
python -m unittest -v tests.test_source_collection_service
python -m unittest -v tests.test_article_parse_service
python -m unittest -v tests.test_event_frame_extraction_service
python -m unittest -v tests.test_strict_story_packing_service
python -m unittest -v tests.test_digest_generation_service tests.test_digest_api
python -m unittest -v tests.test_article_rag_service
python -m unittest -v tests.test_content_tasks
python -m unittest -v tests.test_runtime_coordinator_service
python -m unittest -v tests.test_today_digest_pipeline_script
python -m unittest -v tests.test_digest_runtime_integration
python -m unittest discover -s tests -p "test_*.py" -v
python -m py_compile backend/app/app_main.py backend/app/service/daily_run_coordinator_service.py backend/app/tasks/content_tasks.py backend/app/tasks/aggregation_tasks.py backend/app/scripts/dev_run_today_digest_pipeline.py
python backend/app/scripts/dev_run_today_digest_pipeline.py --source Vogue --source WWD
```

## Notes for the Implementer

- Use `@python-code-style` for every Python change in this plan.
- Keep Postgres as the only business truth source. Redis is broker, lease, and lock state only.
- Do not reintroduce `article.should_publish` as a front-stage gate.
- Do not reintroduce visual image analysis into the primary runtime.
- Do not reintroduce article-level normalization or persisted per-article Chinese intermediates.
- Do not turn `strict_story` packing or `digest` generation into object-level queue fan-out.
- Keep `strict_story_key` and `digest_key` reuse conservative in v1. Prefer minting a new key over reusing an ambiguous one.
- Delete story-era code instead of hiding it behind compatibility branches or feature flags.
