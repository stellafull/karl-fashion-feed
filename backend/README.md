# Backend

## 当前定位

后端当前维护的是一条以 `digest` 为公共输出的运行主链路：

`article -> article_event_frame -> story -> digest`

同时对外提供：

- `auth`
- `digests`
- `chat`
- `deep-research`
- `memories`
- `rag`

旧的静态 feed 输出、root/root 本地多账号登录、Milvus 检索等描述都不再适用于当前代码。

## 公开 API

FastAPI 入口在 [backend/app/app_main.py](/home/czy/karl-fashion-feed/backend/app/app_main.py)。

当前挂载路由：

- `/api/v1/auth`
- `/api/v1/chat`
- `/api/v1/deep-research`
- `/api/v1/memories`
- `/api/v1/rag`
- `/api/v1/digests`

关键接口：

- `POST /api/v1/auth/feishu/client/exchange`：交换飞书客户端 `requestAccess` code，返回 JWT
- `GET /api/v1/auth/feishu/browser/start`：浏览器跳转到飞书 OAuth 授权页
- `GET /api/v1/auth/feishu/browser/callback`：处理飞书 OAuth 回调并重定向回前端 `/auth/complete`
- `POST /api/v1/auth/dev/token`：仅供 `dev-root` 调试登录
- `GET /api/v1/auth/me`：读取当前用户
- `GET /api/v1/digests/feed`：Discover 卡片列表
- `GET /api/v1/digests/{digest_key}`：Digest 详情
- `POST /api/v1/chat/messages/stream`：普通聊天 SSE
- `POST /api/v1/deep-research/messages/stream`：深度研究 SSE
- `GET/POST/PATCH/DELETE /api/v1/memories`：长时记忆 CRUD
- `POST /api/v1/rag/query`：单次 RAG 查询

## 核心不变量

- `article` 是事实真相源
- `canonical_url` 是文章唯一去重键
- 正文写入本地 markdown，数据库只存相对路径
- `article_image` 保存来源图片 URL 和来源文本，不保存二进制真相
- `article_event_frame` 是最小可回放事件单元
- `story` 是内部聚合层
- `digest` 是唯一 public read model
- Redis 只负责 broker / 锁 / 短期协调
- Qdrant 只是检索副本，引用和回源必须回到 Postgres

## 运行链路

### 内容生产

1. `NewsCollectionService`
   从 `sources.yaml` 加载 RSS / web 信源，收集 article seed

2. `ArticleCollectionService`
   执行 canonical URL 去重，写入 `article`

3. `ArticleParseService`
   解析详情页，写正文 markdown 和 `article_image`

4. `EventFrameExtractionService`
   从正文抽取 `article_event_frame`

5. `StoryClusteringService`
   基于 business day 聚 story

6. `StoryFacetAssignmentService`
   给 story 分配运行时 facet

7. `DigestPackagingService`
   把 story 组合成 digest plan

8. `DigestReportWritingService`
   调 LLM 生成最终中文 digest

9. `ArticleRagService`
   将 article / article_image 写入共享 Qdrant collection

### 在线问答

- 普通 chat：`ChatWorkerService`
- deep research：`DeepResearchService` + `DeepResearchGraphService`
- retrieval：`RagAnswerService` + `RagTools` + `QueryService`

## 认证约定

- 普通用户统一从飞书组织登录进入系统
- 飞书客户端内会先等待官方 H5 JSSDK `window.h5sdk.ready()`，再优先走 `tt.requestAccess`，必要时回退到 `tt.requestAuthCode`
- 外部浏览器走授权页跳转
- 飞书 `user_id` 是当前 phase-1 唯一外部身份键，对应本地 `user.feishu_user_id`
- 首次飞书登录会自动创建本地 `user`，后续 chat / memory / session 继续挂在同一个 `user_id`
- 正常登录页只展示飞书登录；本地账号只保留隐藏 dev 入口 `POST /api/v1/auth/dev/token` + 前端 `/__dev/login`
- `dev-root` 是唯一允许继续走本地密码登录的账号
- 后端会把飞书头像映射为 `avatar_url` 返回给前端展示

## 数据模型

### 内容域

