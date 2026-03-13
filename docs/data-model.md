# 数据模型说明

## 0. 后端实现落点

- `backend/app/`：承载 SQL model、schema、service 与检索编排实现
- `backend/app/config/`：集中维护 embedding、Milvus 等易变服务配置
- `backend/app/core/`：集中维护数据库与 Redis 等稳定基础设施；`database.py` 提供 engine/session/Base
- `backend/app/models/`：集中定义 SQLAlchemy ORM models，按领域拆分文件
- `backend/app/router/`：承载 FastAPI 路由与依赖注入入口
- `backend/app/schema/`：承载 API request/response schema
- `backend/app/scripts/`：承载应用内任务入口和可复用脚本
- `backend/app/service/news_collection_service.py`：当前承载 article collection refactor，输出内存 article 列表，不直接落库
- `backend/app/service/document_ingestion_service.py`：当前第一阶段负责 PostgreSQL `document` 入库，service 层直接操作 ORM / Session
- `backend/scripts/`：迁移期保留采集脚本
- `backend/test/`：承载数据模型、脚本与回归验证测试

## 1. 数据职责边界

### YAML

用于：

- 采集源配置
- 组织 allowlist 与认证配置

不用于：

- 运行态 feed 数据
- 用户身份
- 文档主记录
- chat 或 memory 持久化

### PostgreSQL

作为真相源存储：

- 组织与用户
- 登录事件
- 原始文档与资产
- story 稳定身份与不可变聚合记录
- chat session 与消息
- citation 与工具调用日志
- 长期记忆主记录
- 来源运行态状态

### Milvus

作为检索层存储：

- 文本检索单元
- 图片检索单元
- 用户长期记忆检索副本
- 用户画像检索副本

## 2. YAML 配置

### `sources.yaml`

定义：

- source ID
- source 类型
- URL
- 语言
- parser
- crawl interval
- enable 状态

当前代码路径：

- 重构中的 service 读取 `backend/app/service/sources.yaml`
- `backend/scripts/sources.yaml` 仍作为 legacy 脚本输入保留

### 认证配置

定义：

- 允许访问的 Feishu tenant
- 应用凭证
- 服务端模型配置通过 `backend/app/config/` 读取；数据库基础设施通过 `backend/app/core/` 管理，主契约为 `POSTGRES_*`

## 3. Milvus Collections

### `content_text_unit`

用途：

- 全局 AI 检索
- story 内检索
- document 内检索

实体粒度：

- text chunk

关键字段：

- `unit_id`
- `article_id`
- 可选冗余字段 `story_key`
- `chunk_index`
- `text_content`
- text dense vector
- text sparse vector，用于 hybrid retrieval

规则：

- story 成员关系仍以 SQL 中该 story 的固定成员关系为准
- dense 使用 `qwen3-vl-embedding`
- sparse 使用 `text-embedding-v4`

### `content_image_unit`

用途：

- 风格/单品/造型类 query 的图片召回
- story 与全局 AI 的 image citation 支撑

实体粒度：

- image asset

关键字段：

- `unit_id`
- `article_id`
- `asset_id`
- `asset_url`
- `asset_text`
- image dense vector
- image sparse vector
- `asset_role`
- fashion metadata

规则：

- image dense 使用 `qwen3-vl-embedding`
- image sparse 由 `asset_text` 通过 `text-embedding-v4` 生成
- `asset_text` 来自标题、上下文、caption、visual description 与受控 fashion metadata
- v1 不把 video asset 纳入当前 retrieval redesign

### `user_memory`

用途：

- 检索长期用户记忆

规则：

- PostgreSQL 是主存
- Milvus 是可检索副本
- 应支持 hybrid retrieval

### `user_profile_memory`

用途：

- 检索化的用户画像副本

规则：

- 可编辑主画像仍保存在 PostgreSQL 的 `user_profile`

## 4. PostgreSQL Tables

### 身份与认证

- `organization`
- `app_user`
- `auth_login_event`

### 原始文档层

- `document`
- `document_asset`
- `retrieval_unit_ref`

当前第一阶段规则：

- 先只写 `document`
- `article_id` 是采集链路业务唯一键，也是文档主键
- `canonical_url` 是数据库级幂等键
- `content_md_path` 保存清洗后 Markdown 地址
- 已存在 URL 直接跳过，不回写旧记录

### Story 不可变聚合层

- `story`
- `story_article`

### Chat 与交互

- `chat_session`
- `chat_message`
- `message_citation`
- `tool_execution`

### 用户画像与长期记忆

- `user_profile`
- `user_memory_record`
- `memory_write_log`

### 运行状态

- `source_runtime_state`

## 5. Story 身份模型

### 稳定身份

- `story_key` 是稳定 story 标识

### 不可变实体

- `story` 保存单次聚合生成的不可变 story 主记录
- `story_article` 保存该 story 创建时的固定成员关系

### 为什么必须拆分

- story 是用户阅读与引用的稳定单元
- 后续聚合运行不会原地修改既有 story
- 用户仍然需要稳定 topic URL 和可延续的讨论上下文

后续相似事件可以在新的运行中形成新的独立 story，这是产品设计而不是数据异常。

## 6. Memory 分层

### 短期记忆

存放于：

- `chat_session`
- `chat_message`

### 长期记忆

存放于：

- `user_memory_record` 作为真相源
- `user_memory` 作为 Milvus 检索副本

这种拆分是为了满足：

- 审计
- 删除与失效控制
- 回放
- 人工修正

## 7. 不可变 Story 读模型

- 首页和 story API 默认读取不可变 `story` / `story_article`
- 旧 story 不会在后续运行中被原地修改
- 需要排障时依赖 `source_runtime_state`、日志和数据库备份，而不是发布指针
