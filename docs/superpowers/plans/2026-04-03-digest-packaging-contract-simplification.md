# Digest Packaging Contract Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify `digest_packaging` so it only returns `story_keys` and `editorial_angle`, derive `article_ids` and `source_names` locally, and keep final title/dek generation only in `digest_report_writing`.

**Architecture:** Keep the existing `story -> facet assignment -> digest packaging -> digest report writing` chain, but narrow each stage's responsibility. `DigestPackagingService` will stop returning redundant editorial and article-selection fields, `DigestReportWritingService` will receive compact story constraints plus resolved article content, and downstream tests will be updated to the slimmer `ResolvedDigestPlan` contract.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy, LangChain structured output, unittest

---

## File Map

- Modify: `backend/app/schemas/llm/digest_packaging.py`
  Define the slimmer LLM output schema for packaging.
- Modify: `backend/app/prompts/digest_packaging_prompt.py`
  Remove stale output requirements and JSON example fields.
- Modify: `backend/app/service/digest_packaging_service.py`
  Remove redundant validations, derive `article_ids` locally in stable order, keep derived `source_names`, and slim `ResolvedDigestPlan`.
- Modify: `backend/app/prompts/digest_report_writing_prompt.py`
  Align the prompt text with the new writer input shape.
- Modify: `backend/app/service/digest_report_writing_service.py`
  Stop sending packaging title/dek, add compact story summaries to the writer payload, and keep local source-article validation.
- Modify: `backend/tests/test_digest_packaging_service.py`
  Cover the new packaging schema and new zero-article fail-fast.
- Modify: `backend/tests/test_digest_report_writing_service.py`
  Cover the new writer payload shape and missing-story fail-fast, and update the local session fixture to seed `Story` rows.
- Modify: `backend/tests/test_digest_generation_service.py`
  Update all direct `ResolvedDigestPlan(...)` fixtures to the slimmer contract.
- Modify: `backend/tests/test_story_digest_runtime_integration.py`
  Update the fake packaging agent and integration assertions so they cannot silently pass obsolete fields.

## Task 1: Slim `digest_packaging` Output And Derive Articles Locally

**Files:**
- Modify: `backend/app/schemas/llm/digest_packaging.py`
- Modify: `backend/app/prompts/digest_packaging_prompt.py`
- Modify: `backend/app/service/digest_packaging_service.py`
- Modify: `backend/tests/test_digest_packaging_service.py`

- [ ] **Step 1: Write the failing packaging tests**

Update `backend/tests/test_digest_packaging_service.py` so packaging fixtures no longer return `facet`, `article_ids`, `title_zh`, or `dek_zh`, and add one explicit zero-article regression.

```python
def test_build_plans_for_day_derives_article_ids_and_source_names_locally(self) -> None:
    session = _build_session()
    self.addCleanup(session.close)
    call_log: list[dict[str, object]] = []
    service = DigestPackagingService(
        agent=_build_fake_agent(
            [
                DigestPackagingSchema.model_validate(
                    {
                        "digests": [
                            {
                                "story_keys": ["story-1"],
                                "editorial_angle": "秀场造型作为独立看点",
                            }
                        ]
                    }
                ),
                DigestPackagingSchema.model_validate(
                    {
                        "digests": [
                            {
                                "story_keys": ["story-1", "story-2"],
                                "editorial_angle": "设计语言与组织动作共同指向新趋势",
                            }
                        ]
                    }
                ),
            ],
            call_log=call_log,
        ),
        rate_limiter=_FakeRateLimiter(),
    )

    plans = asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))

    self.assertEqual(2, len(plans))
    self.assertEqual(("article-1", "article-2"), plans[0].article_ids)
    self.assertEqual(("article-1", "article-2", "article-3"), plans[1].article_ids)
    self.assertEqual(("Vogue", "WWD"), plans[0].source_names)
    self.assertEqual(("BoF", "Vogue", "WWD"), plans[1].source_names)
```

```python
def test_build_plans_for_day_fails_when_selected_story_group_resolves_zero_articles(self) -> None:
    session = _build_session()
    self.addCleanup(session.close)
    session.query(StoryArticle).delete()
    session.commit()

    service = DigestPackagingService(
        agent=_build_fake_agent(
            [
                DigestPackagingSchema.model_validate(
                    {
                        "digests": [
                            {
                                "story_keys": ["story-1"],
                                "editorial_angle": "没有文章的非法组合",
                            }
                        ]
                    }
                )
            ],
            call_log=[],
        ),
        rate_limiter=_FakeRateLimiter(),
    )

    with self.assertRaisesRegex(ValueError, "resolved zero article_ids"):
        asyncio.run(service.build_plans_for_day(session, date(2026, 3, 30), run_id="run-1"))
```

