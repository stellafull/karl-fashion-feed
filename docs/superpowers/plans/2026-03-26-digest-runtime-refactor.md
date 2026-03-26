# Digest Runtime Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current story-centric pipeline with the new `article -> article_event_frame -> strict_story -> digest` model, add the single-machine `Postgres + Redis + Celery` runtime, and ship a today-article dev script that produces manual-review-ready digest outputs.

**Architecture:** This refactor is one subsystem, not two separate projects: the content-model spec and runtime-execution spec meet in the same pipeline. Front stages become object-level Celery tasks backed by Postgres stage state, while `strict_story` packing and `digest` generation remain business-day batch jobs. Product verification is treated as a first-class deliverable via a `backend/app/scripts/` entrypoint that runs against today’s newly collected articles and emits review artifacts for human evaluation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy ORM, PostgreSQL, Redis, Celery, aiohttp, Qdrant, `unittest`, `@python-code-style`, `@verification-before-completion`

---

## Scope Check

The two approved specs are tightly coupled and should be implemented in one plan:

- `docs/superpowers/specs/2026-03-26-digest-story-pipeline-design.md`
- `docs/superpowers/specs/2026-03-26-runtime-execution-architecture-design.md`

Do not split this into separate content-model and runtime plans. The data model, read model, queue topology, and today-review script all depend on each other.

## File Structure

### New ORM and runtime state files

- Create: `backend/app/models/runtime.py`
  - `PipelineRun`
  - `SourceRunState`
  - stage-level orchestration metadata used by the coordinator
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

### Existing ORM files to modify

- Modify: `backend/app/models/article.py`
  - add durable normalization fields
  - add exact stage state fields for parse/normalization/event-frame extraction
  - update `ensure_article_storage_schema()` to stop creating story-era tables and to create/alter digest/runtime tables and new stage-state columns
- Modify: `backend/app/models/image.py`
  - keep image truth-source fields
  - stop runtime dependence on visual-analysis completion
- Modify: `backend/app/models/__init__.py`
  - export new ORM models
- Delete: `backend/app/models/story.py`
  - replace story read-model semantics with digest/runtime models after migration

### New content services

- Create: `backend/app/service/business_day_service.py`
  - canonical `Asia/Shanghai` business-day helper
- Create: `backend/app/service/article_normalization_service.py`
  - durable Chinese normalization material, no publish gate
- Create: `backend/app/service/event_frame_extraction_service.py`
  - sparse `0..3` frame extraction per article
- Create: `backend/app/service/strict_story_packing_service.py`
  - `same event?` packing for one business day
- Create: `backend/app/service/digest_generation_service.py`
  - `same card?` digest writing and key reuse

### Existing services to modify or retire

- Modify: `backend/app/service/news_collection_service.py`
  - source task boundary helpers stay reusable for Celery
- Modify: `backend/app/service/article_parse_service.py`
  - align with stage-state and retry model
- Modify: `backend/app/service/RAG/article_rag_service.py`
  - index full collected corpus, not story-publishable subset
  - build image retrieval from source-provided text only
- Delete: `backend/app/service/article_enrichment_service.py`
  - replaced by normalization + downstream selection
- Delete: `backend/app/service/article_cluster_service.py`
  - replaced by strict-story packing
- Delete: `backend/app/service/story_generation_service.py`
  - replaced by digest generation
- Delete: `backend/app/service/image_analysis_service.py`
  - removed from primary runtime
- Delete: `backend/app/service/scheduler_service.py`
  - replaced by coordinator + Celery task topology

### New runtime files

- Create: `backend/app/config/celery_config.py`
  - Celery settings and broker wiring
- Create: `backend/app/service/llm_rate_limiter.py`
  - Redis-backed token/lease coordination
- Create: `backend/app/service/daily_run_coordinator_service.py`
  - run control plane, stage re-scan, batch triggers
- Create: `backend/app/tasks/celery_app.py`
  - Celery application bootstrap
- Create: `backend/app/tasks/content_tasks.py`
  - object-level Celery tasks for collect/parse/normalize/extract
- Create: `backend/app/tasks/aggregation_tasks.py`
  - business-day batch jobs for strict-story packing and digest generation

### New API files

- Create: `backend/app/router/digest_router.py`
  - `/digests/feed`
  - `/digests/{digest_key}`
- Create: `backend/app/schemas/digest_feed.py`
  - digest feed/detail DTOs
- Modify: `backend/app/app_main.py`
  - swap `story_router` out for `digest_router`
