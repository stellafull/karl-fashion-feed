# 系统架构

## 1. 总体架构

Fashion Feed 正在从“静态 JSON 信息流”升级为“前后端分离 + 持久化 + RAG”的系统。

目标运行拓扑：

```text
Feishu 登录
   |
前端（React/Vite）
   |
FastAPI
   |-- PostgreSQL
   |-- Milvus
   |-- Redis
   |-- Celery Worker
   |
采集 / 清洗 / 聚类 / 发布 / 检索 / AI
```

## 2. 运行组件

### 前端

职责：

- 首页信息流展示
- story 详情展示
- 来源筛选与排序
- AI sidebar
- story 底部上下文问答入口

### FastAPI

职责：

- 认证接口
- 首页与 topic API
- chat API
- citation 组装
- session 持久化
- 检索与回答编排

代码承载约定：

- `backend/app/` 是 FastAPI 应用主目录
- `backend/server/` 只保留迁移期静态托管职责
- `backend/test/` 是后端统一测试目录

### PostgreSQL

职责：

- 用户与认证
- 原始文档与资产
- story 稳定身份与发布快照
- chat 状态
- citation 持久化
- memory 主记录
- pipeline 元数据

### Milvus

职责：

- `content_text_unit` 检索
- `content_image_unit` 检索
- `user_memory` 检索
- `user_profile_memory` 检索

Milvus 不负责 story 历史真相源，也不负责短期 session 状态。

### Redis + Celery

职责：

- 异步任务队列
- 采集与清洗拆分
- text/image embedding 任务
- image caption / visual description / fashion metadata enrichment
- story 发布任务

## 3. 后端目录约定

```text
backend/
├─ app/
├─ test/
├─ scripts/
├─ server/
├─ schema.md
└─ product.md
```

目录边界：

- `app/`：放 API、domain service、repository、任务入口与配置
- `app/config/`：集中维护模型与服务配置，例如 embedding 与 Milvus
- `app/core/`：集中维护数据库与 Redis 等稳定基础设施
- `app/service/news_collection_service.py`：已承接 refactor 后的 source loading、采集、去重、补图与 article 富化，当前只返回内存 article 列表
- `app/service/document_ingestion_service.py`：负责把采集结果去重后写入 PostgreSQL `document`
- `test/`：统一存放 API、数据模型、脚本与发布回归测试
- `scripts/`：迁移期脚本保留区，后续逐步拆入 Celery 任务
- `server/`：遗留 Node 托管层，不再作为长期后端真相源
- `schema.md`：后端数据模型设计文档
- `product.md`：面向后端开发者的产品文档

## 4. 主要数据流

### 内容生产链路

1. 读取 `sources.yaml`
2. 拉取 RSS / crawl 来源
3. 标准化并去重文档
4. 做摘要、分类与基础清洗
5. 写入 `document`
6. 抽取 `document_asset` 中的 image asset
7. 生成 text retrieval units 并写入 `content_text_unit`
8. 异步生成 image `asset_text`、visual description、fashion metadata，并写入 `content_image_unit`
9. 生成并发布 story 快照
10. 更新 feed API 和迁移期 JSON 产物

当前代码状态：

- `backend/app/service/news_collection_service.py` 与 `backend/app/service/document_ingestion_service.py` 已覆盖 1-5 的 document persistence 子链路
- `document_asset`、Milvus、image enrichment、story 发布、feed JSON 导出仍未接入这条新 service 路径

### Story 发布链路

1. 创建 `pipeline_run`
2. 生成 cluster
3. 解析 `story_key` 连续性
4. 写入 `story_cluster_snapshot`
5. 写入 `story_cluster_member_snapshot`
6. 执行校验
7. 切换 `published_run`

### Story AI 链路

1. 用户打开 story
2. 用户在 story 内发问
3. 后端先读取 story 上下文
4. 生成 dense + sparse query 表示
5. 按 query intent 同时召回 `content_text_unit` 与 `content_image_unit`
6. 文本候选与图片候选分别 rerank 后融合
7. 调用模型生成回答
8. 写入 `chat_message`、`message_citation` 与 memory 记录

### 全局 AI 链路

1. 用户打开左侧 AI sidebar
2. 新建或恢复 session
3. 后端执行 text/image 双路检索与 memory 检索
4. 生成带文本与图片 citation 的回答
5. 将 session 历史写入 PostgreSQL

## 5. Story 身份模型

必须引入稳定 story 身份，因为：

- 用户可能收藏或继续讨论某个 story
- story 范围内聊天需要跨 run 连续
- citation 与 session 回放必须稳定

模型定义：

- `story_key`：稳定 story 标识
- `run_id`：某次发布版本标识
- `published_run`：当前生效的发布版本

## 6. 更新节奏

### 每日重聚类

- 时间：`08:00`
- 范围：最近 72 小时 active window
- 目的：修正 story 归并质量并保持 story continuity

### 日间增量更新

- 时间：`10:00` 到 `18:00` 每 2 小时
- 范围：新增文档
- 目的：持续刷新首页和 story 内容

## 7. 安全模型

- Feishu 是唯一登录入口
- 仅允许 allowlist 组织访问
- 登录结果必须写审计记录
- 权限控制必须在服务端生效，不能只依赖前端

## 8. 迁移策略

迁移期间：

- 现有前端继续存在
- 静态 `feed-data.json` 可继续保留为兜底产物
- 新 API 与旧数据路径并行存在
- 只有在 API 对齐且稳定后，前端才切主数据源