- [ ] **Step 2: Run the packaging tests and verify they fail**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_packaging_service.DigestPackagingServiceTest.test_build_plans_for_day_derives_article_ids_and_source_names_locally \
  backend.tests.test_digest_packaging_service.DigestPackagingServiceTest.test_build_plans_for_day_fails_when_selected_story_group_resolves_zero_articles
```

Expected:

- first test fails because `DigestPackagingSchema` still requires removed fields
- second test fails because zero-article local fail-fast does not exist yet

- [ ] **Step 3: Implement the slimmer packaging schema, prompt, and local resolution**

Replace the schema in `backend/app/schemas/llm/digest_packaging.py` with:

```python
class DigestPackagingPlan(BaseModel):
    """Plan for a single digest package."""

    story_keys: list[str] = Field(min_length=1)
    editorial_angle: str = Field(min_length=1)
```

Update `backend/app/prompts/digest_packaging_prompt.py` so the rule text and JSON example remove stale fields:

```python
def build_digest_packaging_prompt() -> str:
    """Build the system prompt for digest packaging."""
    return """
你是时尚资讯总编，负责把 story 打包成 digest 计划。

输入是同一业务日、同一 facet 下的 story 与候选 article 摘要。你的任务是输出 digest 计划：
- 可以把多个 story 合并到同一个 digest
- story_keys 必须来自输入
- editorial_angle 必须是中文可读内容

规则：
- 可以选择不收录某些 story
- 输出必须是严格 JSON，符合 schema
- 不要输出解释文本或 Markdown 代码块

输出 JSON 结构：
{
  "digests": [
    {
      "story_keys": ["..."],
      "editorial_angle": "..."
    }
  ]
}
""".strip()
```

Slim `ResolvedDigestPlan` and derive article ids locally inside `backend/app/service/digest_packaging_service.py`:

```python
@dataclass(frozen=True)
class ResolvedDigestPlan:
    business_date: date
    facet: str
    story_keys: tuple[str, ...]
    article_ids: tuple[str, ...]
    editorial_angle: str
    source_names: tuple[str, ...]
```

```python
Delete the existing `plan.facet` validation block and the existing `plan.article_ids` / `title_zh` / `dek_zh` validation block entirely.
Do not merge the new logic into the old loop body.

The replacement must start at:

for index, plan in enumerate(schema.digests):
    editorial_angle = plan.editorial_angle.strip()
    if not editorial_angle:
        raise ValueError(f"digests[{index}] editorial_angle cannot be blank")

    story_keys = [story_key.strip() for story_key in plan.story_keys]
    if any(not story_key for story_key in story_keys):
        raise ValueError(f"digests[{index}] story_keys contains blank value")
    if len(set(story_keys)) != len(story_keys):
        raise ValueError(f"digests[{index}] story_keys contains duplicates")

    ordered_article_ids: list[str] = []
    seen_article_ids: set[str] = set()
    for story_key in story_keys:
        for article in story_by_key[story_key].articles:
            if article.article_id in seen_article_ids:
                continue
            seen_article_ids.add(article.article_id)
            ordered_article_ids.append(article.article_id)

    if not ordered_article_ids:
        raise ValueError(f"digests[{index}] resolved zero article_ids from story_keys")

    source_names = tuple(sorted({article_by_id[article_id].source_name for article_id in ordered_article_ids}))
    resolved.append(
        ResolvedDigestPlan(
            business_date=business_day,
            facet=facet,
            story_keys=tuple(story_keys),
            article_ids=tuple(ordered_article_ids),
            editorial_angle=editorial_angle,
            source_names=source_names,
        )
    )
```

- [ ] **Step 4: Run the packaging tests and verify they pass**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_packaging_service
```

Expected:

- all packaging tests pass
- no failures mention missing `title_zh`, `dek_zh`, or `article_ids` in packaging schema

- [ ] **Step 5: Commit the packaging contract change**

Important:

- this commit is expected to leave the broader suite temporarily red because `DigestReportWritingService` and several direct `ResolvedDigestPlan(...)` test fixtures still reference removed fields
- treat this as a task-local checkpoint only
- do not stop after this commit and assume the branch is healthy

Run:

