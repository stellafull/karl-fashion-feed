# Runtime Scripts

## Production-Like Runtime

- `backend/app/scripts/run_celery_worker.py`
  启动 Celery worker，消费 `content,aggregation` 队列。
- `backend/app/scripts/run_scheduler.py`
  启动长期运行的 scheduler loop，按固定间隔 tick `SchedulerService`，负责每天触发采集与后续 digest runtime。
- `backend/app/scripts/run_daily_coordinator.py`
  启动单机 coordinator loop，直接持续 tick 当天 runtime；更适合底层 runtime 排障，而不是常规日更入口。

## Demo Init / Dev Scripts

- `backend/app/scripts/init_recent_demo_digests.py`
  为空库注入最近完整 business day 的 demo digest 数据。它不是生产真相链路；如果库里已经有 digest / story / pipeline_run，会直接 failfast。
- `backend/app/scripts/dev_run_today_digest_pipeline.py`
  本地同步跑今天 digest pipeline（Celery eager），输出 review bundle（仅 dev 使用）。
- `backend/app/scripts/dev_run_today_full_pipeline.py`
  本地同步跑今天 full pipeline（collect → parse → frame → story → digest → RAG），输出 review bundle（仅 dev 使用）。

## Other Existing Scripts

- `backend/app/scripts/init_root_user.py`
  初始化本地联调账号：`dev-root / dev-root`。
- `backend/app/scripts/chat_random_smoke.py`
  走真实前端页面 + 后端 API 做一次随机真实提问 smoke，默认自动登录隐藏 dev 页 `dev-root / dev-root`。
- `backend/app/scripts/run_chat_worker.py`
  启动聊天消息 worker。
- `backend/app/scripts/压测.py`
  压测脚本。

## Commands

启动 runtime worker：

```bash
uv run --project backend python backend/app/scripts/run_celery_worker.py
```

启动 scheduler loop（推荐的日更入口）：

```bash
uv run --project backend python backend/app/scripts/run_scheduler.py
```

启动 coordinator loop（排障 / 直接驱动 runtime）：

```bash
uv run --project backend python backend/app/scripts/run_daily_coordinator.py
```

限制 source 范围启动 coordinator：

```bash
uv run --project backend python backend/app/scripts/run_daily_coordinator.py --source-name Vogue --limit-sources 1
```

为空库注入最近 7 个完整 business day 的 demo digests：

```bash
uv run --project backend python backend/app/scripts/init_recent_demo_digests.py
```

指定 source 范围做 demo init：

```bash
uv run --project backend python backend/app/scripts/init_recent_demo_digests.py --source-name Vogue --limit-sources 1
```

本地同步跑今天 pipeline 并产出 review bundle：

```bash
uv run --project backend python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect
```

指定输出目录：

```bash
uv run --project backend python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect --output-dir /tmp/today-digest-review
```

只保留发布时间属于当天 business day 的文章（dev review 过滤，不影响生产 runtime）：

```bash
uv run --project backend python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect --published-today-only
```

为本次 dev run 导出原始 LLM artifact 落盘目录：

```bash
uv run --project backend python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect --llm-artifact-dir /tmp/llm-artifacts
```

随机跑一次真实 chat 联调 smoke（默认随机问题，每次都不同）：

```bash
uv run --project backend python backend/app/scripts/chat_random_smoke.py
```

指定 seed 复现同一个随机问题：

```bash
uv run --project backend python backend/app/scripts/chat_random_smoke.py --seed 42
```
