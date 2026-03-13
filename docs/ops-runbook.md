# 运维运行手册

## 1. 文档目的

本文档描述重构后 Fashion Feed 的目标运行方式，面向内部运维和工程同学。

## 2. 运行服务

规划中的核心服务：

- 前端静态部署
- FastAPI 服务
- Celery Worker
- Redis
- PostgreSQL
- Milvus

目录约定：

- `backend/app/`：FastAPI 服务主目录
- `backend/app/config/`：后端易变配置包，集中管理 embedding 与 Milvus 等配置
- `backend/app/core/`：后端稳定基础设施目录，集中管理数据库与 Redis；`database.py` 维护 engine/session/Base
- `backend/app/models/`：集中定义 SQLAlchemy ORM models，按领域拆分
- `backend/app/router/`：集中定义 FastAPI 路由与依赖注入入口
- `backend/app/schema/`：集中定义 API request/response schema
- `backend/app/scripts/`：集中定义应用内任务入口和可复用脚本
- `backend/app/service/news_collection_service.py`：当前重写后的 article collection service，供后续 API/任务系统接入
- `backend/scripts/`：迁移期采集脚本目录
- `backend/test/`：后端统一测试目录
- `backend/server/`：遗留 Node 托管层

## 3. 配置项分类

必需配置包括：

- PostgreSQL 连接信息
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- 清洗后 Markdown 存储路径
- `DOCUMENT_MARKDOWN_ROOT`
- Redis 连接信息
- Milvus 连接信息
- 模型供应商配置
- dense embedding 配置
- sparse embedding 配置
- image rerank 配置
- source 配置路径

当前约定的 source 配置路径：

- 重构中的 service：`backend/app/service/sources.yaml`
- legacy 脚本：`backend/scripts/sources.yaml`

认证补充：

- Feishu OAuth2 仍是目标态能力
- 当前阶段认证链路 deferred，因此不是运行前置条件

## 4. 定时任务

### 早间批量聚合

- 时间：`08:00`
- 任务名：`morning_batch_aggregation`
- 范围：上次成功运行之后新增的文档
- 预期输出：
  - 新生成的不可变 `story`
  - 新生成的不可变 `story_article`
  - 刷新的首页聚合结果

## 5. 不可变 Story 生成流程

1. 执行采集、清洗和文档入库
2. 生成 text/image retrieval units
3. 基于本轮新增文档生成新的 `story` 与 `story_article`
4. 校验输出数量与结构完整性
5. 刷新 feed API 和导出产物

既有 story 不会在后续运行中被原地修改。

## 6. 恢复流程

如果某次聚合结果异常：

1. 确认失败窗口内新增文档和聚合输出范围
2. 通过数据库备份或重新执行该失败窗口恢复对应的不可变 story 结果
3. 验证首页与 topic 接口
4. 排查失败原因后再重新聚合

## 7. 日常巡检项

重点关注：

- 登录失败数和 tenant 拒绝数
- source 抓取失败
- document 解析失败
- Milvus 写入失败
- embedding 延迟
- image enrichment 积压
- rerank 失败率
- story 数量异常
- citation 生成失败

## 8. 标准命令

当前约定的基础命令：

- 启动 API：`uvicorn backend.app.main:app --reload`
- 运行后端测试：`python -m unittest discover -s backend/test`
- 初始化 PostgreSQL 表：`python -m backend.main init-db`
- 手动执行文档入库：`python -m backend.main ingest-documents`
- 手动生成 text retrieval units 并同步 Milvus replica：`python -m backend.main ingest-retrieval-units`
- 仅刷新 SQL retrieval refs、不同步 Milvus replica：`python -m backend.main ingest-retrieval-units --skip-replica-sync`
- 手动搜索 text retrieval units（当前为 llama-index core + gateway fallback 路径）：`python -m backend.main search-retrieval-units "runway tailoring" --limit 5`
- 手动执行采集脚本：`python backend/scripts/fetch_feeds.py`

命令语义边界：

- `python -m backend.main ingest-retrieval-units` 不带额外参数时，默认执行 SQL `retrieval_unit_ref` refresh + Milvus replica sync
- 只有显式追加 `--skip-replica-sync`，才会跳过 Milvus upsert；该模式只刷新 SQL truth，不修复 Milvus replica
- 当前仓库尚未接入官方 `llama-index-vector-stores-milvus` 插件；因此 `python -m backend.main search-retrieval-units` 的运行路径固定为 llama-index core + gateway fallback，而不是原生 Milvus plugin search

