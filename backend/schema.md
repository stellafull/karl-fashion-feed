# Backend Schema 设计说明

## 1. 文档目的

本文档用于冻结 `backend/` 侧的数据模型设计，直接指导后续：

- PostgreSQL 建表
- Milvus collection 创建
- FastAPI schema 与 repository 设计
- Celery 任务写入与发布流程

本文档是字段级设计草案，优先保证：

- 首页 feed 可读
- story 可稳定引用
- chat 可回放
- citation 可追溯
- memory 可审计、可失效、可检索

代码承载约定：

- `backend/app/`：实现 SQL model、schema、repository 与服务层
- `backend/app/service/news_collection_service.py`：当前已重写 article collection pipeline 的非持久化部分，读取 `sources.yaml` 后返回 article 列表
- `backend/app/service/document_ingestion_service.py`：当前第一阶段负责将 article collection 结果映射并写入 PostgreSQL `document`
- `backend/test/`：承载 schema 与回归验证测试
- `backend/scripts/`：迁移期保留采集脚本

## 2. 存储职责边界

### 2.1 不进入主数据库的内容

- `sources.yaml`：采集配置真相源
- 当前重构路径使用 `backend/app/service/sources.yaml`；legacy `backend/scripts/sources.yaml` 暂时保留
- 组织 allowlist 配置：可放 `.env` 或单独配置文件
- 大体积原始 HTML、图片、视频文件本体：不直接入 PostgreSQL

### 2.2 PostgreSQL 真相源

PostgreSQL 负责：

- 用户与组织
- 登录审计
- 原始文档与资产
- 稳定 story 身份与每次发布快照
- chat session/message/citation
- 用户画像与长期记忆主记录
- pipeline run 与发布状态

### 2.3 Milvus 检索层

Milvus 负责：

- `content_unit`：内容检索单元
- `user_memory`：长期记忆检索副本
- `user_profile_memory`：用户画像检索副本

## 3. 统一设计约定

### 3.1 ID 规范

- PostgreSQL 主键默认使用 `uuid`
- `story_key`、`source_id`、`unit_id`、`memory_id` 使用业务字符串主键
- `run_id` 建议使用 `uuid`

### 3.2 时间字段

- PostgreSQL 一律使用 `timestamptz`
- Milvus 一律使用 `Int64` 存 Unix timestamp 秒级或毫秒级
- 推荐统一为毫秒时间戳，字段名以 `_ts` 结尾

### 3.3 可变结构字段

- PostgreSQL 使用 `jsonb`
- Milvus 使用 `JSON`

### 3.4 向量策略

- 所有需要 hybrid retrieval 的 collection 同时保留：
  - dense vector
  - sparse vector
- dense vector 维度由具体 embedding 模型决定，不在 schema 中写死
- Milvus collection 中只保存检索所需文本，不保存超长全文；全文真相源仍在 PostgreSQL

### 3.5 推荐 PostgreSQL 扩展

- `pgcrypto`：用于 `gen_random_uuid()`
- `btree_gin`：如后续需要混合索引可启用

## 4. 推荐枚举

| 枚举名 | 推荐值 |
|---|---|
| `organization_status` | `active`, `disabled` |
| `user_role` | `admin`, `editor`, `viewer` |
| `account_status` | `active`, `suspended`, `resigned` |
| `login_result` | `success`, `tenant_rejected`, `visibility_rejected`, `token_error`, `userinfo_error` |
| `asset_type` | `image`, `video` |
| `parse_status` | `parsed`, `failed`, `filtered` |
| `story_status` | `active`, `archived`, `merged`, `suppressed` |
| `run_type` | `daily_recluster`, `incremental_update`, `manual_backfill`, `reindex` |
| `run_status` | `queued`, `running`, `success`, `failed`, `cancelled` |
| `validation_status` | `pending`, `passed`, `failed` |
| `scope_type` | `global`, `story`, `document` |
| `session_status` | `active`, `archived`, `closed` |
| `message_role` | `user`, `assistant`, `system`, `tool` |
| `tool_execution_status` | `queued`, `running`, `success`, `failed`, `timeout`, `cancelled` |
| `memory_type` | `semantic`, `episodic`, `preference`, `task` |
| `memory_status` | `active`, `expired`, `deleted`, `suppressed` |
| `publication_status` | `current`, `superseded`, `rolled_back` |