- Delete: `backend/app/router/story_router.py`
- Delete: `backend/app/schemas/story_feed.py`

### Scripts

- Create: `backend/app/scripts/run_celery_worker.py`
  - local worker entrypoint
- Create: `backend/app/scripts/run_daily_coordinator.py`
  - single-machine coordinator entrypoint
- Create: `backend/app/scripts/dev_run_today_digest_pipeline.py`
  - collect today’s new articles
  - run the refactored pipeline against them
  - emit manual review artifacts for digests
- Modify: `backend/app/scripts/README.md`
  - remove nonexistent story-era script references
  - document new runtime and today-review scripts

### Tests

- Create: `tests/test_digest_models.py`
- Create: `tests/test_article_normalization_service.py`
- Create: `tests/test_business_day_service.py`
- Create: `tests/test_event_frame_extraction_service.py`
- Create: `tests/test_strict_story_packing_service.py`
- Create: `tests/test_digest_generation_service.py`
- Create: `tests/test_digest_api.py`
- Create: `tests/test_runtime_coordinator_service.py`
- Create: `tests/test_content_tasks.py`
- Create: `tests/test_today_digest_pipeline_script.py`
- Modify: `tests/test_pipeline_refactor.py`
  - retire story-era expectations
  - keep reusable runtime retry/regression coverage

## Implementation Tasks

## Stage State Contract

Use these exact `article` field families across Tasks 1, 2, 3, 7, and 8:

- parse:
  - `parse_status`
  - `parse_attempts`
  - `parse_error`
  - `parse_updated_at`
- normalization:
  - `normalization_status`
  - `normalization_attempts`
  - `normalization_error`
  - `normalization_updated_at`
- event frame extraction:
  - `event_frame_status`
  - `event_frame_attempts`
  - `event_frame_error`
  - `event_frame_updated_at`

Use these exact status values:

- `pending`
- `queued`
- `running`
- `done`
- `failed`
- `abandoned`

Attempts increment only when the business operation actually starts and fails.
Rate-limit waiting by itself must not increment attempts.

### Task 1: Establish the New ORM and Stage-State Foundation

**Files:**
- Create: `backend/app/models/runtime.py`
- Create: `backend/app/models/event_frame.py`
- Create: `backend/app/models/strict_story.py`
- Create: `backend/app/models/digest.py`
- Modify: `backend/app/models/article.py`
- Modify: `backend/app/models/image.py`
- Modify: `backend/app/models/__init__.py`
- Test: `tests/test_digest_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
from backend.app.models import (
    Article,
    ArticleEventFrame,
    Digest,
    DigestArticle,
    DigestStrictStory,
    PipelineRun,
    RunStageState,
    SourceRunState,
    StrictStory,
    StrictStoryArticle,
    StrictStoryFrame,
)


def test_article_event_frame_belongs_to_one_strict_story():
    assert StrictStoryFrame.__tablename__ == "strict_story_frame"


def test_digest_relations_replace_story_read_model():
    assert DigestArticle.__tablename__ == "digest_article"


def test_pipeline_run_tracks_batch_stage_state():
    assert RunStageState.__tablename__ == "run_stage_state"
```

- [ ] **Step 2: Run the model test file to verify it fails**

Run: `python -m unittest -v tests.test_digest_models`
Expected: FAIL with `ImportError` / missing model definitions such as `ArticleEventFrame` and `Digest`

- [ ] **Step 3: Implement the new ORM files and stage-state fields**