当前说明：

- `backend/app/config/` 承接易变服务配置；embedding 配置已从总配置中拆出
- `backend/app/core/` 承接数据库与 Redis 基础设施
- `backend/app/config/storage.py` 管理清洗后 Markdown 存储根目录
- `backend/app/router/` 预留给 FastAPI 路由
- `backend/app/schema/` 预留给 API schema
- `backend/app/scripts/` 预留给应用内任务入口；`backend/scripts/` 继续承载迁移期 legacy 脚本
- `backend/app/service/news_collection_service.py` 负责 article collection
- `backend/app/service/document_ingestion_service.py` 负责 PostgreSQL `document` 入库，并直接操作 ORM / Session
- `backend/main.py ingest-retrieval-units` 只面向已持久化的 `document` 记录，运行前必须确认 PostgreSQL 中已有文档数据且对应 Markdown/Text 可读
- `backend/main.py ingest-retrieval-units` 每次都会从 PostgreSQL `document` + 对应 Markdown/Text 重新计算当前 `text_chunk` 集合，并补齐缺失的 SQL `retrieval_unit_ref`
- `backend/main.py ingest-retrieval-units` 默认会先提交 SQL `retrieval_unit_ref`，提交成功后再把当前全部 `text_chunk` 重发到 Milvus `content_text_unit`，因此 Milvus replica 丢失后可以通过重复执行该命令从 SQL/Markdown 重建
- `backend/main.py ingest-retrieval-units --skip-replica-sync` 只刷新 SQL truth，不触发 Milvus upsert
- 如果 `backend/main.py ingest-retrieval-units` 在 SQL commit 之后 Milvus upsert 失败，命令会报错，但 PostgreSQL truth 保留；Milvus 恢复后直接重跑该命令修复 replica
- `backend/main.py search-retrieval-units` 当前不是官方 `llama-index-vector-stores-milvus` 插件链路；现在只是 llama-index core + gateway fallback，先从 Milvus query candidate records，再做应用侧 lexical ranking
- `backend/main.py search-retrieval-units` 当前不代表已接通原生 Milvus full-text / hybrid retrieval，也不依赖 retrieval embedding 配置
- text 检索使用 `qwen3-vl-embedding` dense + `text-embedding-v4` sparse
- image 检索使用 `qwen3-vl-embedding` dense + 基于 `asset_text` 的 `text-embedding-v4` sparse
- image caption / visual description / fashion metadata 通过异步任务回写检索层
- 当前人工入库入口是 `python -m backend.main ingest-documents`
- 当前人工检索构建入口是 `python -m backend.main ingest-retrieval-units`
- legacy `backend/scripts/fetch_feeds.py` 仍可作为迁移期采集参考

检索副本当前运行口径：

- 默认口径：`python -m backend.main ingest-retrieval-units` 等价于“从 SQL/Markdown 重算当前 chunks + 补齐缺失 `retrieval_unit_ref` + commit SQL 后重放全部当前 chunks 到 Milvus replica”
- SQL-only 口径：只有显式传入 `--skip-replica-sync` 时，才只更新 SQL `retrieval_unit_ref`，不会触发 Milvus writer
- 搜索口径：当前没有官方 `llama-index-vector-stores-milvus` 插件接入，`search-retrieval-units` 走的是 llama-index core + gateway fallback，不应理解成原生 Milvus full-text / hybrid retrieval 已经落地

待 Celery 与聚合任务代码落地后，再补充以下命令：

- 启动 Celery worker
- 手动执行早间批量聚合
- 重建 embedding
- 重建 image enrichment
- 恢复指定聚合窗口输出

## 9. 典型故障场景

### 登录异常

检查：

- 当前阶段无需排查登录链路
- Feishu 接入后再检查凭证、allowlist、callback URL 与登录审计表

### 首页为空或异常

检查：

- `story` 当前记录数量
- `story_article` 成员数量
- 最新聚合日志
- API 返回结构

### AI 回答质量异常

检查：

- retrieval 结果是否相关
- 图片候选是否进入最终 context pack
- citation 是否正确落库
- 模型服务是否健康
- session scope 是否正确传入

### Memory 行为异常

检查：

- `user_memory_record` 写入链路
- Milvus memory upsert
- 检索参数是否正确

## 10. 文档维护要求

只要运行服务、定时任务或不可变 story 生成流程发生变化，必须同时更新：

- 本文档
- `docs/architecture.md`
- `docs/data-model.md`
