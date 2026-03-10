# API 契约

## 1. 设计原则

- API 是前端长期数据源
- 迁移期尽量保持与当前 `feed-data.json` 结构接近
- chat 回答必须支持 citation
- story 范围内聊天必须支持按快照回放

## 1.1 接口实现约定

- `backend/app/` 是 API 契约的唯一实现主目录
- `backend/server/` 只保留迁移期托管职责，不承接新增 API 逻辑
- `backend/test/` 承担 API 契约与回归测试

## 2. 认证接口

### `GET /api/v1/auth/login`

用途：

- 发起 Feishu 授权流程

### `GET /api/v1/auth/callback`

用途：

- 交换授权 code
- 校验 tenant 是否允许访问
- 创建或更新本地用户
- 建立登录态

### `GET /api/v1/auth/me`

用途：

- 返回当前登录用户信息

## 3. Feed 接口

### `GET /api/v1/feed/home`

用途：

- 提供首页数据
- 在迁移期保持与当前静态 feed 相近的结构

响应示例：

```json
{
  "meta": {
    "generated_at": "2026-03-09T00:00:00Z",
    "published_run_id": "run_20260309_1000",
    "total_topics": 0,
    "total_articles": 0,
    "sources_count": 0,
    "sources": []
  },
  "categories": [],
  "topics": [
    {
      "id": "story_key",
      "story_key": "story_key",
      "title": "string",
      "summary": "string",
      "key_points": [],
      "tags": [],
      "category": "string",
      "category_name": "string",
      "image": "string",
      "published": "2026-03-09T00:00:00Z",
      "sources": [],
      "article_count": 0
    }
  ]
}
```

### `GET /api/v1/topics/{story_key}`

用途：

- 返回某个 story 的详情数据
- 默认读取当前发布版本

可选 query 参数：

- `run_id`

响应内容应包含：

- story 基础信息
- 来源文档列表
- 成员文档信息
- 前端需要的引用或检索元信息

## 4. Chat 接口

### `GET /api/v1/chat/sessions`

用途：

- 列出用户的历史会话

### `POST /api/v1/chat/sessions`

用途：

- 创建新会话

请求示例：

```json
{
  "scope_type": "global",
  "scope_ref_key": null,
  "scope_snapshot_run_id": null,
  "title": "optional"
}
```

允许的 `scope_type`：

- `global`
- `story`
- `document`

### `GET /api/v1/chat/sessions/{session_id}/messages`

用途：

- 获取某个会话下的消息列表

### `POST /api/v1/chat/sessions/{session_id}/messages`

用途：

- 向会话发送一条消息

请求示例：

```json
{
  "message": "这对奢侈品牌外套方向意味着什么？",
  "scope_type": "story",
  "scope_ref_key": "story_abc123",
  "scope_snapshot_run_id": "run_20260309_1000"
}
```

响应必须包含：

- assistant answer
- citations
- source references
- session 元数据

后续可扩展流式输出，但不影响当前契约基线。

## 5. Citation 结构

任何基于检索生成的回答，都应能暴露类似以下结构：

```json
[
  {
    "doc_id": "doc_123",
    "unit_id": "unit_456",
    "source_url": "https://example.com/article",
    "title": "Original source title"
  }
]
```

## 6. 错误模型

API 应统一使用错误包结构：

```json
{
  "error": {
    "code": "access_denied",
    "message": "Tenant is not allowed",
    "details": {}
  }
}
```

常见错误场景：

- 未登录
- 无访问权限
- story 不存在
- snapshot 不存在
- session 不存在
- 上游模型失败
- 检索超时
