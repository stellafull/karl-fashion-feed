# Runtime Scripts

## Digest Runtime

- `backend/app/scripts/run_celery_worker.py`  
  启动 Celery worker，消费 `content,aggregation` 队列。
- `backend/app/scripts/run_daily_coordinator.py`  
  启动单机 coordinator loop，持续 tick 当天 runtime。
- `backend/app/scripts/dev_run_today_digest_pipeline.py`  
  本地同步跑今天 digest pipeline（Celery eager），输出 review bundle（仅 dev 使用）。

## Other Existing Scripts

- `backend/app/scripts/run_chat_worker.py`  
  启动聊天消息 worker。
- `backend/app/scripts/init_root_user.py`  
  初始化本地联调账号：`root/root`、`ROOT1/ROOT1`、`ROOT2/ROOT2`。
- `backend/app/scripts/chat_random_smoke.py`  
  走真实前端页面 + 后端 API 做一次随机真实提问 smoke，默认自动登录 `ROOT1/ROOT1`、随机选题并保存输出 JSON + 截图。
- `backend/app/scripts/压测.py`  
  压测脚本。

## Commands

启动 runtime worker：

```bash
backend/.venv/bin/python backend/app/scripts/run_celery_worker.py
```

启动 coordinator loop：

```bash
backend/.venv/bin/python backend/app/scripts/run_daily_coordinator.py
```

限制 source 范围启动 coordinator：

```bash
backend/.venv/bin/python backend/app/scripts/run_daily_coordinator.py --source-name Vogue --limit-sources 1
```

本地同步跑今天 pipeline 并产出 review bundle：

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect
```

指定输出目录：

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect --output-dir /tmp/today-digest-review
```

只保留发布时间属于当天 business day 的文章（dev review 过滤，不影响生产 runtime）：

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect --published-today-only
```

为本次 dev run 导出原始 LLM artifact 落盘目录：

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect --llm-artifact-dir /tmp/llm-artifacts
```

随机跑一次真实 chat 联调 smoke（默认随机问题，每次都不同）：

```bash
uv run --project backend python backend/app/scripts/chat_random_smoke.py
```

指定 seed 复现同一个随机问题：

```bash
uv run --project backend python backend/app/scripts/chat_random_smoke.py --seed 42
```