```python
class ArticleEventFrame(Base):
    __tablename__ = "article_event_frame"
    event_frame_id = mapped_column(String(36), primary_key=True)
    article_id = mapped_column(ForeignKey("article.article_id"), nullable=False, index=True)
    business_date = mapped_column(Date, nullable=False, index=True)
    event_type = mapped_column(Text, nullable=False)
    subject_json = mapped_column(JSON, nullable=False, default=dict)
    action_text = mapped_column(Text, nullable=False, default="")
    object_text = mapped_column(Text, nullable=False, default="")
    time_window_start = mapped_column(DateTime, nullable=True)
    time_window_end = mapped_column(DateTime, nullable=True)
    place_text = mapped_column(Text, nullable=True)
    collection_text = mapped_column(Text, nullable=True)
    season_text = mapped_column(Text, nullable=True)
    show_context_text = mapped_column(Text, nullable=True)
    evidence_json = mapped_column(JSON, nullable=False, default=list)
    signature_json = mapped_column(JSON, nullable=False, default=dict)
    extraction_confidence = mapped_column(Float, nullable=False, default=0.0)
    extraction_status = mapped_column(String(32), nullable=False, default="pending")
    extraction_error = mapped_column(Text, nullable=True)


class StrictStory(Base):
    __tablename__ = "strict_story"
    strict_story_key = mapped_column(String(36), primary_key=True)
    business_date = mapped_column(Date, nullable=False, index=True)
    synopsis_zh = mapped_column(Text, nullable=False)
    signature_json = mapped_column(JSON, nullable=False, default=dict)
    packing_status = mapped_column(String(32), nullable=False, default="pending")
    packing_error = mapped_column(Text, nullable=True)


class Digest(Base):
    __tablename__ = "digest"
    digest_key = mapped_column(String(36), primary_key=True)
    business_date = mapped_column(Date, nullable=False, index=True)
    facet = mapped_column(String(64), nullable=False, index=True)
    title_zh = mapped_column(Text, nullable=False)
    dek_zh = mapped_column(Text, nullable=False)
    body_markdown = mapped_column(Text, nullable=False)
    hero_image_url = mapped_column(Text, nullable=True)
    source_article_count = mapped_column(Integer, nullable=False, default=0)
    source_names_json = mapped_column(JSON, nullable=False, default=list)
    created_run_id = mapped_column(ForeignKey("pipeline_run.run_id"), nullable=False, index=True)
    generation_status = mapped_column(String(32), nullable=False, default="pending")
    generation_error = mapped_column(Text, nullable=True)


def ensure_article_storage_schema(bind: Engine) -> None:
    from backend.app.models.event_frame import ArticleEventFrame
    from backend.app.models.strict_story import StrictStory, StrictStoryArticle, StrictStoryFrame
    from backend.app.models.digest import Digest, DigestArticle, DigestStrictStory
    from backend.app.models.runtime import PipelineRun, RunStageState, SourceRunState
    Base.metadata.create_all(bind=bind)
    _ensure_article_stage_columns(bind)
```

- [ ] **Step 4: Run the model test file again**

Run: `python -m unittest -v tests.test_digest_models`
Expected: PASS

- [ ] **Step 5: Commit the schema foundation**

```bash
git add backend/app/models/article.py backend/app/models/image.py backend/app/models/runtime.py backend/app/models/event_frame.py backend/app/models/strict_story.py backend/app/models/digest.py backend/app/models/__init__.py tests/test_digest_models.py
git commit -m "feat: add digest pipeline models and stage state"
```

### Task 2: Replace Enrichment with Durable Article Normalization

**Files:**
- Create: `backend/app/service/business_day_service.py`
- Create: `backend/app/service/article_normalization_service.py`
- Modify: `backend/app/models/article.py`
- Test: `tests/test_business_day_service.py`
- Test: `tests/test_article_normalization_service.py`

- [ ] **Step 1: Write the failing business-day helper test**

```python
def test_business_day_uses_asia_shanghai_ingested_at():
    dt = datetime(2026, 3, 26, 0, 30, tzinfo=UTC)
    assert business_day_for_ingested_at(dt) == date(2026, 3, 26)
```

- [ ] **Step 2: Run the business-day helper test to verify it fails**

Run: `python -m unittest -v tests.test_business_day_service`
Expected: FAIL with `ModuleNotFoundError: backend.app.service.business_day_service`

- [ ] **Step 3: Write the failing normalization tests**

```python
def test_normalize_article_persists_durable_materials():
    article = make_article(parse_status="done", markdown_rel_path="2026-03-26/a.md")
    normalized = asyncio.run(service.normalize_article(session, article))
    assert normalized is True
    assert article.normalization_status == "done"
    assert article.title_zh
    assert article.summary_zh
    assert article.body_zh_rel_path


def test_normalization_abandons_after_third_failure():
    article = make_article(parse_status="done")
    article.normalization_attempts = 2
    result = asyncio.run(service.normalize_article(session, article))
    assert result is False
    assert article.normalization_status == "abandoned"
```

- [ ] **Step 4: Run normalization tests to verify they fail**

Run: `python -m unittest -v tests.test_article_normalization_service`
Expected: FAIL with `ModuleNotFoundError: backend.app.service.article_normalization_service`

- [ ] **Step 5: Implement the business-day helper and normalization service without publish gating**

