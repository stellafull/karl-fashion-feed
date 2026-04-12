# Models

当前 ORM 以 digest runtime 为准，主链路是：

`article -> article_event_frame -> story -> digest`

## 模型分层

### 内容真相层

- [article.py](/home/czy/karl-fashion-feed/backend/app/models/article.py)
- [image.py](/home/czy/karl-fashion-feed/backend/app/models/image.py)
- [event_frame.py](/home/czy/karl-fashion-feed/backend/app/models/event_frame.py)

这里保存可回放事实：

- 原始来源 metadata
- canonical URL
- markdown 路径
- parse 状态
- event-frame 状态
- 来源图片 URL / caption / alt / context snippet

### 聚合层

- [story.py](/home/czy/karl-fashion-feed/backend/app/models/story.py)
- [digest.py](/home/czy/karl-fashion-feed/backend/app/models/digest.py)

职责：

- `story`：同一 business day 内的事件聚合单元
- `story_frame`：story 到 event frame 的有序映射
- `story_article`：story 到 article 的有序映射
- `story_facet`：story 的 facet 归类
- `digest`：公开阅读模型
- `digest_story`：digest 到 story 的有序映射
- `digest_article`：digest 到来源文章的有序映射

### 用户交互层

- [chat.py](/home/czy/karl-fashion-feed/backend/app/models/chat.py)
- [user.py](/home/czy/karl-fashion-feed/backend/app/models/user.py)

职责：

- 用户
- 会话
- 消息
- 附件
- 长时记忆

### 运行态

- [runtime.py](/home/czy/karl-fashion-feed/backend/app/models/runtime.py)

职责：

- `pipeline_run`：每日 digest runtime 执行状态
- `source_run_state`：单 source 在单 run 中的采集状态

## 关键约束

- `digest` 是唯一 public read model
- `story` 是内部聚合中间层，不直接替代 `digest`
- `pipeline_run` / `source_run_state` 只保存运行态，不保存业务真相
- Qdrant 中的 retrieval unit 不是业务真相，必须能回源到 `article` / `article_image`
- chat attachment 二进制文件落本地，数据库只存相对路径和 metadata

## 时间语义

- 运行 business day 基于 `Asia/Shanghai`
- ORM 内的时间戳字段大多存 naive UTC

## 当前需要警惕的历史口径

- 旧文档中出现的 `strict_story` 已经不是当前 ORM 合同
- 旧静态发布流程里的 publish snapshot 术语，不再描述当前 digest runtime
