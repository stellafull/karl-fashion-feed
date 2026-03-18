# Backend

## 目标

为 KARL FASHION FEED 提供一条稳定的后端生产链路：

- 多语言来源采集
- 中文翻译与摘要
- 过滤广告和无关内容
- 生成面向读者的不可变 `story`
- 为后续 RAG 和引用提供可追溯的文章真相源

## 核心不变量

- `article` 是事实真相源。
- `canonical_url` 归一化后是文章唯一键。
- 同一 `canonical_url` 再次出现时按重复处理，不做补写。
- `article` 主表只保存 metadata、摘要预览、Markdown 相对路径和主图引用。
- canonical Markdown 一篇 article 一个文件，按日期分层落到本地 `data/articles/`。
- Markdown 只保存纯文本正文，不保存图片占位和图片 caption。
- 图片资产单独写入 `article_image` 表，只保存 URL 和 metadata，不保存二进制。
- visual LLM 结果写回 `article_image`。
- `story` 是不可变读模型，`story_key` 使用 UUID。
- 每天北京时间 8 点只处理新增 `article`，生成新的 `story`。
- 旧 `story` 不更新、不合并、不重写。
- `article` / `article_image` 是检索与引用真相源。
- Milvus 只是检索副本。
- Redis 只负责缓存、锁、短期会话态。

## 每日 Pipeline

1. `NewsCollectionService`
   抓取来源文章，访问详情页解析 `canonical_url`，只生成 article seed。

2. `ArticleCollectionService`
   对增量 article seed 执行去重入库，写入 `article` 并标记 `parse_status=pending`。

3. `ArticleParseService`
   对 pending/failed `article` 抓详情页并解析，落纯文本 canonical Markdown 和 `article_image`。

4. `ArticleEnrichmentService`
   对每篇 `article` 执行一次 LLM enrichment，产出：
   `should_publish`、`reject_reason`、中文标题、中文摘要、标签、品牌、分类建议、全文翻译

5. Story embedding
   仅对 `should_publish=true` 的文章生成 story aggregation embedding。
   embedding 复用 `service/RAG/embedding_service.py` 中的 embedding 逻辑，输入建议使用中文标题、中文摘要、标签、品牌等结构化拼接文本。

6. `ArticleClusterService`
   基于 embedding 做语义聚类，输出 story 候选簇。

7. `ArticleClusterService` + LLM 复核
   对候选簇做复核，必要时拆分，再生成最终 `story` 标题、摘要、要点、标签和分类。

8. `StoryGenerationService` + `DailyPipelineService`
   生成最终 `story` 草稿，并写入 `story`、`story_article`、`pipeline_run`。

### 初始化与增量的 story 聚合规则

- 初始化 / bootstrap
  - 通过 `backend/app/scripts/bootstrap_story_pipeline.py` 按 `published_at` 日期逐日执行。
  - 每个自然日单独跑一次 pipeline run，而不是把历史全量 article 一次性落成同一轮 bootstrap run。
  - 目的是避免历史全量 article 在第一次建 story 时跨天混聚。
- 日常增量
  - 继续按新增 `article` 处理。
  - 判断新增范围以 `ingested_at` watermark 为准，而不是回看 `published_at`。

## `sources.yaml` 格式

当前只支持两类来源：

- `type: rss`
  必填字段：`name`、`feed_url`、`lang`、`category`
- `type: web`
  必填字段：`name`、`start_urls`、`allowed_domains`、`discovery`

统一约定：

- 所有来源都必须显式声明 `type`
- RSS 用 `feed_url`
- 网页源用 `start_urls + discovery`
- `max_articles` 控制单源单次最多抓取文章数
- `enabled: false` 的来源会被跳过

网页源的 `detail` 选择器是可选的。
未配置时，采集器会使用通用正文提取策略。

## 推荐数据模型

### `article`

- `article_id`
- `canonical_url`
- `source`
- `title_raw`
- `summary_raw`
- `markdown_rel_path`
- `hero_image_id`
- `lang`
- `published_at`
- `ingested_at`
- `parse_status`
- `parsed_at`
- `parse_error`
- `parse_attempts`
- `metadata_json`
- `should_publish`
- `reject_reason`
- `title_zh`
- `summary_zh`
- `tags_json`
- `brands_json`
- `category_candidates_json`
- `cluster_text`

### `article_image`

- `image_id`
- `article_id`
- `source_url`
- `normalized_url`
- `role`
- `position`
- `alt_text`
- `caption_raw`
- `credit_raw`
- `context_snippet`
- `visual_status`
- `observed_description`
- `ocr_text`
- `style_signals_json`
- `contextual_interpretation`

### `story`

- `story_key`
- `created_run_id`
- `title_zh`
- `summary_zh`
- `key_points_json`
- `tags_json`
- `category`
- `hero_image_url`
- `source_article_count`
- `created_at`


## 服务职责边界

- `NewsCollectionService`：发现文章 URL、获取 `canonical_url`、提供正文解析能力
- `ArticleCollectionService`：增量去重、article seed 入库
- `ArticleParseService`：正文解析、Markdown 落盘、`article_image` 入库
- `ArticleEnrichmentService`：翻译、总结、过滤、标签抽取、分类建议
- story embedding：复用 `service/RAG/embedding_service.py` 的 embedding 逻辑生成聚类向量
- `ArticleClusterService`：初始语义聚类和 LLM 拆分复核
- `StoryGenerationService`：cluster 到 story draft 的生成
- `DailyPipelineService`：编排当前已实现的日更 story pipeline
- `ImageAnalysisService`：图片分析能力，当前未接入日更 story pipeline

## 当前实现状态

### 已完成的 Daily Story Pipeline

- `NewsCollectionService`
- `ArticleCollectionService`
- `ArticleParseService`
- `ArticleEnrichmentService`
- `ArticleClusterService` 的初始聚类 + LLM split review
- `StoryGenerationService`
- `DailyPipelineService`
- `story`、`story_article`、`pipeline_run` 持久化

### 已有基础但未接入主链

- `ImageAnalysisService`

### 尚未完成

- Milvus text / image 副本同步
- query planner 与 intent-driven retrieval
- RAG agent
- 北京时间 8 点调度器

## Scripts

采集脚本和命令用法见 [backend/app/scripts/README.md](/root/karl-fashion-feed/backend/app/scripts/README.md)。
