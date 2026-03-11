# Backend 产品文档

## 1. 文档目的

本文档面向后端开发者，定义 `backend/` 需要支撑的产品能力、边界和落地约束。

它不是字段级 schema 说明，也不是给最终用户看的产品介绍：

- 字段与表设计看 `backend/schema.md`
- 跨团队架构约束看 `docs/architecture.md`
- API 契约看 `docs/api-contract.md`

## 2. 后端要支撑的产品能力

`v1` 后端必须支撑以下产品能力：

- 首页信息流：向前端输出稳定的 feed 数据，并在迁移期与 `feed-data.json` 并行
- Story 详情：按 `story_key` 提供聚合结果、成员文档与会话恢复所需元数据
- Feishu 登录：作为后续阶段接入的认证能力，执行 tenant allowlist 校验并记录完整审计
- 全局 AI：支持跨库问答、历史 session 恢复与 citation 返回
- Story 内 AI：支持 story scope 问答，并绑定稳定 `story_key`
- 视觉检索：支持风格/单品/造型类 query 的图片召回与图片级 citation 返回
- 当前态 story 聚合：持续刷新 `story` 与 `story_article`

## 3. 后端产品边界

### 真相源边界

- `sources.yaml` 继续是采集配置真相源
- 当前重构路径由 `backend/app/service/news_collection_service.py` 读取 `backend/app/service/sources.yaml`
- 当前第一阶段已通过 `backend/app/service/document_ingestion_service.py` 将采集结果持久化进 PostgreSQL `document`
- PostgreSQL 是用户、文档、story、chat、citation、memory 和运行态状态真相源
- Milvus 只负责检索副本，不负责 story 真相源和短期会话状态
- v1 内容检索采用 `content_text_unit` + `content_image_unit` 双 collection，而不是单一 `content_unit`

### 稳定身份边界

- 对外稳定 story 标识必须是 `story_key`
- 当前 story 数据直接存放在 SQL 主表，不引入独立 snapshot/run 身份
- story 内聊天与 citation 只需绑定 `story_key`

### 非目标

- `v1` 不新增 `content_source` SQL 主表
- 不为 story 引入额外的 snapshot 发布主表
- 不把原始聊天记录直接镜像到 Milvus 充当唯一记忆
- 不把大体积 HTML 或媒体文件本体写入 PostgreSQL

## 4. 后端目录约定

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

- `app/`：后端主应用目录，后续 FastAPI、repository、service、任务编排与配置统一放这里
- `app/config/`：后端易变配置包；集中处理 embedding、Milvus 等服务配置
- `app/core/`：稳定基础设施目录；保留数据库与 Redis 连接能力
- `app/service/news_collection_service.py`：当前已重写 source loading、采集、去重与 article 富化逻辑，先返回内存中的 article 列表，不承担 JSON 导出或持久化
- `app/service/document_ingestion_service.py`：负责数据库查重、`document` 字段映射与 PostgreSQL 批量入库；当前通过 `backend/main.py` 手动触发
- `test/`：后端统一测试目录；新增测试不再放在 `scripts/`、`server/` 子目录内
- `scripts/`：迁移期保留的采集与聚合脚本
- `server/`：遗留 Node 托管层，仅在切流完成前保留
- `schema.md`：字段级数据模型与存储设计
- `product.md`：后端开发者产品文档

## 5. 开发完成定义

一次后端改动完成前，至少要满足：

- 实现位置符合目录约定，新增 API 逻辑进入 `backend/app/`
- 测试进入 `backend/test/`
- 术语与 `docs/data-model.md` 一致
- API 输出与 `docs/api-contract.md` 一致
- citation 可以从 answer -> unit -> document -> source 回溯
- image citation 可以从 answer -> unit -> document_asset/document -> source 回溯
- 若改动运行命令或部署依赖，同步更新 `docs/ops-runbook.md`

## 6. 近期开发顺序

建议按以下顺序推进：

1. 在 `backend/app/` 落 FastAPI 入口、配置与基础路由
2. 先把 article collection 结果通过 `backend/app/service/document_ingestion_service.py` 写入 PostgreSQL `document`
3. 把 feed、topic、chat 按 API 契约逐步迁入 `backend/app/`
4. 保留 `backend/scripts/` 作为迁移期采集入口；新的 article collection 逻辑先在 `backend/app/service/news_collection_service.py` 重写，再逐步接入任务系统
5. Feishu OAuth2 在内容与数据链路稳定后接入
6. 把所有后端测试统一收敛到 `backend/test/`
