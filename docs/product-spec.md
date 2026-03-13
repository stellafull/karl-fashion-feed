# Fashion Feed 产品规格说明

## 1. 文档目的

本文档是 Fashion Feed 当前阶段的主产品文档，面向以下读者：

- 项目 owner 与 mentor，用于对齐需求、范围、架构和推进节奏
- 设计、前端、后端与数据工程协作者，用于统一产品语言和交付目标
- 后续工程师或智能代理，用于理解“当前是什么”和“v1 要做成什么”

本文档聚焦产品需求与能力边界，不替代以下专项文档：

- 架构细节见 `docs/architecture.md`
- 数据模型见 `docs/data-model.md`
- API 契约见 `docs/api-contract.md`
- 后端实现边界见 `backend/product.md` 与 `backend/schema.md`
- 项目阶段计划见 `plan.md`

## 2. 执行摘要

Fashion Feed 正在从一个由 `feed-data.json` 驱动的时尚资讯聚合前端，升级为一个面向企业内部团队的时尚情报系统。

当前仓库已经证明了两个核心能力：

- 可以稳定抓取全球时尚媒体内容，并通过 AI 生成中文聚合摘要
- 可以用接近编辑产品的前端体验，把多源资讯组织成可浏览的话题流

但当前产品仍然停留在“静态阅读入口”阶段，还不具备企业内部产品需要的关键能力：

- 没有正式登录与组织访问控制
- 没有持久化的 story 身份与当前态聚合模型
- 没有可追溯的 AI 问答、session 历史和长期记忆
- 没有以后端 API 作为稳定真相源

因此，v1 的目标不是简单补一个后端，而是把 Fashion Feed 定义为一个可持续使用、可追溯、可回放、可扩展的内部情报产品。

## 3. 当前状态

## 3.1 已有能力

截至 `2026-03-09`，仓库中已明确存在以下能力：

- 前端主路径仍是首页信息流，路由只有 `/` 和 `404`
- 首页从 `frontend/public/feed-data.json` 读取数据，不依赖正式后端 API
- 首页支持分类切换、来源筛选、排序、精选 topic 和 topic detail overlay
- topic detail 已能展示聚合摘要、要点、标签、原始来源和外链跳转
- Python 脚本已能从 `backend/scripts/sources.yaml` 读取信源并生成聚合结果
- FastAPI 目前只有基础应用骨架和 `/healthz`

现有静态 feed 样例显示：

- 生成时间为 `2026-03-05T17:47:41.916565`
- 共有 `340` 个 topics、`409` 篇 articles、`20` 个前端展示来源
- 首页分类当前为 4 类：`全部`、`秀场/系列`、`街拍/造型`、`趋势总结`、`品牌/市场`

## 3.2 当前缺口

当前代码仓库尚未完成以下产品能力：

- Feishu OAuth2 接入
- tenant allowlist 与登录审计能力
- 以 PostgreSQL 为真相源的文档、story、session、memory 持久化
- 以 Milvus 为检索层的 `content_text_unit`、`content_image_unit` 与 `user_memory`
- story 内 AI 问答与全局 AI sidebar
- 历史 session 恢复
- 当前态 story 聚合刷新与恢复
- 前端从静态 JSON 切换到 API

## 3.3 现状与目标的关系

本项目不是从零开始做新产品，而是在已有“静态聚合阅读产品”基础上，保留首页信息流体验，再补齐登录、发布、检索和 AI 能力。

这意味着：

- 首页信息流仍然是产品主入口，不改成聊天优先
- 现有前端体验要尽量延续，不能因为后端重构牺牲可读性
- 所有新增 AI 能力都必须围绕 story 阅读与证据追溯展开

## 4. 问题定义与产品机会

目标用户今天已经能从大量媒体中获取时尚资讯，但仍面临四类问题：

