# Fashion Feed 重构计划

## 1. 目标

Fashion Feed 将从静态 JSON 驱动的资讯聚合站，重构为前后端分离、具备持久化与 AI 检索能力的企业内部产品。

最终目标：

- 保留现有首页信息流体验
- 引入 `FastAPI` 后端服务
- 以 `PostgreSQL` 作为业务真相源
- 以 `Milvus` 作为内容与记忆检索层
- 通过 Feishu 登录控制企业内部访问
- 支持全局 AI 与 story 内上下文 AI

本文件是唯一主计划文档。

## 2. 产品目标形态

产品最终需要支持：

- 首页信息流，类似 Perplexity Discover 的浏览体验
- story 详情页，展示多来源聚合结果
- 左侧全局 AI sidebar，支持历史 session
- story 详情页底部的上下文问答入口
- 带 citation 的 RAG 回答
- 长期用户记忆与个性化能力

## 3. 核心架构决策

### 3.1 系统拆分

- `frontend/` 保留为前端应用
- `backend/` 作为统一后端目录
- `backend/app/` 作为 FastAPI 应用主目录，承载 API、schema、service 与任务编排入口
- `backend/app/config/` 作为后端易变服务配置包，当前集中维护 embedding 与 Milvus 等配置
- `backend/app/core/` 作为稳定基础设施目录，承载数据库与 Redis 等底层连接能力；`database.py` 负责 engine/session/Base
- `backend/app/models/` 作为 SQLAlchemy ORM models 目录，按领域拆分文件
- `backend/app/router/` 作为 FastAPI 路由入口目录
- `backend/app/schema/` 作为 API request/response schema 目录
- `backend/app/scripts/` 作为应用内任务入口与可复用脚本目录
- `backend/app/service/news_collection_service.py` 当前已重写 article collection 的 source loading、采集、去重与富化链路，先返回内存 article 列表
- `backend/app/service/document_ingestion_service.py` 当前已接入 PostgreSQL `document` 持久化，负责数据库查重、字段映射与批量入库
- `backend/test/` 作为后端统一测试入口
- `backend/server/` 是遗留托管层，切流后逐步移除
- `backend/scripts/` 在迁移期保留，后续逐步拆入任务系统
- `backend/product.md` 作为面向后端开发者的产品文档

### 3.2 数据职责边界

- YAML：
  - `sources.yaml`
  - 组织 allowlist 与认证配置
- PostgreSQL：
  - 用户、登录事件、文档、资产
  - story 稳定身份与不可变聚合记录
  - chat session、message、citation
  - 用户画像与长期记忆主记录
  - 来源运行状态
- Milvus：
  - `content_text_unit`
  - `content_image_unit`
  - `user_memory`
  - `user_profile_memory`
- Env:
  - `.env` 存储服务端密钥与连接配置
  - `backend/app/config/` 负责模型与服务配置
  - `backend/app/core/` 负责数据库与 Redis 基础设施；数据库主契约为 `POSTGRES_*`

### 3.3 Story 身份与不可变聚合

- 稳定 story 身份使用 `story_key`
- 每个 `story_key` 对应一条不可变的 story 聚合记录
- `story` 与 `story_article` 保存该 story 创建时的固定聚合结果
- 首页和 story 详情读取不可变 story 内容
- story 聊天只绑定 `story_key`

### 3.4 运行策略

- `08:00`：
  - 执行早间批量采集与聚合
  - 处理上次成功运行之后新增的文档
  - 生成新的不可变 story
- 聚合流程：
  - 先入库新文档
  - 再生成文档与图片检索单元
  - 基于本轮新增文档生成新的 `story` 与 `story_article`
  - 最后刷新 feed 读模型

### 3.5 目标后端目录

```text
backend/
├─ app/
├─ test/
├─ scripts/
├─ server/
├─ schema.md
└─ product.md
```

目录职责：

- `app/`：后端主应用代码，后续 FastAPI、schema、service 与任务入口统一放在这里
- `test/`：后端统一测试目录，覆盖 API、数据模型、脚本与回归验证
- `scripts/`：迁移期保留的 Python 采集脚本
- `server/`：遗留 Node 托管层，不再承接新增后端能力
- `schema.md`：后端数据模型设计说明
- `product.md`：面向后端开发者的产品文档

## 4. Schema 冻结范围

### 4.1 YAML

- `sources.yaml`
- 认证与租户 allowlist 配置

