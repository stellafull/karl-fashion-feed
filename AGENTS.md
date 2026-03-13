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
- `backend/main.py`：后端包级入口，占位 CLI 入口
- `backend/app/`：后端应用主目录，当前已包含 FastAPI ASGI 入口与配置加载
- `backend/test/`：后端统一测试入口，当前拆分为 `app/` 与 `scripts/`
- `backend/pyproject.toml`：后端 Python 依赖与项目配置真相源
- `backend/uv.lock`：后端依赖锁文件
- `backend/server/`：遗留 Node 静态托管层，仅迁移期保留
- `backend/scripts/`：当前 Python 采集与聚合脚本
- `backend/product.md`：面向后端开发者的产品文档
- `backend/schema.md`：后端 schema 设计说明
- `shared/`：共享常量
- `docs/`：重构后的产品与工程文档
- `plan.md`：本项目唯一主计划文档

## 当前后端实现约定

- `backend/app/main.py` 是 FastAPI ASGI 入口与 `create_app()` 工厂所在位置；新增 API 入口从这里向内扩展。
- `backend/app/config/` 负责外部服务与运行配置；配置模块可直接在模块内执行 dotenv bootstrap 并读取环境变量，不再新增集中式 `env.py` helper。
- `backend/app/core/` 负责稳定基础设施与底层能力，例如数据库、Redis、安全与认证客户端；`database.py` 保留为数据库基础文件。
- `backend/app/models/` 负责 SQLAlchemy ORM model 定义。
- `backend/app/service/` 负责业务编排，并可直接操作 ORM / Session；不再引入 `repository/` 作为正式分层。
- `backend/app/router/` 负责 FastAPI 路由与依赖注入入口。
- `backend/app/schema/` 负责 API request / response schema。
- `backend/app/scripts/` 负责应用内任务入口和可复用脚本编排。
- `backend/main.py` 仅作为后端包级入口或 CLI 占位，不承接 Web API 逻辑。
- `backend/test/app/` 存放 FastAPI 应用、路由与契约测试。
- `backend/test/scripts/` 存放采集脚本与数据处理回归测试。
- `backend/pyproject.toml` 与 `backend/uv.lock` 是后端依赖管理基线；新增后端依赖时优先更新这两处，而不是继续扩散到仓库根目录。

## 目标架构约束

### 真相源划分

- `sources.yaml` 仍然是采集配置真相源。`v1` 不允许新增 `content_source` SQL 主表。
- `PostgreSQL` 存储用户、登录事件、文档、不可变 story 聚合记录、聊天、引用、记忆主记录和运行元数据。
- `Milvus` 只负责检索实体，不负责 story 真相源，也不负责短期聊天状态。
- `feed-data.json` 在迁移期继续保留，直到前端完全切到 API。

### Story 身份规则

- Story 必须使用稳定的 `story_key`。
- Story 在创建后绝对不可变；后续定时任务不会原地修改既有 story。
- Story 范围内的聊天和引用只绑定 `story_key`。
- 跨天或后续运行出现的相似事件，允许形成多个独立 story。

### Memory 规则

- 短期记忆保存在 PostgreSQL 的 session/message 表中。
- 长期记忆必须有 PostgreSQL 主表，同时在 Milvus 中保留可检索副本。
- 不允许把原始聊天记录直接镜像到 Milvus 充当唯一的用户记忆。

### Retrieval 规则

- `document` 与 retrieval `unit` 是检索与 citation 的真相源。
- 检索实体按 chunk 或资产粒度建模，不按 article 粒度建模。
- 内容检索 collection 必须支持 hybrid retrieval，`user_memory` 也应支持 hybrid retrieval。
- Milvus 中的 story 字段只能作为冗余过滤字段，不能替代 SQL 中的文档与聚合关系。

### 认证规则

- `v1` 只支持 Feishu 登录。
- 服务端必须执行 `tenant_key` allowlist 校验。
- 所有登录尝试必须可审计。

## 迁移期护栏

- 在 API 和当前 `feed-data.json` 契约对齐前，不允许移除现有前端信息流主路径。
- 不允许让前端路由或会话恢复依赖可变运行态 ID。
- 不允许让后续聚合任务原地覆盖既有 story 内容。
- 原始 HTML 和大文件资产不入 PostgreSQL，只保存路径或 URL。

## 当前后端目录

```text
/
├─ backend/
│  ├─ app/
│  │  ├─ config/
│  │  ├─ core/
│  │  ├─ models/
│  │  ├─ router/
│  │  ├─ schema/
│  │  ├─ scripts/
│  │  ├─ service/
│  │  └─ main.py
│  ├─ main.py
│  ├─ product.md
│  ├─ pyproject.toml
│  ├─ schema.md
│  ├─ scripts/
│  ├─ server/
│  ├─ test/
│  │  ├─ app/
│  │  └─ scripts/
│  └─ uv.lock
├─ frontend/
├─ docs/
├─ AGENTS.md
└─ plan.md
```

## 必须同步维护的文档

当架构或 schema 发生变化时，必须同步更新：

- `plan.md`
- `backend/product.md`
- `backend/schema.md`
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
  - `08:00`：早间批量采集与不可变 story 生成

## 质量门槛

任何后端或数据层改动在完成前，至少满足：

- schema 术语与 `docs/data-model.md` 一致
- API 输出与 `docs/api-contract.md` 一致
- `story_key` 对应的 story 内容不可变且可稳定回放
- citation 能从 answer -> unit -> document -> source 完整回溯
- 后端实现位置符合当前目录约定：Web API 进入 `backend/app/`，测试进入 `backend/test/app/` 或 `backend/test/scripts/`
- 新增运行命令或部署依赖时，必须同步写入 `docs/ops-runbook.md`