- 信息过散：同一事件分散在多个来源，人工比对成本高
- 信息过快：更新节奏快，团队很难稳定追踪同一个话题的演变
- 信息过浅：只看摘要无法支持进一步分析、比较和决策
- 信息不可积累：没有历史 session、长期记忆和组织内可复用的分析链路

Fashion Feed 的产品机会在于同时解决“看新闻”和“做判断”两件事：

- 用信息流降低发现成本
- 用 story 聚合降低归因成本
- 用 citation 降低 AI 幻觉风险
- 用 session 和记忆降低重复提问成本

## 5. 产品定位与原则

## 5.1 产品定位

Fashion Feed 是面向企业内部团队的时尚情报产品。它不是公开内容社区，也不是通用聊天机器人，而是服务于内部研究、编辑、商品和战略判断的工作台。

## 5.2 产品原则

- 首页优先：用户进入系统后首先应该浏览当下最重要的情报，而不是先聊天
- story 优先：系统要尽量把同一事件的多篇报道组织为可持续追踪的 story
- 证据优先：AI 回答必须尽量基于已知文档和 citation，而不是只给主观结论
- 连续性优先：用户讨论的是稳定的 story，不是某次聚合生成的临时结果
- 真相源清晰：SQL、Milvus、YAML、静态 JSON 的职责必须明确，避免状态漂移
- 迁移可控：在 API 完全对齐前，不能破坏现有前端主路径

## 6. 目标用户与 JTBD

## 6.1 品牌战略团队

他们需要快速理解品牌动作、设计方向、市场信号和竞争格局。

核心任务：

- 今天有哪些值得关注的品牌动作
- 某个品牌方向意味着什么
- 某个趋势在不同来源里是否得到印证

## 6.2 内容与编辑团队

他们需要追踪热点、汇总背景和快速形成选题判断。

核心任务：

- 哪些话题值得跟进
- 同一 story 的不同来源分别强调了什么
- 某个热点过去几天如何演化

## 6.3 研究与情报团队

他们需要高质量检索、追溯和可复用的分析上下文。

核心任务：

- 针对 story 继续追问，拿到可引用的回答
- 从历史 session 恢复上下文
- 在长期使用中形成用户记忆增益

## 6.4 商品与产品团队

他们需要从时尚资讯中提炼与品类、款式、消费信号相关的结论。

核心任务：

- 某类设计语言是否在升温
- 某品牌动作对商品方向有什么启发
- 哪些资讯属于噪音，哪些值得转化为决策输入

## 6.5 管理层

他们需要高压缩、高可信度的摘要与延伸分析。

核心任务：

- 快速掌握行业变化
- 在少量时间内看到多来源证据支撑的结论
- 追问后能回到原始来源验证

## 7. v1 产品目标与非目标

## 7.1 v1 必须实现的目标

- 在后续阶段接入 Feishu 登录，并在服务端执行 tenant allowlist 校验
- 提供稳定的首页 Feed API，逐步替代静态 `feed-data.json`
- 提供按 `story_key` 访问的 story 详情
- 支持 story 内问答，并绑定稳定 `story_key`
- 支持全局 AI sidebar、session 新建与历史恢复
- 支持 citation，从回答追溯到 unit、document 和 source
- 支持长期记忆主记录与可检索副本
- 支持当前态 story 聚合刷新与恢复

## 7.2 v1 非目标

- 不新增 `content_source` SQL 主表
- 不为 story 引入额外的 snapshot 主表
- 不把原始聊天记录直接镜像到 Milvus 充当唯一记忆
- 不把大体积 HTML 或媒体文件本体写入 PostgreSQL
- 不让前端 URL 或会话恢复依赖临时聚合 ID
- 不把产品改造成聊天优先的交互形态

## 8. 核心使用场景

## 8.1 登录进入系统

后续阶段用户通过 Feishu 登录进入产品。服务端验证 tenant 是否在 allowlist 中，并记录本次登录尝试。

用户价值：

