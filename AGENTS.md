# Fashion Feed 协作指南

## 文档目的

本仓库正在从“静态资讯流项目”重构为“前后端分离的持久化服务”。

目标形态如下：

- `frontend/` 保留为用户可见的前端产品界面
- `backend/` 作为后端统一目录，承载服务层与脚本
- `PostgreSQL` 作为业务数据真相源
- `Milvus` 作为内容检索与长期记忆检索层
- `Redis + Celery` 负责异步任务

本文件用于约束后续工程师和智能代理在仓库内的协作方式。

## 当前仓库结构

- `frontend/`：现有 React + Vite 前端
- `backend/server/`：遗留 Node 静态托管层，仅迁移期保留
- `backend/scripts/`：当前 Python 采集与聚合脚本
- `shared/`：共享常量
- `docs/`：重构后的产品与工程文档
- `plan.md`：本项目唯一主计划文档

## 目标架构约束

### 真相源划分

- `sources.yaml` 仍然是采集配置真相源。`v1` 不允许新增 `content_source` SQL 主表。
- `PostgreSQL` 存储用户、登录事件、文档、story 发布快照、聊天、引用、记忆主记录和运行元数据。
- `Milvus` 只负责检索实体，不负责 story 真相源，也不负责短期聊天状态。
- `feed-data.json` 在迁移期继续保留，直到前端完全切到 API。

### Story 身份规则

- Story 必须使用稳定的 `story_key`。
- 快照身份由 `run_id` 表示发布版本，不是稳定 story 身份。
- Story 范围内的聊天和引用，在需要回放时必须同时携带 `story_key` 和 `scope_snapshot_run_id`。
- 不允许把 `story_cluster_snapshot` 当作长期主表。

### Memory 规则

- 短期记忆保存在 PostgreSQL 的 session/message 表中。
- 长期记忆必须有 PostgreSQL 主表，同时在 Milvus 中保留可检索副本。
- 不允许把原始聊天记录直接镜像到 Milvus 充当唯一的用户记忆。

### Retrieval 规则

- `content_unit` 是核心内容检索 collection。
- 检索实体按 chunk 或资产粒度建模，不按 article 粒度建模。
- `content_unit` 必须支持 hybrid retrieval，`user_memory` 也应支持 hybrid retrieval。
- Story 成员关系会变化，SQL 快照成员关系才是真相源；Milvus 中的 story 字段只能作为冗余过滤字段。

### 认证规则

- `v1` 只支持 Feishu 登录。
- 服务端必须执行 `tenant_key` allowlist 校验。
- 所有登录尝试必须可审计。

## 迁移期护栏

- 在 API 和当前 `feed-data.json` 契约对齐前，不允许移除现有前端信息流主路径。
- 不允许让前端路由或会话恢复直接依赖不稳定的 per-run snapshot ID。
- 不允许直接覆盖历史发布结果，必须通过切换 `published_run` 完成发布。
- 原始 HTML 和大文件资产不入 PostgreSQL，只保存路径或 URL。

## 目标目录

```text
/
├─ backend/
│  ├─ app/
│  ├─ alembic/
│  ├─ scripts/
│  ├─ server/
│  └─ tests/
├─ frontend/
├─ docs/
├─ AGENTS.md
└─ plan.md
```

## 必须同步维护的文档

当架构或 schema 发生变化时，必须同步更新：

- `plan.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/api-contract.md`
- `docs/ops-runbook.md`

当用户可见行为发生变化时，还必须更新：

- `docs/product-overview.md`
- `docs/internal-user-guide.md`

## 默认技术选型

- 后端框架：FastAPI
- 任务系统：Celery + Redis
- 主数据库：PostgreSQL
- 向量数据库：Milvus
- 聊天模型调用层：OpenAI-compatible 抽象
- Embedding 方案：支持多模态的适配层，规划接入 DashScope/Qwen
- 发布节奏：
  - `08:00`：日重聚类
  - `10:00` 到 `18:00` 每 2 小时：增量采集与刷新

## 质量门槛

任何后端或数据层改动在完成前，至少满足：

- schema 术语与 `docs/data-model.md` 一致
- API 输出与 `docs/api-contract.md` 一致
- story 跨 run 连续性可验证
- citation 能从 answer -> unit -> document -> source 完整回溯
- 新增运行命令或部署依赖时，必须同步写入 `docs/ops-runbook.md`