## 5. Milvus Collection 设计

## 5.1 `content_unit`

用途：

- 全局 AI 检索
- story 内问答检索
- document 内问答检索

主键：

- `unit_id`

字段设计：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `unit_id` | `VarChar(64)` | 是 | 检索单元唯一 ID |
| `doc_id` | `VarChar(64)` | 是 | 父文档 ID，对应 PostgreSQL `document.doc_id` |
| `story_key` | `VarChar(64)` | 否 | 冗余 story 标识，仅用于过滤优化，不是真相源 |
| `source_id` | `VarChar(64)` | 是 | 来源 ID，对应 `sources.yaml` |
| `unit_type` | `VarChar(32)` | 是 | `text_chunk` / `image_asset` / `video_asset` |
| `chunk_index` | `Int32` | 否 | 文本 chunk 顺序，媒体类型可空 |
| `title` | `VarChar(512)` | 否 | 文档标题或资产标题 |
| `text_content` | `VarChar(8192)` | 否 | 检索用文本，建议截断版本 |
| `asset_url` | `VarChar(2048)` | 否 | 图片或视频地址 |
| `source_url` | `VarChar(2048)` | 是 | 原文 URL |
| `author` | `VarChar(256)` | 否 | 作者 |
| `domain` | `VarChar(256)` | 否 | 来源域名 |
| `language` | `VarChar(16)` | 否 | `en` / `zh` / `ja` 等 |
| `published_at_ts` | `Int64` | 否 | 发布时间戳 |
| `importance_score` | `Float` | 否 | 内容重要度 |
| `freshness_score` | `Float` | 否 | 时效性分数 |
| `is_active` | `Bool` | 是 | 是否参与当前检索 |
| `tags` | `JSON` | 否 | 标签列表 |
| `metadata` | `JSON` | 否 | 其他检索元数据 |
| `text_dense_vector` | `FloatVector(dim_text)` | 否 | 文本 dense embedding |
| `text_sparse_vector` | `SparseFloatVector` | 否 | 文本 sparse embedding |
| `image_dense_vector` | `FloatVector(dim_image)` | 否 | 图片 dense embedding |
| `video_dense_vector` | `FloatVector(dim_video)` | 否 | 视频 dense embedding |
| `created_at_ts` | `Int64` | 是 | 创建时间戳 |
| `updated_at_ts` | `Int64` | 是 | 更新时间戳 |

索引建议：

- 主键：`unit_id`
- 向量索引：
  - `text_dense_vector`
  - `text_sparse_vector`
  - `image_dense_vector`
  - `video_dense_vector`
- 标量过滤字段：
  - `doc_id`
  - `story_key`
  - `source_id`
  - `unit_type`
  - `language`
  - `is_active`
  - `published_at_ts`

约束建议：

- `text_chunk` 必须有 `chunk_index`
- `image_asset`、`video_asset` 必须有 `asset_url`
- `story_key` 可为空，因为 story 归属以 SQL 快照为准

## 5.2 `user_memory`

用途：

- 检索用户长期记忆
- 为后续 session 提供语义延续

主键：

- `memory_id`

字段设计：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `memory_id` | `VarChar(64)` | 是 | 长期记忆唯一 ID，对应 PostgreSQL 主记录 |
| `user_id` | `VarChar(64)` | 是 | 用户 ID |
| `session_id` | `VarChar(64)` | 否 | 来源 session |
| `message_id` | `VarChar(64)` | 否 | 来源 message |
| `memory_type` | `VarChar(32)` | 是 | `semantic` / `episodic` / `preference` / `task` |
| `memory_text` | `VarChar(4096)` | 是 | 检索用记忆文本 |
| `topic_tags` | `JSON` | 否 | 主题标签 |
| `intent` | `VarChar(128)` | 否 | 意图归类 |
| `sentiment` | `VarChar(32)` | 否 | 情绪或态度 |
| `importance_score` | `Float` | 否 | 记忆重要度 |
| `confidence_score` | `Float` | 否 | 记忆抽取置信度 |
| `status` | `VarChar(32)` | 是 | `active` / `expired` / `deleted` / `suppressed` |
| `metadata` | `JSON` | 否 | 检索附加信息 |
| `memory_dense_vector` | `FloatVector(dim_text)` | 否 | dense embedding |
| `memory_sparse_vector` | `SparseFloatVector` | 否 | sparse embedding |
| `valid_from_ts` | `Int64` | 否 | 生效时间戳 |
| `valid_to_ts` | `Int64` | 否 | 失效时间戳 |
| `last_accessed_ts` | `Int64` | 否 | 最近命中时间 |
| `created_at_ts` | `Int64` | 是 | 创建时间 |
| `updated_at_ts` | `Int64` | 是 | 更新时间 |