- 产品从“任何人都能读的静态页”升级为“可控的内部工具”

关键要求：

- 登录判断必须在服务端完成
- 所有成功和失败尝试都可审计

## 8.2 浏览首页信息流

用户进入系统后首先看到首页 Feed，包括精选 story、分类导航、来源筛选、排序和话题列表。

用户价值：

- 在不主动提问的情况下快速发现值得关注的内容

关键要求：

- 首页继续保持接近现有前端的浏览节奏
- API 结构在迁移期尽量与当前静态 feed 一致
- 排序、来源筛选和分类切换不能因切 API 退化

## 8.3 打开 story 详情

用户打开某个 story 后，查看聚合摘要、关键要点、标签、来源列表和成员文档信息。

用户价值：

- 在一个稳定对象上理解同一事件的多来源视角

关键要求：

- 对外稳定标识是 `story_key`
- 默认读取当前 `story`
- 详情页展示当前成员关系与聚合结果

## 8.4 在 story 内继续提问

用户在 story 详情上下文中继续提问，系统先读取 story 上下文，再补充相关检索，返回带 citation 的回答。

用户价值：

- 用户不需要重新描述背景，就能基于当前话题做深入分析

关键要求：

- 会话必须记录 `scope_type=story`
- 必须携带 `story_key`
- 回答必须带 citation

## 8.5 使用全局 AI sidebar

用户在首页或其他页面打开 AI sidebar，进行跨库问答、对比、总结和延伸分析。

用户价值：

- 不离开浏览流也能进入分析状态

关键要求：

- 支持新建 session
- 支持恢复历史 session
- 支持全局检索与 memory 检索

## 8.6 恢复历史 session

用户返回系统后，可以查看过去的对话并继续追问。

用户价值：

- 产品从一次性回答工具升级为持续使用的工作台

关键要求：

- session 和 message 保存在 PostgreSQL
- story 范围内会话要能绑定稳定 `story_key`

## 8.7 验证回答来源

用户需要知道答案来自哪些文档、哪些检索单元、哪些原始来源。

用户价值：

- AI 回答可以被验证、引用和纠错

关键要求：

- citation 必须可回溯到 `answer -> unit -> document -> source`

## 9. 模块级产品需求

## 9.1 认证模块

产品目标：

- 只允许指定企业组织访问
- 为后续 session、memory 和行为审计提供稳定用户身份

需求约束：

- 登录能力接入后只支持 Feishu 登录
- 所有登录尝试写入 `auth_login_event`
- tenant allowlist 必须是服务端约束

## 9.2 首页 Feed 模块

产品目标：

- 成为用户每天高频进入的主入口
- 用 story 聚合替代散乱 article 列表

需求约束：

- 迁移期接口结构尽量贴近现有 `feed-data.json`
- 首页默认读取当前 `story` 聚合状态
- 前端不依赖临时聚合 ID

## 9.3 Story 详情模块

产品目标：

- 让用户在一个稳定对象上读懂一个事件

需求约束：

- `story_key` 是稳定身份
- story 成员关系以 SQL 主表为准，不以向量库冗余字段为准

## 9.4 Chat 模块

产品目标：

- 把问答从单轮调用升级为可持续 session

需求约束：

- 至少支持 `global`、`story`、`document` 三种 scope
- story 范围消息必须记录 `story_key`
- assistant 回答必须返回 citations 和 session 元数据

## 9.5 Citation 模块

产品目标：

- 让用户信任并复核回答

需求约束：

- citation 结构稳定
- citation 存储可持久化
- 前端能拿到足够字段展示原始来源链接

## 9.6 Memory 模块

产品目标：

- 在不牺牲可审计性的前提下提升问答相关性

需求约束：

- 短期记忆放 PostgreSQL session/message
- 长期记忆必须同时有 PostgreSQL 主记录和 Milvus 检索副本
- 不能把 Milvus 当作唯一用户记忆源

