# 数据模型说明

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
- story 稳定身份与发布快照
- chat session 与消息
- citation 与工具调用日志
- 长期记忆主记录
- pipeline run 元数据

### Milvus

作为检索层存储：

- 内容检索单元
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

### 认证配置

定义：

- 允许访问的 Feishu tenant
- 应用凭证

## 3. Milvus Collections

### `content_unit`

用途：

- 全局 AI 检索
- story 内检索
- document 内检索

实体粒度：

- text chunk
- image asset
- video asset

关键字段：

- `unit_id`
- `doc_id`
- 可选冗余字段 `story_key`
- `unit_type`
- 文本或资产元数据
- dense vector
- sparse vector，用于 hybrid retrieval

规则：

- story 成员关系仍以 SQL 快照为准

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

### Story 发布层

- `story_identity`
- `story_cluster_snapshot`
- `story_cluster_member_snapshot`
- `pipeline_run`
- `published_run`

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

### 快照身份

- `(run_id, story_key)` 表示某次发布视图中的 story

### 为什么必须拆分

- 每日重聚类会影响 story 成员
- 增量更新会改变 story 内容
- 用户仍然需要稳定 topic URL 和可延续的讨论上下文

## 6. Story 连续性规则

新 cluster 复用旧 story 的顺序：

1. 代表文档命中
2. 成员文档至少重叠 2 篇，或重叠率 >= 30%
3. 标题/摘要向量相似度 >= 0.85 且标签重叠 >= 2
4. 否则新建 `story_key`

## 7. Memory 分层

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

## 8. 发布模型

### `pipeline_run`

用于记录：

- run 类型
- 时间窗口
- 状态
- 开始与结束时间
- 诊断信息

### `published_run`

用于记录：

- 当前线上生效的是哪个 run
- 谁或什么任务完成了发布
- 回滚目标历史

首页和 story API 默认都读取当前 `published_run`。