索引建议：

- 主键：`memory_id`
- 向量索引：
  - `memory_dense_vector`
  - `memory_sparse_vector`
- 标量过滤字段：
  - `user_id`
  - `memory_type`
  - `status`
  - `valid_to_ts`

## 5.3 `user_profile_memory`

用途：

- 为 query rewrite、个性化排序、风格偏好判断提供检索输入

主键：

- `profile_memory_id`

字段设计：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `profile_memory_id` | `VarChar(64)` | 是 | 画像检索记录 ID |
| `user_id` | `VarChar(64)` | 是 | 用户 ID |
| `organization_id` | `VarChar(64)` | 否 | 所属组织 |
| `profile_version` | `Int32` | 是 | 画像版本号 |
| `profile_text` | `VarChar(4096)` | 是 | 检索用画像摘要 |
| `preferred_language` | `VarChar(16)` | 否 | 偏好语言 |
| `expertise_level` | `VarChar(64)` | 否 | 专业程度 |
| `interaction_style` | `VarChar(64)` | 否 | 偏好交互方式 |
| `role` | `VarChar(64)` | 否 | 用户角色 |
| `common_topics` | `JSON` | 否 | 常见关注主题 |
| `favorite_domains` | `JSON` | 否 | 常访问来源域名 |
| `is_active` | `Bool` | 是 | 是否为当前版本 |
| `metadata` | `JSON` | 否 | 扩展字段 |
| `profile_dense_vector` | `FloatVector(dim_text)` | 否 | dense embedding |
| `profile_sparse_vector` | `SparseFloatVector` | 否 | sparse embedding |
| `created_at_ts` | `Int64` | 是 | 创建时间 |
| `updated_at_ts` | `Int64` | 是 | 更新时间 |

索引建议：

- 主键：`profile_memory_id`
- 向量索引：
  - `profile_dense_vector`
  - `profile_sparse_vector`
- 标量过滤字段：
  - `user_id`
  - `organization_id`
  - `is_active`

## 6. PostgreSQL 建表设计

## 6.1 身份与认证

## `organization`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `organization_id` | `uuid` | PK, default `gen_random_uuid()` | 组织主键 |
| `feishu_tenant_key` | `varchar(128)` | UNIQUE, NOT NULL | Feishu tenant 唯一标识 |
| `name` | `varchar(255)` | NOT NULL | 组织名 |
| `status` | `organization_status` | NOT NULL | 组织状态 |
| `metadata` | `jsonb` | default `'{}'::jsonb` | 组织扩展信息 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

索引建议：

- unique index：`feishu_tenant_key`

## `app_user`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `user_id` | `uuid` | PK, default `gen_random_uuid()` | 用户主键 |
| `organization_id` | `uuid` | FK -> `organization.organization_id`, NOT NULL | 所属组织 |
| `feishu_open_id` | `varchar(128)` | UNIQUE, NOT NULL | Feishu 应用内唯一身份 |
| `feishu_union_id` | `varchar(128)` | INDEX | 跨应用关联 ID |
| `feishu_user_id` | `varchar(128)` | INDEX | 租户内用户 ID，可空 |
| `name` | `varchar(255)` | NOT NULL | 中文名或显示名 |
| `en_name` | `varchar(255)` |  | 英文名 |
| `avatar_url` | `text` |  | 头像地址 |
| `email` | `varchar(255)` |  | 邮箱 |
| `enterprise_email` | `varchar(255)` |  | 企业邮箱 |
| `mobile` | `varchar(64)` |  | 手机号 |
| `language_preference` | `varchar(16)` |  | 语言偏好 |
| `timezone` | `varchar(64)` |  | 时区 |
| `user_role` | `user_role` | NOT NULL | 用户角色 |
| `account_status` | `account_status` | NOT NULL | 账号状态 |
| `last_login_at` | `timestamptz` |  | 最近登录时间 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

