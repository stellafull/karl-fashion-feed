# Runtime Scripts

## Digest Runtime

- `backend/app/scripts/run_celery_worker.py`  
  启动 Celery worker，消费 `content,aggregation` 队列。
- `backend/app/scripts/run_daily_coordinator.py`  
  启动单机 coordinator loop，持续 tick 当天 runtime。
- `backend/app/scripts/dev_run_today_digest_pipeline.py`  
  本地同步跑今天 digest pipeline（Celery eager），输出 review bundle。

## Other Existing Scripts

- `backend/app/scripts/run_chat_worker.py`  
  启动聊天消息 worker。
- `backend/app/scripts/init_root_user.py`  
  初始化 root 用户。
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