```python
def business_day_for_ingested_at(value: datetime) -> date:
    return value.astimezone(ZoneInfo("Asia/Shanghai")).date()


class ArticleNormalizationService:
    async def normalize_article(self, session: Session, article: Article) -> bool:
        if article.normalization_status == "abandoned":
            return False
        if article.normalization_attempts >= 3:
            article.normalization_status = "abandoned"
            session.flush()
            return False
        result = await self._infer_normalized_material(article)
        article.title_zh = result.title_zh
        article.summary_zh = result.summary_zh
        article.body_zh_rel_path = self._write_body_markdown(article.article_id, result.body_zh)
        article.normalization_status = "done"
        session.flush()
        return True
```

- [ ] **Step 6: Run business-day and normalization tests again**

Run: `python -m unittest -v tests.test_business_day_service tests.test_article_normalization_service`
Expected: PASS

- [ ] **Step 7: Commit the helper and normalization**

```bash
git add backend/app/service/business_day_service.py backend/app/service/article_normalization_service.py backend/app/models/article.py tests/test_business_day_service.py tests/test_article_normalization_service.py
git commit -m "feat: add business day helper and article normalization"
```

### Task 3: Add Sparse Event Frame Extraction

**Files:**
- Create: `backend/app/service/event_frame_extraction_service.py`
- Modify: `backend/app/models/article.py`
- Modify: `backend/app/models/event_frame.py`
- Test: `tests/test_event_frame_extraction_service.py`

- [ ] **Step 1: Write the failing event-frame tests**

```python
def test_extract_event_frames_caps_output_at_three():
    frames = asyncio.run(service.extract_frames(session, article))
    assert len(frames) <= 3


def test_zero_frames_is_valid_done_state():
    result = asyncio.run(service.extract_frames(session, article))
    assert result == ()
    assert article.event_frame_status == "done"
```

- [ ] **Step 2: Run the event-frame tests to verify they fail**

Run: `python -m unittest -v tests.test_event_frame_extraction_service`
Expected: FAIL with `ModuleNotFoundError` or missing `event_frame_status`

- [ ] **Step 3: Implement sparse event-frame extraction**

```python
class EventFrameExtractionService:
    async def extract_frames(self, session: Session, article: Article) -> tuple[ArticleEventFrame, ...]:
        payload = await self._infer_frames(article)
        frames = tuple(payload.frames[:3])
        self._replace_article_frames(session, article.article_id, frames)
        article.event_frame_status = "done"
        session.flush()
        return tuple(frames)
```

- [ ] **Step 4: Run the event-frame tests again**

Run: `python -m unittest -v tests.test_event_frame_extraction_service`
Expected: PASS

- [ ] **Step 5: Commit sparse frame extraction**

```bash
git add backend/app/service/event_frame_extraction_service.py backend/app/models/article.py backend/app/models/event_frame.py tests/test_event_frame_extraction_service.py
git commit -m "feat: add sparse event frame extraction"
```

### Task 4: Implement Strict-Story Packing for One Business Day

**Files:**
- Create: `backend/app/service/strict_story_packing_service.py`
- Modify: `backend/app/models/strict_story.py`
- Modify: `backend/app/models/event_frame.py`
- Test: `tests/test_strict_story_packing_service.py`

- [ ] **Step 1: Write the failing strict-story packing tests**

```python
def test_pack_day_groups_frames_into_strict_stories():
    stories = asyncio.run(service.pack_business_day(session, business_day))
    assert len(stories) == 2


def test_reuses_strict_story_key_when_signature_and_membership_match():
    first = asyncio.run(service.pack_business_day(session, business_day))
    second = asyncio.run(service.pack_business_day(session, business_day))
    assert [item.strict_story_key for item in first] == [item.strict_story_key for item in second]


def test_rerun_removes_strict_stories_not_reproduced_for_same_day():
    first = asyncio.run(service.pack_business_day(session, business_day))
    delete_one_frame(session, business_day)
    second = asyncio.run(service.pack_business_day(session, business_day))
    assert len(second) < len(first)
    assert no_stale_strict_story_rows_remain(session, business_day)
```

- [ ] **Step 2: Run strict-story packing tests to verify they fail**

Run: `python -m unittest -v tests.test_strict_story_packing_service`
Expected: FAIL with `ModuleNotFoundError: backend.app.service.strict_story_packing_service`

- [ ] **Step 3: Implement business-day packing**

```python
class StrictStoryPackingService:
    async def pack_business_day(self, session: Session, business_day: date) -> list[StrictStory]:
        frames = self._load_day_frames(session, business_day)
        candidate_groups = self._group_by_signature(frames)
        existing = self._load_existing_strict_stories(session, business_day)
        matched = self._match_by_signature_then_membership(candidate_groups, existing)
        packed = await self._llm_tie_break_ambiguous_matches(matched)
        self._replace_day_strict_stories(session, business_day, packed)
        return packed
```