## 9.7 内容生产与聚合模块

产品目标：

- 让用户看到的是经过校验、连续可追踪的当前态 story

需求约束：

- `sources.yaml` 继续作为采集配置真相源
- 当前阶段直接维护 `story` / `story_article` 当前态
- 重聚类时尽量复用既有 `story_key`

## 10. 核心对象与术语

- `story_key`：稳定 story 身份，对外 URL、session、引用都依赖它
- `story`：当前 story 聚合主记录
- `story_article`：当前 story 成员关系
- `document`：原始文档主记录
- `document_asset`：文档相关图片、视频等资产引用
- `content_text_unit`：文本检索单元，粒度是 chunk
- `content_image_unit`：图片检索单元，粒度是 image asset
- `chat_session`：一次连续对话的容器
- `chat_message`：session 下的消息记录
- `message_citation`：回答与文档证据之间的引用关系
- `user_memory_record`：长期记忆主记录

术语关系必须稳定：

- 用户讨论的对象是稳定 `story_key`，不是临时聚合结果
- 用户恢复的是稳定 `story_key` 下的当前上下文，不是临时前端状态
- 检索用的是 units，不是直接用 article 作为唯一粒度

## 11. 系统架构与数据边界

## 11.1 目标运行拓扑

目标形态如下：

- `frontend/`：前端产品界面
- `backend/app/`：FastAPI 服务、领域服务、检索编排
- `PostgreSQL`：业务真相源
- `Milvus`：内容检索与长期记忆检索副本
- `Redis + Celery`：异步任务与生产链路

## 11.2 真相源边界

- `sources.yaml`：采集配置真相源
- PostgreSQL：用户、登录事件、文档、story、session、citation、memory 和运行态状态真相源
- Milvus：`content_text_unit`、`content_image_unit`、`user_memory`、`user_profile_memory` 的检索副本
- `feed-data.json`：迁移期前端兜底产物，不是长期真相源

## 11.3 Story 边界

- `story_key` 是稳定标识
- `story` 保存当前聚合主记录
- `story_article` 保存当前成员关系

## 11.4 Memory 边界

- 短期记忆是 session/message
- 长期记忆必须有 SQL 主表
- Milvus 中的用户记忆只是可检索副本

## 11.5 Retrieval 边界

- 内容检索核心由 `content_text_unit` 与 `content_image_unit` 组成
- 文本与图片分 collection 存储，但查询时统一由 query intent 决定权重与配额
- story 成员关系以 SQL 主表为准，Milvus 中的 story 字段只做冗余过滤
- v1 的 asset retrieval 只承诺 image asset，不把 video 纳入当前实现范围

## 12. 关键链路

## 12.1 内容生产链路

1. 从 `sources.yaml` 读取采集配置。
2. 拉取 RSS 或 crawl 内容。
3. 标准化、去重、摘要、分类和基础清洗。
4. 写入 `document` 与 `document_asset`。
5. 生成 text retrieval units，并异步生成 image retrieval units 后写入 Milvus。
6. 刷新当前 `story` 聚合结果。
7. 执行校验后刷新 `story` 与 `story_article`。
8. 在迁移期继续产出静态 feed 兜底结果。

## 12.2 首页读取链路

1. 前端请求 `GET /api/v1/feed/home`。
2. 后端读取当前 `story`。
3. 组装首页所需的 meta、categories 和 topics。
4. 前端继续执行分类、来源筛选和排序交互。

## 12.3 Story AI 链路

1. 用户打开某个 story。
2. 用户发起 story 范围提问。
3. 后端读取 story 上下文。
4. 后端按 query intent 同时补充 text/image 检索结果。
5. 模型返回带 citation 的回答。
6. 系统写入 `chat_message`、`message_citation` 与 memory 记录。

## 12.4 全局 AI 链路

