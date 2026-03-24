# Task 4: 本地鉴权 + 异步 Chat Domain V1

## 概述

实现本地账号鉴权和异步聊天系统：
- 本地账号登录（root/root），不开放注册
- JWT access token 鉴权
- 异步消息处理：用户消息入库 → assistant 占位消息排队 → worker 消费 → 前端轮询结果
- 支持文字、图片、图文混合输入
- 对话上下文：compact_context + 最近5条历史 + 当前消息/附件 + 用户记忆

## 数据库模型

### User (backend/app/models/user.py)
- user_id (UUID PK)
- login_name (unique)
- display_name
- email (nullable, unique)
- password_hash
- auth_source ('local'|'sso')
- sso_provider, sso_subject (SSO 预留)
- is_active, is_admin
- last_login_at, created_at, updated_at

### ChatSession (backend/app/models/chat.py)
- chat_session_id (UUID PK)
- user_id (FK)
- title
- compact_context (nullable)
- created_at, updated_at

### ChatMessage (backend/app/models/chat.py)
- chat_message_id (UUID PK)
- chat_session_id (FK)
- role ('user'|'assistant')
- content_text (NOT NULL, default='')
- status ('done'|'queued'|'running'|'failed')
- reply_to_message_id (nullable FK)
- response_json (nullable)
- error_message (nullable)
- created_at, started_at, completed_at

### ChatAttachment (backend/app/models/chat.py)
- chat_attachment_id (UUID PK)
- chat_message_id (FK)
- attachment_type ('image')
- mime_type
- original_filename
- storage_rel_path
- size_bytes
- created_at

### LongTermMemory (backend/app/models/chat.py)
- memory_id (UUID PK)
- user_id (FK)
- memory_type
- memory_key
- memory_value
- source ('manual')
- created_at, updated_at
- 唯一约束：(user_id, memory_type, memory_key)

## API 端点

### 认证
- `POST /api/v1/auth/token` - 登录（OAuth2PasswordRequestForm）
- `GET /api/v1/auth/me` - 当前用户信息

### 聊天
- `POST /api/v1/chat/messages` - 发送消息（multipart form: chat_session_id?, content_text?, image?）
- `GET /api/v1/chat/messages/{message_id}` - 轮询消息状态
- `GET /api/v1/chat/sessions` - 会话列表
- `GET /api/v1/chat/sessions/{session_id}` - 会话详情
- `GET /api/v1/chat/sessions/{session_id}/messages` - 会话消息列表
- `GET /api/v1/chat/attachments/{attachment_id}/content` - 附件内容

### 记忆
- `GET /api/v1/memories` - 记忆列表
- `POST /api/v1/memories` - 创建/更新记忆（upsert）
- `PATCH /api/v1/memories/{memory_id}` - 更新记忆
- `DELETE /api/v1/memories/{memory_id}` - 删除记忆

## 核心流程

### 消息发送流程
1. 用户调用 `POST /chat/messages`（可选 chat_session_id、content_text、image）
2. 校验：文本和图片不能同时为空，最多1张图
3. 事务内：
   - 若无 session，创建新 session（title 从 content_text 前30字符或"图片对话"）
   - 写入 user message (status='done')
   - 若有图片，保存到 CHAT_ATTACHMENT_ROOT，写入 attachment
   - 写入 assistant 占位 message (status='queued', reply_to_message_id 指向 user message)
4. 返回 chat_session_id, user_message_id, assistant_message_id

### Worker 处理流程
1. 查询 role='assistant' AND status='queued'
2. 使用 SELECT FOR UPDATE SKIP LOCKED claim 一条消息
3. 更新 status='running', started_at=now
4. 加载上下文：
   - session.compact_context
   - 最近5条 status='done' 且早于当前轮 user message 的历史消息
   - 当前轮 user message
   - 当前用户的 long_term_memory
   - 当前 user message 的图片附件
5. 调用 RagAnswerService.answer(conversation_compact, recent_messages, user_memories)
6. 成功：回写 content_text, response_json, status='done', completed_at
7. 失败：回写 status='failed', error_message, completed_at
8. 若已完成消息 > 5，重算 compact_context

## 实现文件清单

### 新增文件
1. `backend/app/models/user.py` - User 模型
2. `backend/app/models/chat.py` - ChatSession, ChatMessage, ChatAttachment, LongTermMemory
3. `backend/app/models/bootstrap.py` - ensure_auth_chat_schema()
4. `backend/app/config/auth_config.py` - AuthSettings (pydantic-settings)
5. `backend/app/core/security.py` - PasswordHasher, JWTManager
6. `backend/app/core/auth_dependencies.py` - get_current_user, get_current_admin_user
7. `backend/app/schemas/auth.py` - TokenResponse, UserProfile
8. `backend/app/schemas/chat.py` - CreateMessageResponse, MessageResponse, SessionResponse, AttachmentResponse
9. `backend/app/schemas/memory.py` - CreateMemoryRequest, MemoryResponse
10. `backend/app/router/auth_router.py` - 认证端点
11. `backend/app/router/chat_router.py` - 聊天端点
12. `backend/app/router/memory_router.py` - 记忆端点
13. `backend/app/service/chat_worker_service.py` - Worker 逻辑
14. `backend/app/scripts/init_root_user.py` - 初始化 root 用户
15. `backend/app/scripts/run_chat_worker.py` - Worker 进程入口

### 修改文件
1. `backend/app/models/__init__.py` - 导出新模型
2. `backend/app/router/__init__.py` - 导出新 routers
3. `backend/app/app_main.py` - lifespan 钩子，注册 routers，CORS 配置
4. `backend/app/service/RAG/rag_answer_service.py` - 扩展 answer() 支持对话上下文
5. `backend/pyproject.toml` - 添加 python-jose[cryptography]

## 环境变量

```bash
AUTH_JWT_SECRET=<生成安全随机密钥>
AUTH_JWT_ALGORITHM=HS256
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES=60
CORS_ALLOWED_ORIGINS=http://localhost:3000
CHAT_ATTACHMENT_ROOT=/root/karl-fashion-feed/backend/data/chat_attachments
```

## 测试要点

1. **认证**：root/root 登录成功，错误密码 401，无 JWT 401，缺 secret 启动失败
2. **消息入队**：自动建 session，纯图片消息，文本+图片都空 422，多图 422，非本人 403
3. **Worker**：claim queued，上下文正确，失败处理，compact_context 更新
4. **记忆**：upsert，唯一约束，只能 CRUD 自己的
5. **附件**：image/* 校验，落盘，content_url，所有权校验
6. **轮询**：观察 queued → running → done/failed

## 核心假设

- 本期只做本地账号，SSO 只预留字段
- JWT 仅 access token，不做 refresh/logout
- Redis 不保存 auth/chat 核心真相
- chat_message.status 就是队列真相
- long_term_memory 手工维护
- worker 使用 SELECT FOR UPDATE SKIP LOCKED