Use this conservative first-pass rule for `strict_story_key` reuse:

- only consider candidates with compatible event signatures
- among them, choose the existing strict story with the highest frame-membership overlap ratio
- reuse the key directly only when overlap ratio is `>= 0.5`
- if multiple candidates are still plausible, use LLM tie-break review
- otherwise mint a new `strict_story_key`

- [ ] **Step 4: Run strict-story packing tests again**

Run: `python -m unittest -v tests.test_strict_story_packing_service`
Expected: PASS

- [ ] **Step 5: Commit strict-story packing**

```bash
git add backend/app/service/strict_story_packing_service.py backend/app/models/strict_story.py backend/app/models/event_frame.py tests/test_strict_story_packing_service.py
git commit -m "feat: add strict story packing"
```

### Task 5: Generate Digests and Replace the Story Read API

**Files:**
- Create: `backend/app/service/digest_generation_service.py`
- Create: `backend/app/router/digest_router.py`
- Create: `backend/app/schemas/digest_feed.py`
- Modify: `backend/app/app_main.py`
- Modify: `backend/app/models/digest.py`
- Test: `tests/test_digest_generation_service.py`
- Test: `tests/test_digest_api.py`
- Delete: `backend/app/router/story_router.py`
- Delete: `backend/app/schemas/story_feed.py`

- [ ] **Step 1: Write the failing digest-generation and API tests**

```python
def test_generate_digest_allows_one_or_many_strict_stories():
    digests = asyncio.run(service.generate_for_day(session, business_day))
    assert all(digest.body_markdown for digest in digests)


def test_generate_digest_reuses_digest_key_for_same_facet_and_members():
    first = asyncio.run(service.generate_for_day(session, business_day))
    second = asyncio.run(service.generate_for_day(session, business_day))
    assert [item.digest_key for item in first] == [item.digest_key for item in second]


def test_rerun_removes_stale_digests_for_same_day():
    first = asyncio.run(service.generate_for_day(session, business_day))
    delete_one_strict_story_membership(session, business_day)
    second = asyncio.run(service.generate_for_day(session, business_day))
    assert len(second) < len(first)
    assert no_stale_digest_rows_remain(session, business_day)


def test_digest_feed_returns_public_digest_cards():
    response = client.get("/api/v1/digests/feed")
    assert response.status_code == 200
    payload = response.json()
    assert "topics" not in payload
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


def test_digest_detail_returns_full_public_contract_without_strict_story_internals():
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

- [ ] **Step 2: Run the digest tests to verify they fail**

Run: `python -m unittest -v tests.test_digest_generation_service tests.test_digest_api`
Expected: FAIL with missing `digest_router` and `DigestGenerationService`

- [ ] **Step 3: Implement digest generation and router swap**

```python
class DigestGenerationService:
    async def generate_for_day(self, session: Session, business_day: date) -> list[Digest]:
        strict_stories = self._load_strict_stories(session, business_day)
        plans = await self._select_digest_memberships(strict_stories)
        return await self._replace_day_digests(session, business_day, plans)


router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("/feed", response_model=DigestFeedResponse)
async def get_digest_feed(db: Session = Depends(get_db)) -> DigestFeedResponse:
    return build_digest_feed_response(db)
```

Use this conservative first-pass rule for `digest_key` reuse:

- only compare digests within the same `business_day` and `facet`
- treat “nearly same membership set” as exact same `strict_story` membership set in v1
- if membership set changes, mint a new `digest_key`
- replace same-day digest rows wholesale so stale digests are removed from current state

- [ ] **Step 4: Run the digest tests again**

Run: `python -m unittest -v tests.test_digest_generation_service tests.test_digest_api`
Expected: PASS

- [ ] **Step 5: Commit digest read-model replacement**

```bash
git add backend/app/service/digest_generation_service.py backend/app/router/digest_router.py backend/app/schemas/digest_feed.py backend/app/app_main.py backend/app/models/digest.py tests/test_digest_generation_service.py tests/test_digest_api.py
git rm backend/app/router/story_router.py backend/app/schemas/story_feed.py
git commit -m "feat: replace story read model with digests"
```

### Task 6: Refactor RAG to Index the Full Corpus and Remove Visual-Analysis Dependency

**Files:**
- Modify: `backend/app/service/RAG/article_rag_service.py`
- Modify: `backend/app/models/image.py`
- Test: `tests/test_pipeline_refactor.py`
- Delete: `backend/app/service/image_analysis_service.py`

- [ ] **Step 1: Write the failing RAG tests**

```python
def test_rag_upserts_normalized_articles_without_story_publish_gate():
    result = service.upsert_articles(["article-1"])
    assert result.indexed_articles == 1


