# KARL Fashion Feed

KARL Fashion Feed 是一个面向中国区同事的时尚资讯平台。当前代码已经不是早期的“静态 RSS JSON 站点”，而是一个完整的前后端应用：

- 前端：React 19 + Vite，提供登录、Discover、专题详情、Chat、Deep Research
- 后端：FastAPI + PostgreSQL，提供鉴权、digest feed、chat、memory、RAG、deep research
- 内容链路：采集 article -> 解析正文/图片 -> 抽取 event frame -> 聚合 story -> 生成 public digest
- 检索链路：基于 article / article_image 写入 Qdrant，共享 text/image retrieval collection

## 当前真实能力

- 本地账号密码登录，JWT 鉴权
- Discover 页面读取 `GET /api/v1/digests/feed`
- Digest 详情页读取 `GET /api/v1/digests/{digest_key}`
- Chat 支持历史会话、图片上传、SSE 流式回答、中断
- Story 页支持携带 story context 发起普通追问或 deep research
- Deep Research 走 LangGraph runtime，流式返回阶段事件和最终回答
- RAG 支持文本检索、图搜图/以图辅文、外部 web/image search fallback
- Daily digest runtime 通过 coordinator + Celery 驱动内容生产

## 仓库结构

```text
.
├─ backend/
│  ├─ app/
│  │  ├─ config/      # LLM、embedding、搜索、Celery、sources 配置
│  │  ├─ core/        # 数据库、鉴权、安全、Redis
│  │  ├─ models/      # article / event_frame / story / digest / chat ORM
│  │  ├─ router/      # FastAPI 路由
│  │  ├─ schemas/     # Pydantic 契约
│  │  ├─ service/     # digest runtime、chat、deep research、RAG
│  │  ├─ scripts/     # 本地运行和运维脚本
│  │  ├─ tasks/       # Celery tasks
│  │  └─ sources.yaml # 信源配置
│  ├─ tests/          # 后端测试
│  └─ .venv/          # 当前已存在的本地 Python 环境
├─ frontend/
│  ├─ src/
│  │  ├─ components/
│  │  ├─ hooks/
│  │  ├─ lib/
│  │  └─ pages/
│  └─ vite.config.ts
├─ docs/
│  ├─ architecture.md
│  ├─ product-overview.md
│  ├─ ops-runbook.md
│  └─ internal-user-guide.md
└─ data/
   └─ articles/       # article markdown 等本地落盘数据
```

## 核心数据模型

- `article`：事实真相源；保存来源 metadata、正文 markdown 相对路径、parse/event-frame 状态
- `article_image`：图片 URL、位置、来源文本、可选视觉分析字段
- `article_event_frame`：从正文抽出的最小事件单元
- `story`：按 business day 聚合出的内部故事单元
- `digest`：唯一 public read model，Discover 和 Story 页面都围绕它读取
- `chat_session` / `chat_message` / `chat_attachment`：用户会话、消息和附件
- `long_term_memory`：用户 memory
- `pipeline_run` / `source_run_state`：每日运行态，不承载业务真相

## 当前日处理链路

1. `NewsCollectionService` 从 `sources.yaml` 读取 RSS / web 信源，收集 article seed
2. `ArticleCollectionService` 做 canonical URL 去重并写入 `article`
3. `ArticleParseService` 解析正文、主图和 `article_image`
4. `EventFrameExtractionService` 抽取 `article_event_frame`
5. `StoryClusteringService` 基于同一 business day 的 frame 聚 story
6. `StoryFacetAssignmentService` 给 story 打 facet
7. `DigestPackagingService` 生成 digest 计划
8. `DigestReportWritingService` 写入最终中文 digest
9. `ArticleRagService` 把 parse-complete / event-frame-complete 内容 upsert 到 Qdrant

## 本地开发

### 前端

```bash
cd frontend
pnpm install
pnpm dev
```

默认监听 `:3000`，并把 `/api/v1` 代理到 `http://127.0.0.1:8000`。

### 后端 API

```bash
backend/.venv/bin/uvicorn backend.app.app_main:app --reload --host 0.0.0.0 --port 8000
```

### 初始化本地登录账号

```bash
backend/.venv/bin/python backend/app/scripts/init_root_user.py
```

会确保以下账号存在：

- `root / root`
- `ROOT1 / ROOT1`
- `ROOT2 / ROOT2`

### 启动内容 runtime

```bash
backend/.venv/bin/python backend/app/scripts/run_celery_worker.py
backend/.venv/bin/python backend/app/scripts/run_daily_coordinator.py
```

### 本地同步跑当天 digest pipeline

```bash
backend/.venv/bin/python backend/app/scripts/dev_run_today_digest_pipeline.py --skip-collect
```

## 文档入口

- 项目总览：`README_PROJECT.md`
- 架构说明：[docs/architecture.md](/home/czy/karl-fashion-feed/docs/architecture.md)
- 产品与能力：[docs/product-overview.md](/home/czy/karl-fashion-feed/docs/product-overview.md)
- 运维手册：[docs/ops-runbook.md](/home/czy/karl-fashion-feed/docs/ops-runbook.md)
- 用户手册：[docs/internal-user-guide.md](/home/czy/karl-fashion-feed/docs/internal-user-guide.md)
- 后端约定：[backend/README.md](/home/czy/karl-fashion-feed/backend/README.md)

## 当前遗留与风险

- 根目录 `package.json` 仍保留旧的 Node bundling 脚本，`build` 指向缺失的 `backend/server/index.ts`，不是当前推荐运行路径
- `docs/task*.md` 属于历史任务记录，不是当前系统说明
- `frontend/src/pages/Home.tsx` 仍在仓库中，但当前路由实际使用的是 `DiscoverPage`
- 调度器当前实现为“按 Asia/Shanghai 计算 business day，但首次开跑门槛是 Sydney 9 点”；这和顶层目标“北京时间 8 点”不一致，属于待收敛实现