```bash
git add \
  backend/app/schemas/llm/digest_packaging.py \
  backend/app/prompts/digest_packaging_prompt.py \
  backend/app/service/digest_packaging_service.py \
  backend/tests/test_digest_packaging_service.py
git commit -m "refactor: slim digest packaging contract"
```

## Task 2: Send Story Constraints, Not Packaging Title/Dek, To The Writer

**Files:**
- Modify: `backend/app/prompts/digest_report_writing_prompt.py`
- Modify: `backend/app/service/digest_report_writing_service.py`
- Modify: `backend/tests/test_digest_report_writing_service.py`

- [ ] **Step 1: Write the failing writer payload tests**

Add one payload-shape test and one missing-story fail-fast test to `backend/tests/test_digest_report_writing_service.py`.

Before adding the tests, update `_build_session(root_path)` so it seeds the `Story` rows referenced by the new story-summary loader.

```python
from backend.app.models import Article, Digest, PipelineRun, Story, ensure_article_storage_schema
```

```python
session.add_all(
    [
        Story(
            story_key="story-1",
            business_date=business_day,
            event_type="runway_show",
            synopsis_zh="Acme 巴黎秀场",
            anchor_json={"brand": "Acme"},
            article_membership_json=["article-1"],
            created_run_id="run-1",
            clustering_status="done",
        ),
        Story(
            story_key="story-2",
            business_date=business_day,
            event_type="campaign_launch",
            synopsis_zh="Beta 发布新广告大片",
            anchor_json={"brand": "Beta"},
            article_membership_json=["article-2"],
            created_run_id="run-1",
            clustering_status="done",
        ),
    ]
)
```

```python
def test_write_digest_sends_story_summaries_and_omits_packaging_title_fields(self) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        session = _build_session(Path(tmp_dir))
        self.addCleanup(session.close)
        call_log: list[dict[str, object]] = []
        service = DigestReportWritingService(
            agent=_FakeAgent(
                [
                    DigestReportWritingSchema.model_validate(
                        {
                            "title_zh": "本日品牌动作速写",
                            "dek_zh": "导语摘要",
                            "body_markdown": "# 正文\n\n聚合后的内容",
                            "source_article_ids": ["article-2", "article-1"],
                        }
                    )
                ],
                call_log=call_log,
            ),
            markdown_root=Path(tmp_dir),
            rate_limiter=_build_fake_rate_limiter(),
        )

        asyncio.run(
            service.write_digest(
                session,
                run_id="run-1",
                plan=ResolvedDigestPlan(
                    business_date=date(2026, 3, 30),
                    facet="trend_summary",
                    story_keys=("story-1", "story-2"),
                    article_ids=("article-1", "article-2"),
                    editorial_angle="用品牌动作解释趋势变化",
                    source_names=("Vogue", "WWD"),
                ),
            )
        )

        payload = json.loads(call_log[0]["messages"][0]["content"])
        self.assertNotIn("title_zh", payload["plan"])
        self.assertNotIn("dek_zh", payload["plan"])
        self.assertEqual(
            [
                {"story_key": "story-1", "synopsis_zh": "Acme 巴黎秀场", "event_type": "runway_show"},
                {"story_key": "story-2", "synopsis_zh": "Beta 发布新广告大片", "event_type": "campaign_launch"},
            ],
            payload["story_summaries"],
        )
```

```python
def test_write_digest_fails_when_story_summary_rows_are_missing(self) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        session = _build_session(Path(tmp_dir))
        self.addCleanup(session.close)
        session.query(Story).delete()
        session.commit()
        service = DigestReportWritingService(
            agent=_FakeAgent([]),
            markdown_root=Path(tmp_dir),
            rate_limiter=_build_fake_rate_limiter(),
        )

        with self.assertRaisesRegex(ValueError, "missing story summary rows"):
            asyncio.run(
                service.write_digest(
                    session,
                    run_id="run-1",
                    plan=ResolvedDigestPlan(
                        business_date=date(2026, 3, 30),
                        facet="trend_summary",
                        story_keys=("story-1",),
                        article_ids=("article-1",),
                        editorial_angle="用品牌动作解释趋势变化",
                        source_names=("Vogue",),
                    ),
                )
            )
```

- [ ] **Step 2: Run the writer tests and verify they fail**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_report_writing_service.DigestReportWritingServiceTest.test_write_digest_sends_story_summaries_and_omits_packaging_title_fields \
  backend.tests.test_digest_report_writing_service.DigestReportWritingServiceTest.test_write_digest_fails_when_story_summary_rows_are_missing