def test_image_retrieval_uses_source_text_without_visual_analysis():
    content = build_image_retrieval_content(article, image)
    assert "caption" in content
    assert image.visual_status != "done"
```

- [ ] **Step 2: Run the RAG regression tests to verify they fail**

Run: `python -m unittest -v tests.test_pipeline_refactor`
Expected: FAIL because `ArticleRagService` still requires `should_publish` and `visual_status == "done"`

- [ ] **Step 3: Remove visual-analysis gating and reindex from source text**

```python
indexed_articles = [
    article
    for article in articles
    if article.normalization_status == "done" and article.parse_status == "done"
]


return RagInsertResult(
    indexed_articles=len(indexed_articles),
    text_units=len(text_records),
    image_units=len(image_records),
    upserted_units=upserted_units,
)


if not has_image_text_projection(image):
    continue
```

- [ ] **Step 4: Run the RAG regression tests again**

Run: `python -m unittest -v tests.test_pipeline_refactor`
Expected: PASS

- [ ] **Step 5: Commit the retrieval refactor**

```bash
git add backend/app/service/RAG/article_rag_service.py backend/app/models/image.py tests/test_pipeline_refactor.py
git rm backend/app/service/image_analysis_service.py
git commit -m "feat: index full corpus and remove image analysis dependency"
```

### Task 7: Add Celery, Redis Rate Limiting, and Object-Level Tasks

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/config/celery_config.py`
- Create: `backend/app/service/llm_rate_limiter.py`
- Create: `backend/app/tasks/celery_app.py`
- Create: `backend/app/tasks/content_tasks.py`
- Test: `tests/test_content_tasks.py`

- [ ] **Step 1: Write the failing Celery/runtime tests**

```python
def test_all_object_stage_tasks_are_registered():
    assert sorted(celery_app.tasks.keys()) >= [
        "content.collect_source",
        "content.parse_article",
        "content.normalize_article",
        "content.extract_event_frames",
    ]


def test_parse_task_marks_object_done_on_success_in_eager_mode():
    celery_app.conf.task_always_eager = True
    content_tasks.parse_article.delay("article-1")
    assert article.parse_status == "done"


def test_llm_rate_limit_wait_does_not_increment_attempts():
    limiter.acquire("normalization")
    assert article.normalization_attempts == 0
```

- [ ] **Step 2: Run the Celery/runtime tests to verify they fail**

Run: `python -m unittest -v tests.test_content_tasks`
Expected: FAIL with missing `celery_config`, `llm_rate_limiter`, and `content_tasks`

- [ ] **Step 3: Add Celery and the Redis token/lease limiter**

```python
celery_app = Celery("kff", broker=build_redis_broker_url_from_env())
celery_app.conf.task_always_eager = False


def build_redis_broker_url_from_env() -> str:
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


class LlmRateLimiter:
    def acquire(self, bucket: str) -> None:
        while not self._try_acquire(bucket):
            time.sleep(self._poll_seconds)


@celery_app.task(name="content.collect_source")
def collect_source(source_name: str, run_id: str) -> None:
    run_object_stage(source_name=source_name, run_id=run_id, stage="collect")


@celery_app.task(name="content.parse_article")
def parse_article(article_id: str) -> None:
    run_object_stage(article_id=article_id, stage="parse")


@celery_app.task(name="content.normalize_article")
def normalize_article(article_id: str) -> None:
    run_object_stage(article_id=article_id, stage="normalize")


@celery_app.task(name="content.extract_event_frames")
def extract_event_frames(article_id: str) -> None:
    run_object_stage(article_id=article_id, stage="event_frame")
```

- [ ] **Step 4: Run the Celery/runtime tests again**

Run: `python -m unittest -v tests.test_content_tasks`
Expected: PASS

- [ ] **Step 5: Commit the runtime worker scaffold**

```bash
git add backend/pyproject.toml backend/app/config/celery_config.py backend/app/service/llm_rate_limiter.py backend/app/tasks/celery_app.py backend/app/tasks/content_tasks.py tests/test_content_tasks.py
git commit -m "feat: add celery content tasks and redis llm limiter"
```

### Task 8: Implement the Daily Run Coordinator and Batch Trigger Logic