索引建议：

- index：`organization_id`
- unique index：`feishu_open_id`
- index：`feishu_union_id`
- index：`feishu_user_id`

## `auth_login_event`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `login_event_id` | `uuid` | PK, default `gen_random_uuid()` | 登录事件主键 |
| `user_id` | `uuid` | FK -> `app_user.user_id` | 本地用户，可空 |
| `feishu_tenant_key` | `varchar(128)` |  | 登录返回的 tenant key |
| `feishu_open_id` | `varchar(128)` |  | 登录返回 open_id |
| `result` | `login_result` | NOT NULL | 登录结果 |
| `failure_reason` | `text` |  | 失败原因 |
| `ip_address` | `inet` |  | 登录 IP |
| `user_agent` | `text` |  | 浏览器 UA |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 事件时间 |

索引建议：

- index：`user_id`
- index：`feishu_tenant_key`
- index：`result`
- index：`created_at`

## 6.2 原始文档层

## `document`

当前第一阶段实现：

- 先只持久化 `document`
- `document_asset` 与 `retrieval_unit_ref` 后续再接入
- 数据库查重按 `canonical_url`
- `article_id` 作为内部业务唯一键保留
- `parse_status` 当前默认写 `parsed`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `doc_id` | `uuid` | PK, default `gen_random_uuid()` | 文档主键 |
| `article_id` | `varchar(64)` | UNIQUE, NOT NULL | 采集链路内部业务唯一键 |
| `source_id` | `varchar(64)` | NOT NULL | 来源 ID，对应 `sources.yaml` |
| `external_id` | `varchar(255)` |  | 来源侧原始 ID |
| `canonical_url` | `text` | UNIQUE, NOT NULL | 规范化后的原文地址 |
| `title` | `text` | NOT NULL | 文档标题 |
| `author` | `varchar(255)` |  | 作者 |
| `domain` | `varchar(255)` |  | 域名 |
| `language` | `varchar(16)` |  | 语言 |
| `published_at` | `timestamptz` |  | 发布时间 |
| `raw_text` | `text` |  | 清洗后的全文文本 |
| `raw_html_path` | `text` |  | 原始 HTML 存储路径 |
| `content_hash` | `varchar(64)` | INDEX | 内容 hash，用于去重或追踪 |
| `summary_zh` | `text` |  | 单篇中文摘要 |
| `category_hint` | `varchar(64)` |  | 单篇分类提示 |
| `content_type` | `varchar(64)` |  | 内容类型，如 `runway`、`fashion-tech` |
| `relevance_score` | `integer` |  | 相关性评分 |
| `relevance_reason` | `text` |  | 相关性说明 |
| `is_relevant` | `boolean` | NOT NULL, default `true` | 是否保留 |
| `is_sensitive` | `boolean` | NOT NULL, default `false` | 是否敏感 |
| `parse_status` | `parse_status` | NOT NULL | 解析状态；第一阶段默认写 `parsed` |
| `source_payload` | `jsonb` | default `'{}'::jsonb` | 来源附加字段；第一阶段承载 `link`、`image`、`content_snippet`、`article_tags` 等未单独建列信息 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

索引建议：

- unique index：`article_id`
- unique index：`canonical_url`
- index：`source_id`
- index：`published_at`
- index：`content_hash`
- index：`parse_status`
- index：`is_relevant`

## `document_asset`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `asset_id` | `uuid` | PK, default `gen_random_uuid()` | 资产主键 |
| `doc_id` | `uuid` | FK -> `document.doc_id`, NOT NULL | 所属文档 |
| `asset_type` | `asset_type` | NOT NULL | `image` / `video` |
| `asset_url` | `text` | NOT NULL | 资源地址 |
| `caption` | `text` |  | 图片/视频说明文字 |
| `sort_order` | `integer` | default `0` | 展示顺序 |
| `metadata` | `jsonb` | default `'{}'::jsonb` | 宽高、时长、来源等 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |

索引建议：

- index：`doc_id`
- unique index：`(doc_id, asset_url)`