```

Expected:

- first test fails because `title_zh` and `dek_zh` are still present in the writer payload
- second test fails because no story-summary loader/fail-fast exists yet

- [ ] **Step 3: Implement local story-summary loading and the new writer payload**

Update the writer prompt in `backend/app/prompts/digest_report_writing_prompt.py`:

```python
def build_digest_report_writing_prompt() -> str:
    """Build the system prompt for digest long-form report writing."""
    return """
你是时尚资讯主笔，负责输出一条 digest 的长文。

输入包括：
- digest 计划（facet、story_keys、editorial_angle、article_ids）
- story 级约束摘要
- 对应的 article 摘要与正文

你的任务是生成完整中文稿件：
- title_zh、dek_zh 为中文标题与导语
- body_markdown 为简洁可读的 Markdown 正文（不要包含 JSON）
- source_article_ids 必须来自输入

规则：
- 只基于输入事实，不要编造
- 必须沿着 editorial_angle 组织叙事
- 输出必须是严格 JSON，符合 schema
- 不要输出解释文本或 Markdown 代码块
""".strip()
```

Add a local story-summary loader and update the writer payload in `backend/app/service/digest_report_writing_service.py`:

```python
from backend.app.models import Article, Digest, Story
```

```python
@dataclass(frozen=True)
class _StorySummaryInput:
    story_key: str
    synopsis_zh: str
    event_type: str
```

```python
def _load_story_summaries(
    self,
    session: Session,
    story_keys: tuple[str, ...],
) -> list[_StorySummaryInput]:
    rows = list(
        session.scalars(
            select(Story)
            .where(Story.story_key.in_(story_keys))
            .order_by(Story.story_key.asc())
        ).all()
    )
    story_by_key = {row.story_key: row for row in rows}
    missing_story_keys = [story_key for story_key in story_keys if story_key not in story_by_key]
    if missing_story_keys:
        joined = ", ".join(missing_story_keys)
        raise ValueError(f"missing story summary rows for digest report writing: {joined}")
    return [
        _StorySummaryInput(
            story_key=story_key,
            synopsis_zh=story_by_key[story_key].synopsis_zh.strip(),
            event_type=story_by_key[story_key].event_type.strip() or "general",
        )
        for story_key in story_keys
    ]
```

```python
async def write_digest(
    self,
    session: Session,
    plan: ResolvedDigestPlan,
    *,
    run_id: str,
) -> Digest:
    story_summaries = self._load_story_summaries(session, plan.story_keys)
    article_sources = self._load_article_sources(session, plan.article_ids)
    schema = await self._write_report(plan, story_summaries, article_sources, run_id=run_id)
    return self._resolve_written_digest(
        run_id=run_id,
        plan=plan,
        article_sources=article_sources,
        schema=schema,
)
```

```python
async def _write_report(
    self,
    plan: ResolvedDigestPlan,
    story_summaries: list[_StorySummaryInput],
    article_sources: list[_ArticleSourceInput],
    *,
    run_id: str,
) -> DigestReportWritingSchema:
    agent = self._get_agent()
    system_prompt = build_digest_report_writing_prompt()
    user_message = self._build_user_message(plan, story_summaries, article_sources)
    invoke_payload = {
        "messages": [
            {"role": "user", "content": user_message},
        ],
    }
    ...