**Files:**
- Create: `backend/app/service/daily_run_coordinator_service.py`
- Create: `backend/app/tasks/aggregation_tasks.py`
- Modify: `backend/app/models/runtime.py`
- Test: `tests/test_runtime_coordinator_service.py`
- Delete: `backend/app/service/scheduler_service.py`

- [ ] **Step 1: Write the failing coordinator tests**

```python
def test_requeues_retryable_failed_objects():
    article.normalization_status = "failed"
    article.normalization_attempts = 1
    coordinator.tick()
    assert queued_task_names == ["content.normalize_article"]


def test_reclaims_stale_running_objects_before_requeue():
    article.normalization_status = "running"
    article.normalization_updated_at = old_timestamp
    coordinator.tick()
    assert article.normalization_status == "failed"
    assert article.normalization_attempts == 1


def test_enqueues_day_batch_jobs_once_front_stages_are_drained():
    coordinator.tick()
    assert queued_task_names[-1] == "aggregation.pack_strict_stories_for_day"


def test_enqueues_generate_only_after_pack_succeeds():
    mark_batch_stage_done(run_id, business_day, "strict_story_packing")
    coordinator.tick()
    assert queued_task_names[-1] == "aggregation.generate_digests_for_day"


def test_does_not_enqueue_duplicate_batch_jobs_for_same_day():
    coordinator.tick()
    coordinator.tick()
    assert queued_task_names.count("aggregation.pack_strict_stories_for_day") == 1
```

- [ ] **Step 2: Run the coordinator tests to verify they fail**

Run: `python -m unittest -v tests.test_runtime_coordinator_service`
Expected: FAIL with missing `DailyRunCoordinatorService`

- [ ] **Step 3: Implement re-scan recovery and batch triggering**

```python
class DailyRunCoordinatorService:
    def tick(self, run_id: str, business_day: date) -> None:
        self._reclaim_stale_objects()
        self._requeue_retryable_sources(run_id)
        self._requeue_retryable_articles(stage="parse")
        self._requeue_retryable_articles(stage="normalize")
        self._requeue_retryable_articles(stage="event_frame")
        if self._front_stages_drained(run_id, business_day):
            self._enqueue_unique_batch("aggregation.pack_strict_stories_for_day", business_day, run_id)
        if self._batch_stage_done(run_id, business_day, "strict_story_packing"):
            self._enqueue_unique_batch("aggregation.generate_digests_for_day", business_day, run_id)
```

- [ ] **Step 4: Run the coordinator tests again**

Run: `python -m unittest -v tests.test_runtime_coordinator_service`
Expected: PASS

- [ ] **Step 5: Commit the coordinator**

```bash
git add backend/app/service/daily_run_coordinator_service.py backend/app/tasks/aggregation_tasks.py backend/app/models/runtime.py tests/test_runtime_coordinator_service.py
git rm backend/app/service/scheduler_service.py
git commit -m "feat: add daily run coordinator and batch triggers"
```

### Task 9: Add the Today-Article Dev Script and Manual Review Artifacts

**Files:**
- Create: `backend/app/scripts/run_celery_worker.py`
- Create: `backend/app/scripts/run_daily_coordinator.py`
- Create: `backend/app/scripts/dev_run_today_digest_pipeline.py`
- Modify: `backend/app/scripts/README.md`
- Test: `tests/test_today_digest_pipeline_script.py`

- [ ] **Step 1: Write the failing today-script tests**

```python
def test_dev_run_today_digest_pipeline_outputs_review_bundle():
    result = run_script("--skip-collect")
    assert result.exit_code == 0
    assert "review bundle:" in result.stdout
    assert (output_dir / "digests.json").exists()
    assert (output_dir / "summary.md").exists()
```

- [ ] **Step 2: Run the script tests to verify they fail**

Run: `python -m unittest -v tests.test_today_digest_pipeline_script`
Expected: FAIL with missing `dev_run_today_digest_pipeline.py`

- [ ] **Step 3: Implement the today pipeline script and review artifacts**

```python
async def main() -> None:
    business_day = business_day_for_ingested_at(datetime.now(UTC))
    article_ids = await collect_today_new_articles(
        source_names=args.source_names,
        limit_sources=args.limit_sources,
    )
    await wait_for_front_stages(article_ids)
    await run_day_batches(business_day)
    review_dir = write_review_bundle(
        business_day=business_day,
        digests=load_day_digests(business_day),
        articles=load_day_articles(article_ids),
    )
    print(f"review bundle: {review_dir}")
```

