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

## 基础鉴权

- `AUTH_JWT_SECRET`
- `AUTH_JWT_ALGORITHM`
- `AUTH_ACCESS_TOKEN_EXPIRE_MINUTES`
- `CORS_ALLOWED_ORIGINS`
- `CHAT_ATTACHMENT_ROOT`

## Postgres

- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

## Redis / Celery

- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_PASSWORD`

## LLM

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `USE_RESPONSES_API`
- `STORY_SUMMARIZATION_MODEL`
- `RAG_MODEL` 或 `RAG_CHAT_MODEL`

## Embedding / Rerank

- `DENSE_EMBEDDING_MODEL`
- `DENSE_EMBEDDING_DIMENSION`
- `SPARSE_EMBEDDING_MODEL`
- `RERANKER_MODEL`

## 检索

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `BRAVE_API_KEY`
- `TAVILY_API_KEY`

## 3. 本地启动顺序

### 后端 API

```bash
backend/.venv/bin/uvicorn backend.app.app_main:app --reload --host 0.0.0.0 --port 8000
```

### 初始化本地账号

```bash
backend/.venv/bin/python backend/app/scripts/init_root_user.py
```

### 前端

```bash
cd frontend
pnpm install
pnpm dev
```

默认访问：

- 前端：`http://127.0.0.1:3000`
- 后端 docs：`http://127.0.0.1:8000/docs`

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
- `POST /api/v1/auth/token` 能拿到 token

### 前端

- 登录成功
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

- 是否已执行 `init_root_user.py`
- JWT secret 是否与当前运行实例一致
- 浏览器里旧 token 是否需要清掉

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
- 业务日按上海时区切，但 scheduler 首次开跑门槛仍是 Sydney 9 点
- `docs/task*.md` 不是 runbook
