# 产品与功能总览

## 产品定位

KARL Fashion Feed 面向中国区内部同事，目标是把多语言时尚资讯整理成中文可读、可追问、可深挖的工作台。

它不是简单的 RSS 阅读器。当前实现已经包含：

- 每日 digest 聚合阅读
- 基于 digest / article 的上下文问答
- 图文混合检索
- 带阶段事件的 deep research

## 当前用户可见功能

## 1. 登录

- 本地账号密码登录
- 登录后前端把 JWT 存在浏览器 `localStorage`
- 当前代码没有接 Feishu / SSO 主路径

## 2. Discover

Discover 页是默认阅读入口，数据来自 `GET /api/v1/digests/feed`。

支持：

- 按 facet 浏览 digest
- 按来源筛选
- 按时间 / 覆盖文章数排序
- 查看 digest 主图、标题、摘要、来源覆盖度

## 3. Digest 详情

Story 页实际读取的是 digest detail。

支持：

- 查看中文标题、摘要、正文 markdown
- 查看来源列表和原始链接
- 直接从当前 digest 发起普通 chat
- 直接从当前 digest 发起 deep research
- 上传图片辅助提问

## 4. Chat

Chat 页支持：

- 新建会话
- 历史会话恢复
- 普通 RAG 问答
- 图片附件上传
- 流式回答
- 手动中断运行中的 assistant

## 5. Deep Research

Deep Research 与普通 chat 共用 session 持久化层，但运行时不同。

支持：

- graph 执行阶段事件流
- clarification thread 复用
- 从普通 digest 上下文切入研究

## 6. Memory

后端提供长时记忆 CRUD 接口，当前主要是能力已具备，前端未做完整独立工作台。

## 7. RAG 与图文检索

系统当前支持：

- 文本问答
- 图像辅助问答
- 以图找相似视觉证据
- 视觉查询证据不足时，追加外部 web/image 搜索

## 当前后端能力边界

### 已落地

- digest feed / detail
- chat / deep research
- memory CRUD
- article/image Qdrant upsert
- daily runtime 协调

### 尚未统一收口

- “北京时间 8 点运行”还是目标，代码当前门槛是 Sydney 9 点
- 用户态 memory 的完整前端管理页面还不完整
- 根目录 Node build 流程仍有历史残留

## 重要术语

- `article`：事实真相源
- `article_image`：文章图片事实层
- `article_event_frame`：结构化事件片段
- `story`：内部聚合层
- `digest`：公开阅读模型
- `chat session`：问答会话
- `deep research thread`：研究线程，挂在 chat session 内

## 不再适用的旧描述

以下说法已经不适用于当前项目：

- “前端直接读取 feed-data.json”
- “Feishu 是唯一登录入口”
- “Milvus 是当前向量库”
- “旧 story 页就是唯一 public model”
