# 运维与联调手册

## 1. 依赖组件

当前项目实际依赖：

- PostgreSQL
- Redis
- Qdrant
- Python 3.12+
- Node / pnpm

可选外部能力：

- OpenAI-compatible LLM endpoint
- Brave Search API
- Tavily API

## 2. 关键环境变量

### 基础鉴权

- `AUTH_JWT_SECRET`
- `AUTH_JWT_ALGORITHM`
- `AUTH_ACCESS_TOKEN_EXPIRE_MINUTES`
- `AUTH_BROWSER_STATE_EXPIRE_SECONDS`
- `CORS_ALLOWED_ORIGINS`
- `CHAT_ATTACHMENT_ROOT`

### Feishu 登录

后端：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BROWSER_REDIRECT_URI`
- `FEISHU_FRONTEND_AUTH_COMPLETE_URL`
- `FEISHU_OAUTH_SCOPE`
- `FEISHU_REQUEST_TIMEOUT_SECONDS`

前端：

- `VITE_FEISHU_APP_ID`
- `VITE_FEISHU_SCOPE_LIST`

注意：

- `FEISHU_BROWSER_REDIRECT_URI` 必须和飞书后台 Redirect URL 完全一致
- `FEISHU_FRONTEND_AUTH_COMPLETE_URL` 必须是前端实际可访问的 `/auth/complete` 地址
- 当前实现里 `FEISHU_OAUTH_SCOPE` 用空格分隔；`VITE_FEISHU_SCOPE_LIST` 用逗号分隔

### Postgres

- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

### Redis / Celery

- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_PASSWORD`

### LLM

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `USE_RESPONSES_API`
- `STORY_SUMMARIZATION_MODEL`
- `RAG_MODEL` 或 `RAG_CHAT_MODEL`

### Embedding / Rerank

- `DENSE_EMBEDDING_MODEL`
- `DENSE_EMBEDDING_DIMENSION`
- `SPARSE_EMBEDDING_MODEL`
- `RERANKER_MODEL`

### 检索

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `BRAVE_API_KEY`
- `TAVILY_API_KEY`

## 3. 本地启动顺序

### 后端 API

```bash
backend/.venv/bin/uvicorn backend.app.app_main:app --reload --host 0.0.0.0 --port 8000
```

### 初始化本地调试账号

```bash
backend/.venv/bin/python backend/app/scripts/init_root_user.py
```

该脚本现在只负责创建 `dev-root / dev-root`。普通用户不再依赖本地账号。

### 前端

```bash
cd frontend
pnpm install
pnpm dev
```

默认访问：

- 前端：`http://127.0.0.1:3000`
- 后端 docs：`http://127.0.0.1:8000/docs`
- 隐藏 dev 登录页：`http://127.0.0.1:3000/__dev/login`

如果要联调普通用户飞书登录，还需要保证：

- 前端公开地址可被飞书回跳访问
- `GET /api/v1/auth/feishu/browser/start` 生成的回调 URL 已配置进飞书应用后台

## 3.5 Docker 部署（仅应用层）

当前 compose 文件：`docker-compose.app.yml`

它会启动：

- `backend`
- `worker`
- `scheduler`
- `frontend`

不会启动：

- Postgres
- Redis
- Qdrant

因此部署前必须先准备外部基础设施，并正确填写 `.env`。

### 环境变量准备

```bash
cp .env.example .env
```

必须至少确认：

- `POSTGRES_*`
- `REDIS_*`
- `QDRANT_URL`
- `AUTH_JWT_SECRET`
- `FEISHU_*`
- `VITE_FEISHU_APP_ID`

### 启动

```bash
docker compose -f docker-compose.app.yml up --build -d backend worker scheduler frontend
```

### 初始化 demo digest

仅用于空库首屏可见数据注入：

```bash
docker compose -f docker-compose.app.yml --profile init run --rm demo-init
```

约束：

- 只适合 runtime 为空时执行
- 如果已有 `pipeline_run / story / digest`，脚本会 failfast
- 真实日更仍由长期运行的 `scheduler` 容器负责

### 恢复已导出的 seed

如果不想在服务器上重新跑初始化采集链路，可以直接恢复仓库里导出的 Postgres seed：

