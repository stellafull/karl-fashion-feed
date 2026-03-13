# KARL FASHION FEED

全球时尚资讯平台 + Agent。
目标用户是中国区同事，输入多语言来源，输出中文可读内容。

## 核心目标

- 每天北京时间 8 点执行一次采集。
- 当天新增 `article` 聚合为新的 `story`。
- `story` 只服务阅读，不回写历史，不做当前态刷新。

## Backend 核心约定

- `article` 是事实真相源。
- `canonical_url` 归一化后作为文章唯一去重键。
- 同一 `canonical_url` 二次抓到时直接视为重复，不做补写。
- `article` 主表只保存 metadata、摘要预览、Markdown 相对路径和主图引用。
- article 正文解析后写到本地 Markdown 文件，数据库只存相对路径。
- 图片资产单独入表，Markdown 中只保留 `[image:<image_id>]` 占位符。
- `story` 是不可变聚合结果，`story_key` 使用 UUID。
- 每次定时任务只基于新增 `article` 生成新的 `story`。
- `article` 全量入库，再由 LLM 判断是否适合展示给读者。
- LLM enrichment 一次完成翻译、总结、过滤、标签抽取、分类建议。
- 仅 `should_publish=true` 的文章参与 embedding 和 story 聚类。
- 检索与引用以 `article` / `retrieval_unit_ref` 为准，Milvus 只是检索副本。
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
7. 持久化 `story`、`story_article`、`retrieval_unit_ref`。

## 关键实体

- `article`：原文事实、来源、发布时间、LLM enrichment 结果
- `story`：面向读者的不可变中文聚合内容
- `story_article`：`story` 与 `article` 的不可变映射
- `article_image`：图片 URL、位置、caption、visual LLM 结果
- `retrieval_unit_ref`：文章切块与检索索引桥接
- `pipeline_run`：采集、聚类、索引任务执行记录

## 文档入口

- Backend 详细约定见 `backend/README.md`