1. 用户打开 AI sidebar。
2. 用户新建或恢复 session。
3. 后端执行全局检索和 memory 检索。
4. 模型返回回答与 citation。
5. session 和消息持久化到 PostgreSQL。

## 13. 对外接口基线

产品层面需要稳定的首批接口如下：

- `GET /api/v1/auth/login`
- `GET /api/v1/auth/callback`
- `GET /api/v1/auth/me`
- `GET /api/v1/feed/home`
- `GET /api/v1/topics/{story_key}`
- `GET /api/v1/chat/sessions`
- `POST /api/v1/chat/sessions`
- `GET /api/v1/chat/sessions/{session_id}/messages`
- `POST /api/v1/chat/sessions/{session_id}/messages`

产品文档在这里定义“为什么需要这些接口”和“它们支持什么用户能力”；字段级返回结构以 `docs/api-contract.md` 为准。

## 14. 成功标准

v1 完成后，至少要满足以下产品结果：

- 认证接入完成后，用户可以通过 Feishu 正常登录，且访问控制可审计
- 首页仍然是稳定、清晰、快速的信息流主入口
- story 详情足以支撑快速阅读与继续分析
- AI 回答具备上下文、citation 和回溯能力
- 用户能够恢复历史 session
- 长期记忆可以提升后续问答相关性
- story 在跨日聚合更新时仍保持连续身份
- 聚合失败时可通过数据库备份或重新聚合恢复

## 15. 交付阶段

## 15.1 Phase 0：文档与 schema 冻结

目标：

- 统一术语
- 冻结职责边界
- 明确 v1 范围与非目标

## 15.2 Phase 1：后端骨架与认证

目标：

- 落地 FastAPI 主目录
- 建立基础 PostgreSQL 模型
- 预留 Feishu 登录与租户校验接口

## 15.3 Phase 2：文档入库与检索单元

目标：

- 文档进入 PostgreSQL
- 生成 text/image retrieval units
- 建立 `content_text_unit` 与 `content_image_unit` 检索能力

## 15.4 Phase 3：Story 聚合层

目标：

- 建立 story 稳定身份
- 建立 `story` / `story_article` 当前态聚合
- 提供 API 化首页数据

## 15.5 Phase 4：AI、session 与 memory

目标：

- 建立 chat session/message
- 建立 citation 持久化
- 建立长期记忆主记录与检索副本

## 15.6 Phase 5：前端切流

目标：

- 首页由静态 JSON 切到 API
- 接入 AI sidebar
- 接入 story 底部上下文问答入口

## 16. 核心风险与待 mentor 对齐问题

## 16.1 核心风险

- story continuity 不稳定，导致用户讨论对象碎裂
- 聚类误判会直接降低首页质量与 AI 问答可信度
- citation 如果不能稳定追溯，会削弱产品信任
- memory 如果只有检索副本，没有 SQL 主记录，会导致删除、修正和审计失控
- API 切流如果不能保持现有首页体验，会损害产品主路径

## 16.2 待 mentor 对齐的问题

- v1 首页是否仍坚持“阅读优先”，不让 AI sidebar 占主导位置
- story 详情页在 v1 是否需要展示成员文档级信息，还是先以聚合摘要和来源列表为主
- 长期记忆在 v1 的写入策略应更保守还是更积极
- 当前态聚合是否需要人工审批，还是完全自动刷新 `story`
- mentor 更关注的验收口径是“用户体验稳定”，还是“后端数据真相源完成切换”，还是两者同时满足

## 17. 文档使用方式

建议把本文档作为评审主线：

- 先确认产品问题、目标用户和 v1 范围
- 再确认 story、memory、retrieval 和当前态聚合模型等关键架构边界
- 最后按交付阶段讨论优先级、风险和资源安排

如果评审中对某个点需要下钻，再分别进入 `docs/architecture.md`、`docs/data-model.md`、`docs/api-contract.md` 和 `backend/schema.md`。
