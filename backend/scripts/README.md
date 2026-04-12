# Legacy Scripts Directory

`backend/scripts/` 不再是当前应用的主入口。

当前有效的运行与联调脚本都在 [backend/app/scripts/README.md](/home/czy/karl-fashion-feed/backend/app/scripts/README.md) 对应的目录下：

- `backend/app/scripts/run_celery_worker.py`
- `backend/app/scripts/run_daily_coordinator.py`
- `backend/app/scripts/dev_run_today_digest_pipeline.py`
- `backend/app/scripts/run_chat_worker.py`
- `backend/app/scripts/init_root_user.py`

如果你在找当前后端入口，请直接使用：

- API: [backend/app/app_main.py](/home/czy/karl-fashion-feed/backend/app/app_main.py)
- 运行说明: [backend/README.md](/home/czy/karl-fashion-feed/backend/README.md)
- 运维说明: [docs/ops-runbook.md](/home/czy/karl-fashion-feed/docs/ops-runbook.md)

这个目录保留仅为历史兼容和仓库结构稳定性，不应再被当成当前实现说明。