```bash
sha256sum -c backend/seeds/init_seed_2026-04-13.dump.sha256

export PGPASSWORD='your-postgres-password'
pg_restore \
  --host=your-postgres-host \
  --port=5432 \
  --username=karl \
  --dbname=karlfeed \
  --clean --if-exists --no-owner --no-privileges \
  backend/seeds/init_seed_2026-04-13.dump
```

### 服务器更新

```bash
git pull --ff-only
docker compose -f docker-compose.app.yml up --build -d backend worker scheduler frontend
```

## 4. 内容 runtime 启动

### 启动 worker

```bash
backend/.venv/bin/python backend/app/scripts/run_celery_worker.py
```

### 启动 coordinator loop

```bash
backend/.venv/bin/python backend/app/scripts/run_daily_coordinator.py
```

限制信源范围：

```bash
backend/.venv/bin/python backend/app/scripts/run_daily_coordinator.py --source-name Vogue --limit-sources 1
```

## 5. 本地 review run

同步跑当天 pipeline：

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect
```

常用参数：

- `--published-today-only`
- `--output-dir /tmp/today-digest-review`
- `--llm-artifact-dir /tmp/llm-artifacts`
- `--source-name NAME`
- `--limit-sources N`

review bundle 默认输出到：

`backend/runtime_reviews/<business_day>/`

包含：

- `digests.json`
- `articles.json`
- `summary.md`

## 6. 联调核验清单

### API 存活

- 打开 `/docs`
- `GET /api/v1/digests/feed` 返回 200
- `GET /api/v1/auth/feishu/browser/start` 返回 307，并跳转到 Feishu authorize 页面
- `POST /api/v1/auth/dev/token` 仅 `dev-root` 可拿到 token

### 前端

- 首页飞书登录成功
- `/auth/complete` 会接收 JWT 后立即清理 URL 中的 `token` 参数
- `__dev/login` 只允许 `dev-root` 调试登录
- Discover 能显示 digest
- 点进 Story 页面可见正文和来源
- Chat 可创建新会话
- Deep Research 可流式返回

### 运行态

- `pipeline_run` 有当日记录
- `source_run_state` 能随采集推进更新
- 当日完成后 `digest` 表有结果
- `metadata_json.rag_upserted=true`

## 7. 测试命令

### 后端

```bash
backend/.venv/bin/pytest backend/tests
```

### 前端类型检查

```bash
cd frontend
pnpm check
```

## 8. 常见问题

### `/docs` 起不来

优先检查：

- `AUTH_JWT_SECRET` 是否存在
- Postgres 是否可连
- Deep research graph 初始化依赖是否满足

### 登录失败

优先检查：

- JWT secret 是否与当前运行实例一致
- 浏览器里旧 token 是否需要清掉
- 飞书应用是否已配置正确的 Redirect URL 和所需 scope
- 普通用户是否确实从 `/` 走飞书登录，而不是尝试本地密码
- 飞书网页应用入口是否真的在客户端容器内打开，并且 H5 JSSDK 已成功注入

### 飞书授权页报 20029 / redirect URL 有误

优先检查：

- `FEISHU_BROWSER_REDIRECT_URI` 和飞书后台 Redirect URL 是否完全一致
- 当前访问域名、协议、端口是否与回调配置一致
- 反向代理后的公网地址是否已经替换掉本地 `3000/8000` 地址

### 飞书授权页报 20043 / scope 有误，或回调后提示缺少 `user_id` / `name`

优先检查：

- 飞书应用权限是否已开启并发布
- 浏览器 OAuth scope 与 `requestAccess` scope 是否和前后端环境变量一致
- 用户信息接口所需权限是否包含基础资料与 `user_id`

### Chat 图片打不开

优先检查：

- `CHAT_ATTACHMENT_ROOT`
- 附件文件是否真实存在
- 当前 token 是否过期

### RAG / Deep Research 报外部搜索错误

优先检查：

- `BRAVE_API_KEY`
- `TAVILY_API_KEY`
- Qdrant 是否可达

## 9. 当前运维注意事项

- 根目录 `package.json` 不是当前推荐启动入口
- 业务日按上海时区切，scheduler 首次开跑门槛是 `Asia/Shanghai 07:00`
- `docs/task*.md` 不是 runbook
