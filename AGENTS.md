## 第一性原理

请使用第一性原理思考 你不能总是假设我非常清楚自己想要什么和该怎么得到。请保持审慎，从原始需求和问题出发，如果动机和目标不清晰，停下来和我讨论

## 代码规范

当你编写任何TypeScript代码时，强制使用
当你编写任何Python代码时, 强制使用


## 方案规范

当你给出修改或重构方案时必须符合以下规范:

- 不允许给出兼容性或补丁性方案
- 不允许过度设计, 保持最短路径实现且不能违反第一条要求
- 不允许自行给出我提供的需求意外的方案，例如一些helper和fallback方案，这可能导致业务逻辑偏移问题
- 必须确保方案的逻辑正确，必须经过全链路的逻辑验证
- failfast, let it crash, let bug exposure eariler


# KARL FASHION FEED

全球时尚资讯平台 + Agent。
目标用户是中国区同事，输入多语言来源，输出中文可读内容。

## 核心目标

- 每天北京时间 8 点执行一次采集。
- 当天新增 `article` 聚合为新的 `story`。
- `story` 只服务阅读，不回写历史，不做当前态刷新。
- `article` 构建RAG，提供user多入口query，自动带入story上下文
- image/text 混合RAG 提供以文搜图，以图搜图等服务
- future: Agent编排, 加入时尚风向调研

## Backend 核心约定

- `article` 是事实真相源。
- `canonical_url` 归一化后作为文章唯一去重键。
- 同一 `canonical_url` 二次抓到时直接视为重复，不做补写。
- `article` 主表只保存 metadata、摘要预览、Markdown 相对路径和主图引用。
- article 正文解析后写到本地 Markdown 文件，数据库只存相对路径。
- 图片资产单独入表
- `story` 是不可变聚合结果，`story_key` 使用 UUID。
- 每次定时任务只基于新增 `article` 生成新的 `story`。
- `article` 全量入库，再由 LLM 判断是否适合展示给读者。
- LLM enrichment 一次完成翻译、总结、过滤、标签抽取、分类建议。
- 仅 `should_publish=true` 的文章参与 embedding 和 story 聚类。
- 检索与引用以 `article` / `article_image`为准，Milvus 只是检索副本。
- Redis 仅用于缓存、锁和短期会话态，不保存核心业务真相。

## 建议目录

`backend/app/`

- `config/`：模型、embedding、第三方服务配置
- `core/`：数据库、Redis、安全、基础设施初始化
- `models/`：ORM 模型
- `router/`：FastAPI 路由
- `schemas/`：Pydantic DTO
- `service/`：业务逻辑
- `service/agents/`：Agent 编排
- `service/RAG/`：检索与向量能力
- `scripts/`：初始化和运维脚本
- `sources.yaml`：采集源配置
- `app_main.py`：FastAPI 入口

## 每日处理链路

1. 采集文章并做 `canonical_url` 归一化去重。
2. 将正文解析为 Markdown blocks，并将图片解析为独立 asset。
3. 对每篇文章执行 LLM enrichment：
   `should_publish`、中文标题、中文摘要、标签、品牌、分类建议。
4. 对可发布文章生成聚类文本和 embedding。
5. 基于语义相似度做初始聚类。
6. 用 LLM 复核聚类结果，必要时拆分，再生成最终 `story` 内容。
7. 持久化 `story`。

## 关键实体

- `article`：原文事实、来源、发布时间、LLM enrichment 结果
- `story`：面向读者的不可变中文聚合内容
- `article_image`：图片 URL、位置、caption、visual LLM 结果
- `pipeline_run`：采集、聚类、索引任务执行记录

## 文档入口

- Backend 详细约定见 `backend/README.md`