- `article`
- `article_image`
- `article_event_frame`
- `story`
- `story_frame`
- `story_article`
- `story_facet`
- `digest`
- `digest_story`
- `digest_article`

### 用户域

- `user`（以本地 `user_id` 为真相主键；Feishu 用户额外挂 `feishu_user_id/open_id/union_id/avatar`）
- `chat_session`
- `chat_message`
- `chat_attachment`
- `long_term_memory`

### 运行态

- `pipeline_run`
- `source_run_state`

## 时间语义

- `business_day` 使用 `Asia/Shanghai`
- `utc_bounds_for_business_day()` 用上海自然日推导 UTC 窗口
- `SchedulerService` 当前在“没有现有 run”时，会等到 `Asia/Shanghai 07:00` 才启动当日 pipeline

## 本地启动

### API

```bash
backend/.venv/bin/uvicorn backend.app.app_main:app --reload --host 0.0.0.0 --port 8000
```

### 初始化本地调试账号

```bash
backend/.venv/bin/python backend/app/scripts/init_root_user.py
```

当前脚本只初始化 `dev-root / dev-root`，用于隐藏 dev 登录页和 smoke 测试。

### Celery worker

```bash
backend/.venv/bin/python backend/app/scripts/run_celery_worker.py
```

### Coordinator loop

```bash
backend/.venv/bin/python backend/app/scripts/run_daily_coordinator.py
```

### 本地同步 review run

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect
```

可选参数：

- `--source-name NAME`
- `--limit-sources N`
- `--published-today-only`
- `--output-dir PATH`
- `--llm-artifact-dir PATH`

## Docker 部署

仓库根目录提供 `docker-compose.app.yml`，只部署应用层：

- `backend`
- `worker`
- `scheduler`
- `frontend`

不会部署：

- Postgres
- Redis
- Qdrant

部署前：

```bash
cp .env.example .env
```

启动：

```bash
docker compose -f docker-compose.app.yml up --build -d backend worker scheduler frontend
```

为空库注入 demo digest：

```bash
docker compose -f docker-compose.app.yml --profile init run --rm demo-init
```

如果需要直接恢复已经导出的初始化数据，而不是重跑采集链路：

```bash
export PGPASSWORD='your-postgres-password'
pg_restore \
  --host=your-postgres-host \
  --port=5432 \
  --username=karl \
  --dbname=karlfeed \
  --clean --if-exists --no-owner --no-privileges \
  backend/seeds/init_seed_2026-04-13.dump
```

## 关键环境变量

### 基础

- `AUTH_JWT_SECRET`
- `CORS_ALLOWED_ORIGINS`
- `CHAT_ATTACHMENT_ROOT`
- `AUTH_ACCESS_TOKEN_EXPIRE_MINUTES`
- `AUTH_BROWSER_STATE_EXPIRE_SECONDS`

### Feishu Auth

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BROWSER_REDIRECT_URI`
- `FEISHU_FRONTEND_AUTH_COMPLETE_URL`
- `FEISHU_OAUTH_SCOPE`（浏览器授权页 scope，当前实现按空格拼接）
- `FEISHU_REQUEST_TIMEOUT_SECONDS`

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

### LLM / Embedding

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `STORY_SUMMARIZATION_MODEL`
- `RAG_MODEL` / `RAG_CHAT_MODEL`
- `DENSE_EMBEDDING_MODEL`
- `DENSE_EMBEDDING_DIMENSION`
- `SPARSE_EMBEDDING_MODEL`
- `RERANKER_MODEL`

### Retrieval / Web Search

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `BRAVE_API_KEY`
- `TAVILY_API_KEY`

## 测试

后端测试位于 [backend/tests](/home/czy/karl-fashion-feed/backend/tests)，覆盖：

- auth
- digest runtime
- chat / deep research
- rag answer
- prompt / LLM contract
- scheduler / celery config

建议命令：

```bash
backend/.venv/bin/pytest backend/tests
```

## 明确不是当前真相的内容

- 旧静态 feed-data.json 流程
- `root / root`、`ROOT1 / ROOT1` 之类的本地多账号登录说明
- Milvus 作为当前检索主库
- “旧 story 已是唯一 public read model”的旧文档口径
