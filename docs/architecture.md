# 系统架构

本文档只描述当前代码已经实现并仍在主路径上的架构。

## 1. 总览

```text
frontend (React + Vite)
    |
    |  /api/v1
    v
backend (FastAPI)
    |-- PostgreSQL        # article/story/digest/chat/memory/runtime 真相源
    |-- Redis             # Celery broker / locks / coordination
    |-- Celery Worker     # content + aggregation + scheduler tasks
    |-- Qdrant            # text/image retrieval 副本
    |-- LangGraph runtime # deep research graph
    |
    +-- local file storage
         |- data/articles          # article markdown
         |- backend/data/chat_attachments
```

## 2. 前端架构

入口：

- [frontend/src/main.tsx](/home/czy/karl-fashion-feed/frontend/src/main.tsx)
- [frontend/src/App.tsx](/home/czy/karl-fashion-feed/frontend/src/App.tsx)
- [frontend/src/components/AppShell.tsx](/home/czy/karl-fashion-feed/frontend/src/components/AppShell.tsx)

当前实际路由：

- `/discover`
- `/stories/:storyId`
- `/chat/new`
- `/chat/:sessionId`
- `/`

页面职责：

- `DiscoverPage`：digest feed 浏览、分类、来源筛选、排序
- `StoryPage`：digest detail、来源列表、就地追问 / deep research
- `ChatPage`：普通 chat / deep research 会话

状态组织：

- `useAuth()`：飞书登录 / 浏览器回调 / 隐藏 dev 登录页的 JWT 会话态
- `useFeedData()`：digest feed 读取与前端映射
- `useChatSessions()`：session、message、SSE streaming、中断

## 2.5 认证链路

```text
正常用户
  Feishu client -> tt.requestAccess -> POST /auth/feishu/client/exchange
  Browser      -> GET /auth/feishu/browser/start
                -> Feishu authorize
                -> GET /auth/feishu/browser/callback
                -> 307 /auth/complete?token=...
                -> 前端写入 localStorage 并清理 URL

dev 调试
  /__dev/login -> POST /auth/dev/token (only dev-root)
```

说明：

- 本地 `user_id` 仍是 chat / memory / session 的真相外键
- Feishu `user_id` 落到 `user.feishu_user_id`，作为当前唯一外部身份键
- 首次飞书登录会自动创建本地 `user`；后续更新 display name、email、avatar

## 3. 后端架构

入口：

- [backend/app/app_main.py](/home/czy/karl-fashion-feed/backend/app/app_main.py)

模块边界：

- `router/`：HTTP 层
- `service/`：业务编排
- `models/`：ORM
- `schemas/`：Pydantic 契约
- `tasks/`：Celery task 边界
- `scripts/`：本地运维 / review / worker 启动

### 公开路由

- `auth_router`
- `digest_router`
- `chat_router`
- `deep_research_router`
- `memory_router`
- `rag_router`

### 核心服务

- `DailyRunCoordinatorService`
- `StoryClusteringService`
- `StoryFacetAssignmentService`
- `DigestPackagingService`
- `DigestReportWritingService`
- `ChatWorkerService`
- `DeepResearchService`
- `RagAnswerService`
- `ArticleRagService`

## 4. 内容生产链路

```text
sources.yaml
  -> NewsCollectionService
  -> ArticleCollectionService
  -> ArticleParseService
  -> EventFrameExtractionService
  -> StoryClusteringService
  -> StoryFacetAssignmentService
  -> DigestPackagingService
  -> DigestReportWritingService
  -> Digest
```

说明：

- `article` 是事实真相源
- `story` 是同 business day 聚合层
- `digest` 是对外阅读层
- `ArticleRagService` 在 pipeline 完成后把内容写入 Qdrant

## 5. Chat / Research 架构

### 普通聊天

```text
POST /chat/messages/stream
  -> create_message_round
  -> ChatWorkerService
  -> RagAnswerService
  -> SSE delta / complete / interrupted
```

能力：

- 复用会话上下文
- 支持图片附件
- 支持 story context 隐式注入
- 支持中断

### Deep Research

```text
POST /deep-research/messages/stream
  -> create_message_round
  -> DeepResearchService
  -> DeepResearchGraphService.graph
  -> SSE phase events + final answer
```

特点：

- 使用独立 `message_type=deep_research`
- 支持复用 thread_id 做 clarification loop
- 仍然持久化到现有 `chat_session` / `chat_message`

## 6. RAG 架构

Qdrant 中使用单共享 collection，靠 `modality` 区分 text / image。

### 内部检索

- dense embedding
- sparse embedding
- hybrid retrieval
- rerank
- article / image package 组装

### 外部补充

- Tavily：通用 web search
- Brave：web / image / llm context search

使用场景：

- 普通文本问答
- 视觉导向问答
- 上传图片后做 image-assisted retrieval
- 内部证据不足时追加 web fallback

## 7. 运行与调度

### Celery 队列

- `content`
- `aggregation`
- `scheduler`

### 调度职责

- `DailyRunCoordinatorService`：reclaim stale state、分发 content/aggregation task、收敛当日 run
- `SchedulerService`：周期 tick、启动门槛控制、pipeline 完成后触发 RAG upsert

### 时间语义

- business day：`Asia/Shanghai`
- 首次启动门槛：`Asia/Shanghai 07:00`

## 8. 遗留区域

以下内容存在于仓库，但不是当前主运行路径：

- 根目录 Node `build` 脚本引用的 `backend/server/index.ts`
- `frontend/src/pages/Home.tsx` 老页面
- `docs/task*.md` 历史任务记录

这些内容不应再作为理解系统的起点。