## `retrieval_unit_ref`

用途：

- 作为 PostgreSQL 与 Milvus 的桥接表
- 支撑 citation、回灌、reindex 和调试

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `unit_id` | `varchar(64)` | PK | 对应 Milvus `content_unit.unit_id` |
| `doc_id` | `uuid` | FK -> `document.doc_id`, NOT NULL | 所属文档 |
| `unit_type` | `varchar(32)` | NOT NULL | `text_chunk` / `image_asset` / `video_asset` |
| `chunk_index` | `integer` |  | chunk 顺序 |
| `source_url` | `text` | NOT NULL | 原文 URL |
| `asset_url` | `text` |  | 资源 URL |
| `embedding_provider` | `varchar(64)` |  | embedding 服务商 |
| `embedding_model` | `varchar(128)` |  | embedding 模型名 |
| `embedding_version` | `varchar(64)` |  | embedding 版本 |
| `content_version_hash` | `varchar(64)` |  | 当前内容版本 hash |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

索引建议：

- index：`doc_id`
- index：`unit_type`
- index：`embedding_model`
- unique index：`(doc_id, unit_type, chunk_index)`，仅对文本 chunk 生效

## 6.3 Story 发布层

## `story_identity`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `story_key` | `varchar(64)` | PK | 稳定 story 标识 |
| `canonical_title` | `text` |  | 代表性标题 |
| `status` | `story_status` | NOT NULL | story 状态 |
| `current_category_id` | `varchar(64)` |  | 当前分类 ID |
| `first_seen_at` | `timestamptz` | NOT NULL | 首次出现时间 |
| `last_seen_at` | `timestamptz` | NOT NULL | 最近出现时间 |
| `merged_into_story_key` | `varchar(64)` | FK -> `story_identity.story_key` | 如被合并，指向目标 story |
| `metadata` | `jsonb` | default `'{}'::jsonb` | 连续性辅助信息 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

索引建议：

- index：`status`
- index：`last_seen_at`

## `story_cluster_snapshot`

说明：

- 这是发布快照表，不是稳定主表
- 主键应绑定 `run_id + story_key`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `run_id` | `uuid` | FK -> `pipeline_run.run_id`, NOT NULL | 所属发布 run |
| `story_key` | `varchar(64)` | FK -> `story_identity.story_key`, NOT NULL | 稳定 story 标识 |
| `snapshot_date` | `date` | NOT NULL | 快照日期 |
| `title` | `text` | NOT NULL | 当前版本标题 |
| `summary` | `text` | NOT NULL | 当前版本摘要 |
| `key_points` | `jsonb` | default `'[]'::jsonb` | 核心要点列表 |
| `topic_tags` | `jsonb` | default `'[]'::jsonb` | 标签列表 |
| `category_id` | `varchar(64)` |  | 分类 ID |
| `category_name` | `varchar(128)` |  | 分类名称 |
| `cover_image_url` | `text` |  | 封面图 |
| `representative_doc_id` | `uuid` | FK -> `document.doc_id` | 代表文档 |
| `rank_score` | `numeric(10,4)` |  | 首页排序分数 |
| `importance_score` | `numeric(10,4)` |  | 重要度分数 |
| `freshness_score` | `numeric(10,4)` |  | 时效性分数 |
| `article_count` | `integer` | NOT NULL, default `0` | 成员文档数 |
| `source_count` | `integer` | NOT NULL, default `0` | 来源数 |
| `newest_published_at` | `timestamptz` |  | 最新文档发布时间 |
| `metadata` | `jsonb` | default `'{}'::jsonb` | 其他发布字段 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |

主键建议：

- `PRIMARY KEY (run_id, story_key)`

索引建议：

- index：`story_key`
- index：`rank_score DESC`
- index：`newest_published_at DESC`

## `story_cluster_member_snapshot`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `run_id` | `uuid` | FK -> `pipeline_run.run_id`, NOT NULL | 所属 run |
| `story_key` | `varchar(64)` | NOT NULL | 所属 story |
| `doc_id` | `uuid` | FK -> `document.doc_id`, NOT NULL | 成员文档 |
| `member_score` | `numeric(10,4)` |  | 成员相关度分数 |
| `sort_order` | `integer` | default `0` | 成员排序 |
| `is_representative` | `boolean` | NOT NULL, default `false` | 是否代表文档 |