### 4.2 Milvus collections

- `content_text_unit`
- `content_image_unit`
- `user_memory`
- `user_profile_memory`

### 4.3 PostgreSQL tables

- `organization`
- `app_user`
- `auth_login_event`
- `document`
- `document_asset`
- `retrieval_unit_ref`
- `story`
- `story_article`
- `chat_session`
- `chat_message`
- `message_citation`
- `tool_execution`
- `user_profile`
- `user_memory_record`
- `memory_write_log`
- `source_runtime_state`

### 4.4 不允许回退的规则

- `v1` 不新增 `content_source` SQL 主表
- 不允许使用混合 raw 文档和聚合语义的 `article` 主表
- 不允许把长期记忆只放在 Milvus
- 不允许把临时聚合 story ID 当对外 topic 稳定标识

## 5. API 基线

首批稳定 API：

- `GET /api/v1/feed/home`
- `GET /api/v1/topics/{story_key}`
- `GET /api/v1/chat/sessions`
- `POST /api/v1/chat/sessions`
- `GET /api/v1/chat/sessions/{session_id}/messages`
- `POST /api/v1/chat/sessions/{session_id}/messages`
- `GET /api/v1/auth/login`
- `GET /api/v1/auth/callback`
- `GET /api/v1/auth/me`

Chat 请求必须支持：

- `scope_type`
- `scope_ref_key`
- 用户消息内容

AI 回答必须返回：

- answer 内容
- citations
- source references
- image citations / preview metadata（命中 image asset 时）
- session 元数据

## 6. 交付阶段

### Phase 0：文档与 schema 冻结

交付：

- `AGENTS.md`
- `plan.md`
- `docs/` 文档集

退出条件：

- 所有文档中的 story、document、unit、memory 等术语一致
- 数据职责边界冻结

### Phase 1：后端骨架与认证

交付：

- `backend/app/` 项目骨架
- `backend/test/` 测试骨架
- `backend/product.md`
- Feishu 登录设计占位
- 基础 PostgreSQL 模型

退出条件：

- 后端骨架可运行
- 文档与数据术语一致

### Phase 2：文档入库与检索单元

交付：

- `document` 入 PostgreSQL
- `article_id` 作为主键，`canonical_url` 作为数据库幂等键
- 清洗后的正文写入 Markdown 文件，数据库仅保存 `content_md_path`
- `backend/main.py` 提供 `init-db` / `ingest-documents` 手动入口
- text/image retrieval unit 生成
- image asset 异步 enrichment
- `content_text_unit` 与 `content_image_unit` 写入 Milvus

退出条件：

- 重复执行文档入库不会重复写入同一 `canonical_url`
- 任一文档与图片资产都可追溯到对应 retrieval units

### Phase 3：Story 聚合层

交付：

- 不可变 `story` / `story_article`
- API 化首页数据

退出条件：

- 每个 `story_key` 对应固定聚合内容
- 首页只依赖不可变 story 读模型

### Phase 4：AI、session 与 memory

交付：

- chat session/message
- citation 持久化
- `user_memory_record` 与 memory 检索副本
- story 内问答与全局 AI

退出条件：

- story 问答能返回带 citation 的回答
- 长期记忆可写入并可检索

### Phase 5：前端切流

交付：

- 首页从 JSON 切换到 API
- 左侧 AI sidebar
- story 底部上下文问答入口

退出条件：

- 现有信息流体验保持稳定
- API 安全替换静态 feed

## 7. 主要风险与控制

### 风险：story 碎裂

控制：

- 使用稳定 `story_key`
- 明确“相似后续事件可形成独立 story”是产品设计
- 让检索与 citation 真相源仍然回到 `document` / `unit`

### 风险：首页切流回归

控制：

- 迁移期保留 `feed-data.json`
- 对比 API 与当前 feed 输出结构

### 风险：memory 漂移

控制：

- PostgreSQL 主存
- Milvus 检索副本
- 明确版本和写入链路

### 风险：运行不可观测

控制：

- 记录 `source_runtime_state`
- 保留采集与聚合日志
- 文档化重跑与恢复流程

## 8. 验收清单

- 文档完整且术语统一
- schema 足够稳定，可启动后端搭建
- auth、feed、topic、chat 的边界清楚
- story 不可变语义明确建模
- memory 在 SQL 与 Milvus 之间职责分离清楚
