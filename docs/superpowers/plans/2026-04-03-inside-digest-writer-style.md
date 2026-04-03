# Inside Digest Writer Style Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `digest_report_writing` from a magazine-feature voice to a company-internal inside-digest voice without changing pipeline structure or persistence shape.

**Architecture:** Keep the existing `packaging -> digest_report_writing` contract and only tighten the writer prompt. Codify the style contract in prompt-focused tests so the writer stays flexible in organization while being hard-constrained on audience, tone, and information priorities.

**Tech Stack:** Python 3.12, unittest, existing LangChain writer path

---

## File Map

- Modify: `backend/app/prompts/digest_report_writing_prompt.py`
  Replace the current generic long-form prompt with an inside-digest style prompt.
- Create: `backend/tests/test_digest_report_writing_prompt.py`
  Add prompt contract tests that assert the new internal-reader style cues are present.
- Verify: `backend/tests/test_digest_report_writing_service.py`
  Existing writer-path tests should remain green because they already assert prompt wiring through `build_digest_report_writing_prompt()`.
- Verify: `backend/tests/test_digest_generation_service.py`
  Existing digest generation tests should remain green after the prompt change.
- Verify: `backend/tests/test_story_digest_runtime_integration.py`
  Existing integration test should remain green after the prompt change.

## Task 1: Codify The Inside Digest Prompt Contract

**Files:**
- Modify: `backend/app/prompts/digest_report_writing_prompt.py`
- Create: `backend/tests/test_digest_report_writing_prompt.py`

- [ ] **Step 1: Write the failing prompt contract tests**

Create `backend/tests/test_digest_report_writing_prompt.py` with these tests:

```python
from __future__ import annotations

import unittest

from backend.app.prompts.digest_report_writing_prompt import build_digest_report_writing_prompt


class DigestReportWritingPromptTest(unittest.TestCase):
    def test_prompt_targets_internal_inside_digest_readers(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("公司内部", prompt)
        self.assertIn("inside digest", prompt)
        self.assertIn("不是面向消费者的时尚杂志稿件", prompt)

    def test_prompt_keeps_writer_flexible_but_defaults_to_short_digest(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("不要强制使用固定小标题或固定段落模板", prompt)
        self.assertIn("默认写成短读", prompt)
        self.assertIn("如果同一 digest 包含多篇 story", prompt)

    def test_prompt_prioritizes_business_and_design_signal(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("品牌动作", prompt)
        self.assertIn("商品信号", prompt)
        self.assertIn("趋势变化", prompt)
        self.assertIn("设计师可直接取用", prompt)

    def test_prompt_explicitly_blocks_magazine_style_language(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("避免夸张修辞", prompt)
        self.assertIn("避免情绪化开头", prompt)
        self.assertIn("避免空泛审美词", prompt)
```

- [ ] **Step 2: Run the new prompt tests and verify they fail**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_report_writing_prompt
```

Expected:

- tests fail because the current prompt does not mention internal readers, inside digest voice, business/design priorities, or anti-magazine constraints

- [ ] **Step 3: Replace the writer prompt with the inside-digest style contract**

Update `backend/app/prompts/digest_report_writing_prompt.py` to:

```python
"""Prompt builder for digest long-form report writing."""


def build_digest_report_writing_prompt() -> str:
    """Build the system prompt for digest report writing."""
    return """
你是公司内部资讯 writer，负责输出一条给内部同事阅读的 inside digest。

这不是面向消费者的时尚杂志稿件，也不是外部媒体 feature。

输入包括：
- digest 计划（facet、story_keys、editorial_angle、article_ids）
- story 级约束摘要
- 对应的 article 摘要与正文

你的任务是生成完整中文稿件：
- title_zh：允许编辑型标题，但必须锚定具体品牌、品类、事件或主题
- dek_zh：保留，一句即可
- body_markdown：简洁可读的 Markdown 正文（不要包含 JSON）
- source_article_ids：必须来自输入

写作要求：
- 面向公司内部同事，不是面向公众
- 保持 inside digest 风格：客观、克制、直接
- 默认写成短读；如果同一 digest 包含多篇 story，可以自然变长
- 不要强制使用固定小标题或固定段落模板，让你自行组织编排
- 优先保留品牌动作、商品信号、趋势变化等高信息密度内容
- 让设计师可直接取用有效信号，但不要强行写“对我们有什么启发”

明确避免：
- 避免夸张修辞
- 避免情绪化开头
- 避免空泛审美词
- 避免把 digest 写成时尚杂志长稿

规则：
- 只基于输入事实，不要编造
- 必须沿着 editorial_angle 组织叙事
- 输出必须是严格 JSON，符合 schema
- 不要输出解释文本或 Markdown 代码块
""".strip()
```

- [ ] **Step 4: Run prompt tests and writer-path regressions**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_digest_report_writing_prompt \
  backend.tests.test_digest_report_writing_service \
  backend.tests.test_digest_generation_service \
  backend.tests.test_story_digest_runtime_integration
```

Expected:

- prompt tests pass
- existing writer service tests still pass
- digest generation tests still pass
- runtime integration test still passes

- [ ] **Step 5: Commit the style-contract change**

Run:

```bash
git add \
  backend/app/prompts/digest_report_writing_prompt.py \
  backend/tests/test_digest_report_writing_prompt.py
git commit -m "refactor: shift digest writer to inside digest style"
```

## Task 2: Verify The Full Affected Regression Suite Still Passes

**Files:**
- Verify only

- [ ] **Step 1: Run the full affected regression suite**

Run:

```bash
PYTHONPATH=/home/czy/karl-fashion-feed ./backend/.venv/bin/python -m unittest \
  backend.tests.test_llm_contracts \
  backend.tests.test_digest_packaging_service \
  backend.tests.test_digest_report_writing_prompt \
  backend.tests.test_digest_report_writing_service \
  backend.tests.test_digest_generation_service \
  backend.tests.test_story_digest_runtime_integration
```

Expected:

- all tests pass
- no failures in packaging/writer/generation/integration contract suites

- [ ] **Step 2: Record one manual preview check**

Run an existing real or preview digest review command after the prompt change and inspect at least 3 generated digests manually.

Acceptance criteria:

- titles still read like editorial digest titles, not raw database labels
- `dek` stays one concise sentence
- body reads like internal inside digest, not magazine feature copy
- body keeps business/design signal density without forced fixed sections

- [ ] **Step 3: Commit only if manual preview still matches spec**

If the manual preview is acceptable and tests are green, no new code change is needed here.
If the preview reveals a prompt wording issue, fix only the prompt, rerun Task 1 Step 4 and Task 2 Step 1, then commit:

```bash
git add backend/app/prompts/digest_report_writing_prompt.py backend/tests/test_digest_report_writing_prompt.py
git commit -m "tune inside digest writer prompt"
```

## Self-Review

- Spec coverage:
  - internal-reader orientation: Task 1
  - keep structure flexible: Task 1
  - preserve editorial title + one-line dek: Task 1
  - bias toward brand/product/trend signals: Task 1
  - suppress magazine voice: Task 1
  - confirm no regressions in existing digest path: Task 1 and Task 2
- Placeholder scan:
  - no `TODO` / `TBD`
  - manual preview step has explicit acceptance criteria
- Type consistency:
  - no runtime contract changes introduced
  - prompt-only change remains isolated from packaging/generation schemas