```

```python
def _build_user_message(
    self,
    plan: ResolvedDigestPlan,
    story_summaries: list[_StorySummaryInput],
    article_sources: list[_ArticleSourceInput],
) -> str:
    payload = {
        "plan": {
            "business_date": plan.business_date.isoformat(),
            "facet": plan.facet,
            "story_keys": list(plan.story_keys),
            "article_ids": list(plan.article_ids),
            "editorial_angle": plan.editorial_angle,
            "source_names": list(plan.source_names),
        },
        "story_summaries": [
            {
                "story_key": story.story_key,
                "synopsis_zh": story.synopsis_zh,
                "event_type": story.event_type,
            }
            for story in story_summaries
        ],
        "source_articles": [
            {
                "article_id": article.article_id,
                "source_name": article.source_name,
                "title_raw": article.title_raw,
                "summary_raw": article.summary_raw,
                "body_markdown": article.body_markdown,
            }
            for article in article_sources
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
```

- [ ] **Step 4: Run the writer tests and verify they pass**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_report_writing_service
```

Expected:

- all writer tests pass
- payload tests confirm `plan.title_zh` and `plan.dek_zh` are gone
- writer payload includes `story_summaries`

- [ ] **Step 5: Commit the writer payload change**

Important:

- after this task, writer-specific code should be green
- the branch may still have stale `ResolvedDigestPlan(...)` constructors in other test files until Task 3 is complete

Run:

```bash
git add \
  backend/app/prompts/digest_report_writing_prompt.py \
  backend/app/service/digest_report_writing_service.py \
  backend/tests/test_digest_report_writing_service.py
git commit -m "refactor: constrain digest writer with story summaries"
```

## Task 3: Update Cross-Service Fixtures And Verify End-To-End Contract

**Files:**
- Modify: `backend/tests/test_digest_generation_service.py`
- Modify: `backend/tests/test_story_digest_runtime_integration.py`

- [ ] **Step 1: Make the cross-service tests fail on the old contract**

Update all `ResolvedDigestPlan(...)` constructors in `backend/tests/test_digest_generation_service.py` to remove stale `title_zh` and `dek_zh` arguments. Update the fake packaging agent in `backend/tests/test_story_digest_runtime_integration.py` so it returns only the new contract:

```python
def _build_fake_digest_packaging_agent(call_log: list[dict[str, object]]) -> _FakeStructuredResponseAgent:
    def responder(payload: dict[str, object]) -> DigestPackagingSchema:
        messages = payload.get("messages")
        assert isinstance(messages, list)
        user_payload = json.loads(messages[0]["content"])
        stories = user_payload.get("stories", [])
        story_keys = []
        for story in stories:
            normalized_story_key = str(story["story_key"])
            if normalized_story_key not in story_keys:
                story_keys.append(normalized_story_key)

        response_payload = {
            "digests": [
                {
                    "story_keys": story_keys,
                    "editorial_angle": "同日主事件聚焦",
                }
            ]
        }
        return DigestPackagingSchema.model_validate(response_payload)

    return _FakeStructuredResponseAgent(responder=responder, call_log=call_log)
```

Strengthen the integration assertion so it would fail if the old contract leaks through:

```python
self.assertEqual([story_key], report_request["plan"]["story_keys"])
self.assertNotIn("title_zh", report_request["plan"])
self.assertNotIn("dek_zh", report_request["plan"])
self.assertEqual(
    [
        {
            "story_key": story_key,
            "synopsis_zh": "Acme 同日秀场事件",
            "event_type": "runway_show",
        }
    ],
    report_request["story_summaries"],
)
```

- [ ] **Step 2: Run the cross-service tests and verify they fail**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_generation_service \
  backend.tests.test_story_digest_runtime_integration
```

Expected:

- failures from stale `ResolvedDigestPlan(...)` constructor arguments
- failures from the old fake packaging response shape

- [ ] **Step 3: Fix the affected tests and any remaining plan-constructor fallout**

Use the slimmer constructor shape everywhere:

```python
ResolvedDigestPlan(
    business_date=date(2026, 3, 30),
    facet="trend_summary",
    story_keys=("story-1", "story-2"),
    article_ids=("article-1", "article-2"),
    editorial_angle="趋势综合稿",
    source_names=("Vogue", "WWD"),
)
```

Do not reintroduce compatibility shims in production code just to satisfy old tests.

- [ ] **Step 4: Run the full affected regression suite**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_llm_contracts \
  backend.tests.test_digest_packaging_service \
  backend.tests.test_digest_report_writing_service \
  backend.tests.test_digest_generation_service \
  backend.tests.test_story_digest_runtime_integration
```

Expected:

- all tests pass
- no failures mention missing packaging `title_zh`, `dek_zh`, or `article_ids`

- [ ] **Step 5: Commit the cross-service contract cleanup**

Run:

```bash
git add \
  backend/tests/test_digest_generation_service.py \
  backend/tests/test_story_digest_runtime_integration.py
git commit -m "test: update digest contract fixtures"
```

## Self-Review

- Spec coverage:
  - slimmer packaging schema: Task 1
  - prompt cleanup: Task 1
  - local `article_ids` and `source_names` derivation: Task 1
  - zero-article fail-fast: Task 1
  - writer payload cleanup and story constraints: Task 2
  - `ResolvedDigestPlan` fallout and integration fake-agent trap: Task 3
- Placeholder scan:
  - no `TODO`, `TBD`, or “appropriate validation” language left without explicit code
- Type consistency:
  - final `ResolvedDigestPlan` shape is consistent across Tasks 1-3
  - writer payload key is consistently `story_summaries`
