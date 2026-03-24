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
- 左侧可收缩导航与全局 Chat workspace，支持历史 session
- story 详情页底部的上下文问答入口
- 带 citation 的 RAG 回答
- 长期用户记忆与个性化能力

## 3. 核心架构决策

### 3.1 系统拆分

- `frontend/` 保留为前端应用
- `backend/` 作为统一后端目录
- `backend/app/` 作为 FastAPI 应用主目录，承载 API、service、repository 与任务编排入口
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
  - story 稳定身份与发布快照
  - chat session、message、citation
  - 用户画像与长期记忆主记录
  - 运行状态与发布元数据
- Milvus：
  - `content_unit`
  - `user_memory`
  - `user_profile_memory`
- Env:
  - `.env`存储key等 

### 3.3 Story 身份与发布

- 稳定 story 身份使用 `story_key`
- 快照身份使用 `(run_id, story_key)`
- 首页永远读取当前 `published_run`
- story 聊天和回放可按需绑定进入时的 `scope_snapshot_run_id`

### 3.4 更新策略

- `08:00`：
  - 执行 `daily_recluster`
  - 对最近 72 小时 active window 重聚类
  - 尽量复用已存在的 `story_key`
- `10:00` 到 `18:00` 每 2 小时：
  - 执行 `incremental_update`
  - 采集新增文档
  - 合并到活跃 story 或创建新 story
- 发布流程：
  - 先写 run
  - 再做校验
  - 最后切换 `published_run`
  - 回滚通过切换回历史成功 run 完成

### 3.5 Story 连续性规则

新 cluster 复用旧 `story_key` 的顺序：

1. 代表文档命中
2. 成员文档至少重叠 2 篇，或重叠率 >= 30%
3. 标题/摘要向量相似度 >= 0.85 且标签重叠 >= 2
4. 否则新建 `story_key`

### 3.6 目标后端目录

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

- `app/`：后端主应用代码，后续 FastAPI、repository、service 与任务入口统一放在这里
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

- `content_unit`
- `user_memory`
- `user_profile_memory`

### 4.3 PostgreSQL tables

- `organization`
- `app_user`
- `auth_login_event`
- `document`
- `document_asset`
- `retrieval_unit_ref`
- `story_identity`
- `story_cluster_snapshot`
- `story_cluster_member_snapshot`
- `chat_session`
- `chat_message`
- `message_citation`
- `tool_execution`
- `user_profile`
- `user_memory_record`
- `memory_write_log`
- `source_runtime_state`
- `pipeline_run`
- `published_run`

### 4.4 不允许回退的规则

- `v1` 不新增 `content_source` SQL 主表
- 不允许使用混合 raw 文档和聚合语义的 `article` 主表
- 不允许把长期记忆只放在 Milvus
- 不允许把 per-run story ID 当对外 topic 稳定标识

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
- `scope_snapshot_run_id`
- 用户消息内容

AI 回答必须返回：

- answer 内容
- citations
- source references
- session 元数据

## 6. 交付阶段

### Phase 0：文档与 schema 冻结

交付：

- `AGENTS.md`
- `plan.md`
- `docs/` 文档集

退出条件：

- 所有文档中的 story、document、unit、memory、run 等术语一致
- 数据职责边界冻结

### Phase 1：后端骨架与认证

交付：

- `backend/app/` 项目骨架
- `backend/test/` 测试骨架
- `backend/product.md`
- Feishu 登录链路
- 基础 PostgreSQL 模型

退出条件：

- 登录可用
- tenant 校验可用
- 用户身份可持久化

### Phase 2：文档入库与检索单元

交付：

- document 入 PostgreSQL
- retrieval unit 生成
- `content_unit` 向量写入 Milvus

退出条件：

- 任一文档可追溯到对应 retrieval units

### Phase 3：Story 发布层

交付：

- story 身份解析
- 快照表
- run 发布控制
- API 化首页数据

退出条件：

- 稳定 `story_key` 跨重聚类不丢失
- 发布 run 可回滚

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
- 左侧可收缩导航与 Chat 主视图
- story 底部上下文问答入口

退出条件：

- 现有信息流体验保持稳定
- API 安全替换静态 feed

## 7. 主要风险与控制

### 风险：story 碎裂

控制：

- 使用稳定 `story_key`
- 72 小时 active window 重聚类
- 明确 story continuity 规则

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

- 显式 `pipeline_run`
- 显式 `published_run`
- 文档化回滚流程

## 8. 验收清单

- 文档完整且术语统一
- schema 足够稳定，可启动后端搭建
- auth、feed、topic、chat 的边界清楚
- story 连续性明确建模
- memory 在 SQL 与 Milvus 之间职责分离清楚