- [ ] **Step 4: Run the script tests again**

Run: `python -m unittest -v tests.test_today_digest_pipeline_script`
Expected: PASS

- [ ] **Step 5: Commit the dev/product workflow**

```bash
git add backend/app/scripts/run_celery_worker.py backend/app/scripts/run_daily_coordinator.py backend/app/scripts/dev_run_today_digest_pipeline.py backend/app/scripts/README.md tests/test_today_digest_pipeline_script.py
git commit -m "feat: add today digest pipeline review scripts"
```

### Task 10: Retire Story-Era Modules and Verify the Refactor End-to-End

**Files:**
- Create: `tests/test_digest_runtime_integration.py`
- Modify: `backend/app/app_main.py`
- Modify: `backend/app/router/__init__.py`
- Modify: `backend/README.md`
- Modify: `tests/test_pipeline_refactor.py`
- Delete: `backend/app/service/article_enrichment_service.py`
- Delete: `backend/app/service/article_cluster_service.py`
- Delete: `backend/app/service/story_generation_service.py`
- Delete: `backend/app/models/story.py`

- [ ] **Step 1: Write the failing import/regression test**

```python
def test_business_day_runtime_persists_digest_without_story_services():
    result = run_seeded_business_day()
    assert result.digest_count == 1
    assert result.story_service_calls == 0
    assert result.stale_digest_count == 0
```

- [ ] **Step 2: Run the import/regression test to verify it fails**

Run: `python -m unittest -v tests.test_digest_runtime_integration`
Expected: FAIL while the integration path still depends on story-era modules or misses digest persistence

- [ ] **Step 3: Remove obsolete story-era modules and docs references**

```python
from backend.app.router import digest_router

app.include_router(digest_router, prefix="/api/v1")
```

- [ ] **Step 4: Run the full automated verification suite**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: PASS

- [ ] **Step 5: Run the product-state verification script and capture artifacts**

Run: `python backend/app/scripts/dev_run_today_digest_pipeline.py --source Vogue --source WWD`
Expected:
- script exits `0`
- prints the review bundle path
- writes `digests.json`, `articles.json`, and `summary.md`
- human can inspect digest grouping and writing quality from today’s newly collected articles

- [ ] **Step 6: Commit the final cleanup**

```bash
git add backend/app/app_main.py backend/app/router/__init__.py backend/README.md tests/test_pipeline_refactor.py tests/test_digest_runtime_integration.py
git rm backend/app/service/article_enrichment_service.py backend/app/service/article_cluster_service.py backend/app/service/story_generation_service.py backend/app/models/story.py
git commit -m "refactor: replace story pipeline with digest runtime"
```

## Manual Product Review Checklist

Run this after Task 10, Step 5:

- [ ] Open `summary.md` in the review bundle and confirm digest count matches expectation for the day.
- [ ] Read every digest body and verify it is a readable report, not a bullet summary.
- [ ] Check whether `strict_story` grouping obviously merged unrelated events.
- [ ] Check whether multiple digests overlap only when they add new strict-story composition value.
- [ ] Check at least three digests against their source article links to confirm no obvious factual drift.
- [ ] If grouping quality is poor, inspect the emitted `articles.json` and `digests.json` before changing prompts or thresholds.

## Verification Commands

Use these commands during execution, especially before the final commit:

```bash
python -m unittest -v tests.test_digest_models
python -m unittest -v tests.test_business_day_service tests.test_article_normalization_service
python -m unittest -v tests.test_event_frame_extraction_service
python -m unittest -v tests.test_strict_story_packing_service
python -m unittest -v tests.test_digest_generation_service tests.test_digest_api
python -m unittest -v tests.test_content_tasks
python -m unittest -v tests.test_runtime_coordinator_service
python -m unittest -v tests.test_today_digest_pipeline_script
python -m unittest -v tests.test_digest_runtime_integration
python -m unittest discover -s tests -p "test_*.py" -v
python -m py_compile backend/app/app_main.py
python backend/app/scripts/dev_run_today_digest_pipeline.py --source Vogue --source WWD
```

## Notes for the Implementer

- Use `@python-code-style` for every Python change.
- Keep Postgres as the only business truth source.
- Do not reintroduce `article.should_publish` as the main gate.
- Do not reintroduce visual image analysis into the primary runtime.
- Do not turn `strict_story` or `digest` aggregation into object-level queue fan-out.
- Keep batch semantics at the business-day level for `strict_story` packing and `digest` generation.