主键建议：

- `PRIMARY KEY (run_id, story_key, doc_id)`

索引建议：

- index：`doc_id`
- index：`(run_id, story_key, sort_order)`

## `pipeline_run`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `run_id` | `uuid` | PK, default `gen_random_uuid()` | run 主键 |
| `run_type` | `run_type` | NOT NULL | run 类型 |
| `status` | `run_status` | NOT NULL | run 状态 |
| `validation_status` | `validation_status` | NOT NULL, default `'pending'` | 校验状态 |
| `window_start` | `timestamptz` |  | 数据窗口起点 |
| `window_end` | `timestamptz` |  | 数据窗口终点 |
| `trigger_source` | `varchar(64)` |  | 触发来源，如 `schedule` / `manual` |
| `trigger_actor` | `varchar(128)` |  | 触发人或任务 |
| `total_sources` | `integer` | default `0` | 参与来源数 |
| `total_raw_documents` | `integer` | default `0` | 原始文档数 |
| `total_documents_after_dedup` | `integer` | default `0` | 去重后文档数 |
| `total_stories` | `integer` | default `0` | 生成 story 数 |
| `validation_errors` | `jsonb` | default `'[]'::jsonb` | 校验错误集合 |
| `logs_path` | `text` |  | 日志路径 |
| `started_at` | `timestamptz` |  | 开始时间 |
| `finished_at` | `timestamptz` |  | 结束时间 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |

索引建议：

- index：`run_type`
- index：`status`
- index：`started_at DESC`

## `published_run`

说明：

- 用于管理当前线上使用哪个 run
- 不直接覆盖已有发布历史

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `publication_id` | `uuid` | PK, default `gen_random_uuid()` | 发布记录主键 |
| `run_id` | `uuid` | FK -> `pipeline_run.run_id`, NOT NULL | 被发布的 run |
| `status` | `publication_status` | NOT NULL | 发布状态 |
| `is_current` | `boolean` | NOT NULL, default `false` | 是否当前线上版本 |
| `published_by` | `varchar(128)` |  | 发布人或任务 |
| `replaced_publication_id` | `uuid` | FK -> `published_run.publication_id` | 被替换的发布记录 |
| `notes` | `text` |  | 备注 |
| `published_at` | `timestamptz` | NOT NULL, default `now()` | 发布时间 |

索引建议：

- index：`run_id`
- partial unique index：`is_current = true`

## 6.4 Chat 与交互

## `chat_session`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `session_id` | `uuid` | PK, default `gen_random_uuid()` | session 主键 |
| `user_id` | `uuid` | FK -> `app_user.user_id`, NOT NULL | 发起用户 |
| `scope_type` | `scope_type` | NOT NULL | `global` / `story` / `document` |
| `scope_ref_key` | `varchar(128)` |  | `story_key` 或 `doc_id` 字符串形式 |
| `scope_snapshot_run_id` | `uuid` | FK -> `pipeline_run.run_id` | 进入对话时绑定的快照 run |
| `title` | `varchar(255)` |  | 会话标题 |
| `summary_text` | `text` |  | 会话摘要 |
| `status` | `session_status` | NOT NULL | 会话状态 |
| `model_config` | `jsonb` | default `'{}'::jsonb` | 模型配置 |
| `total_messages` | `integer` | NOT NULL, default `0` | 消息数 |
| `total_tokens_used` | `integer` | NOT NULL, default `0` | token 数 |
| `last_message_at` | `timestamptz` |  | 最近消息时间 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |
| `ended_at` | `timestamptz` |  | 结束时间 |

索引建议：

- index：`user_id`
- index：`(scope_type, scope_ref_key)`
- index：`last_message_at DESC`

## `chat_message`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `message_id` | `uuid` | PK, default `gen_random_uuid()` | 消息主键 |
| `session_id` | `uuid` | FK -> `chat_session.session_id`, NOT NULL | 所属 session |
| `user_id` | `uuid` | FK -> `app_user.user_id` | 用户消息可带 user_id，assistant/system 可空 |
| `role` | `message_role` | NOT NULL | 消息角色 |
| `content` | `text` | NOT NULL | 消息内容 |
| `token_used` | `integer` | default `0` | 消耗 token |
| `model_used` | `varchar(128)` |  | 使用模型 |
| `response_time_ms` | `integer` |  | 响应耗时 |
| `parent_message_id` | `uuid` | FK -> `chat_message.message_id` | 父消息 |
| `metadata` | `jsonb` | default `'{}'::jsonb` | 扩展元数据 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |

索引建议：

- index：`session_id`
- index：`parent_message_id`
- index：`created_at`

## `message_citation`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `uuid` | PK, default `gen_random_uuid()` | citation 主键 |
| `message_id` | `uuid` | FK -> `chat_message.message_id`, NOT NULL | 关联回答消息 |
| `doc_id` | `uuid` | FK -> `document.doc_id`, NOT NULL | 引用文档 |
| `unit_id` | `varchar(64)` | FK -> `retrieval_unit_ref.unit_id`, NOT NULL | 引用检索单元 |
| `source_url` | `text` | NOT NULL | 原文地址 |
| `citation_order` | `integer` | NOT NULL, default `0` | 引用顺序 |
| `quote_text` | `text` |  | 可展示引用文本片段 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |

索引建议：

- index：`message_id`
- index：`doc_id`
- index：`unit_id`

## `tool_execution`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `execution_id` | `uuid` | PK, default `gen_random_uuid()` | 工具执行主键 |
| `message_id` | `uuid` | FK -> `chat_message.message_id`, NOT NULL | 关联消息 |
| `session_id` | `uuid` | FK -> `chat_session.session_id`, NOT NULL | 所属 session |
| `user_id` | `uuid` | FK -> `app_user.user_id` | 发起用户 |
| `tool_name` | `varchar(128)` | NOT NULL | 工具名 |
| `input_params` | `jsonb` | default `'{}'::jsonb` | 输入参数 |
| `output_result` | `jsonb` | default `'{}'::jsonb` | 输出结果 |
| `execution_status` | `tool_execution_status` | NOT NULL | 执行状态 |
| `error_message` | `text` |  | 错误信息 |
| `start_time` | `timestamptz` |  | 开始时间 |
| `end_time` | `timestamptz` |  | 结束时间 |
| `duration_ms` | `integer` |  | 耗时 |

索引建议：

- index：`message_id`
- index：`session_id`
- index：`tool_name`
- index：`execution_status`

## 6.5 用户画像与长期记忆

## `user_profile`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `profile_id` | `uuid` | PK, default `gen_random_uuid()` | 画像主键 |
| `user_id` | `uuid` | FK -> `app_user.user_id`, UNIQUE, NOT NULL | 对应用户 |
| `preferred_language` | `varchar(16)` |  | 语言偏好 |
| `timezone` | `varchar(64)` |  | 时区 |
| `expertise_level` | `varchar(64)` |  | 专业程度 |
| `interaction_style` | `varchar(64)` |  | 回答风格偏好 |
| `team` | `varchar(128)` |  | 所属团队 |
| `explicit_interests` | `jsonb` | default `'[]'::jsonb` | 明确兴趣点 |
| `hidden_topics` | `jsonb` | default `'[]'::jsonb` | 不希望看到的话题 |
| `favorite_domains` | `jsonb` | default `'[]'::jsonb` | 常用来源 |
| `common_topics` | `jsonb` | default `'[]'::jsonb` | 高频主题 |
| `avg_session_duration` | `numeric(10,2)` |  | 平均会话时长 |
| `total_interactions` | `integer` | NOT NULL, default `0` | 总交互次数 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

索引建议：

- unique index：`user_id`

## `user_memory_record`

说明：

