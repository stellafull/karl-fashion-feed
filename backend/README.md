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

旧的静态 feed 输出、Feishu 登录、Milvus 检索等描述都不再适用于当前代码。

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

- `POST /api/v1/auth/token`：本地账号登录，返回 JWT
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

- `user`
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
- `SchedulerService` 当前在“没有现有 run”时，会等到 `Australia/Sydney 09:00` 才启动当日 pipeline

最后一条是当前实现事实，不代表最终产品目标。

## 本地启动

### API

```bash
backend/.venv/bin/uvicorn backend.app.app_main:app --reload --host 0.0.0.0 --port 8000
```

### 初始化本地账号

```bash
backend/.venv/bin/python backend/app/scripts/init_root_user.py
```

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

## 关键环境变量

### 基础

- `AUTH_JWT_SECRET`
- `CORS_ALLOWED_ORIGINS`
- `CHAT_ATTACHMENT_ROOT`

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
- Feishu 登录说明
- Milvus 作为当前检索主库
- “旧 story 已是唯一 public read model”的旧文档口径