- 这是长期记忆真相源
- Milvus `user_memory` 只是检索副本

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `memory_id` | `varchar(64)` | PK | 长期记忆业务 ID |
| `user_id` | `uuid` | FK -> `app_user.user_id`, NOT NULL | 所属用户 |
| `session_id` | `uuid` | FK -> `chat_session.session_id` | 来源 session |
| `message_id` | `uuid` | FK -> `chat_message.message_id` | 来源 message |
| `memory_type` | `memory_type` | NOT NULL | 记忆类型 |
| `memory_text` | `text` | NOT NULL | 记忆正文 |
| `topic_tags` | `jsonb` | default `'[]'::jsonb` | 记忆主题标签 |
| `intent` | `varchar(128)` |  | 意图 |
| `sentiment` | `varchar(32)` |  | 情绪 |
| `importance_score` | `numeric(5,2)` |  | 重要度 |
| `confidence_score` | `numeric(5,2)` |  | 置信度 |
| `status` | `memory_status` | NOT NULL | 当前状态 |
| `valid_from` | `timestamptz` |  | 生效时间 |
| `valid_to` | `timestamptz` |  | 失效时间 |
| `last_accessed_at` | `timestamptz` |  | 最近命中时间 |
| `metadata` | `jsonb` | default `'{}'::jsonb` | 扩展信息 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |
| `deleted_at` | `timestamptz` |  | 软删除时间 |

索引建议：

- index：`user_id`
- index：`memory_type`
- index：`status`
- index：`valid_to`

## `memory_write_log`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `uuid` | PK, default `gen_random_uuid()` | 写入日志主键 |
| `user_id` | `uuid` | FK -> `app_user.user_id`, NOT NULL | 用户 |
| `session_id` | `uuid` | FK -> `chat_session.session_id` | 来源 session |
| `message_id` | `uuid` | FK -> `chat_message.message_id` | 来源消息 |
| `memory_id` | `varchar(64)` | FK -> `user_memory_record.memory_id` | 目标 memory |
| `write_reason` | `text` |  | 写入原因 |
| `status` | `varchar(32)` | NOT NULL | `queued` / `success` / `failed` / `skipped` |
| `error_message` | `text` |  | 错误信息 |
| `created_at` | `timestamptz` | NOT NULL, default `now()` | 创建时间 |

索引建议：

- index：`user_id`
- index：`memory_id`
- index：`status`

## 6.6 运行状态

## `source_runtime_state`

说明：

- 采集配置依然在 YAML
- 此表只负责记录运行态状态

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `source_id` | `varchar(64)` | PK | 来源 ID |
| `last_success_at` | `timestamptz` |  | 最近成功时间 |
| `last_error_at` | `timestamptz` |  | 最近失败时间 |
| `last_error_message` | `text` |  | 最近错误信息 |
| `last_etag` | `text` |  | 最近 ETag |
| `last_modified` | `text` |  | 最近 Last-Modified |
| `last_cursor` | `text` |  | 最近游标或分页状态 |
| `last_run_id` | `uuid` | FK -> `pipeline_run.run_id` | 最近处理 run |
| `updated_at` | `timestamptz` | NOT NULL, default `now()` | 更新时间 |

## 7. 首页读模型映射

当前前端首页需要的字段，可由以下 SQL 实体组合得到：

- `meta.generated_at` <- `published_run.published_at`
- `meta.total_topics` <- 当前 `published_run` 对应 `story_cluster_snapshot` 数量
- `meta.total_articles` <- 当前 `published_run` 对应成员总数
- `meta.sources_count` <- 当前 `published_run` 覆盖的 distinct `source_id` 数量
- `topics[].id` <- `story_key`
- `topics[].title` <- `story_cluster_snapshot.title`
- `topics[].summary` <- `story_cluster_snapshot.summary`
- `topics[].key_points` <- `story_cluster_snapshot.key_points`
- `topics[].tags` <- `story_cluster_snapshot.topic_tags`
- `topics[].category` <- `story_cluster_snapshot.category_id`
- `topics[].category_name` <- `story_cluster_snapshot.category_name`
- `topics[].image` <- `story_cluster_snapshot.cover_image_url`
- `topics[].published` <- `story_cluster_snapshot.newest_published_at`
- `topics[].article_count` <- `story_cluster_snapshot.article_count`

## 8. 建模注意事项

- `story_key` 必须是稳定主身份，不能用 `(run_id, story_id)` 代替
- `story_cluster_snapshot` 是发布视图，不是长期真相源
- Milvus 中的 `story_key` 只是过滤优化字段，不是 story 归属真相源
- 长期记忆必须先写 PostgreSQL，再异步写 Milvus
- 任何回答都应能通过 `message_citation -> retrieval_unit_ref -> document` 完整回溯
- `frontend/public/feed-data.json` 在迁移期仍可作为导出产物，但它不是 schema 真相源
